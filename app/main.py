"""TriageBot: app FastAPI y endpoints HTTP (SPEC §5).

Aquí no hay SQL (va en db.py) ni llamadas a ninguna API de IA (van SOLO en classifier.py).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import classifier, db
from app.models import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    MAX_ASSIGNEE_LENGTH,
    Category,
    Classification,
    Priority,
    Status,
    Ticket,
    TicketCreate,
    TicketUpdate,
)

# Se importa el MÓDULO classifier (no la función): los tests sustituyen
# app.classifier.classify_ticket y la llamada debe pasar por el módulo para verlo.

load_dotenv()

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Textos en castellano para el tablero. Los valores internos (bug, P1, open...) no cambian:
# son los de la API y los tests; aquí solo se traducen para mostrarlos.
CATEGORY_LABELS = {
    "bug": "Error",
    "feature_request": "Mejora",
    "question": "Duda",
    "urgent": "Urgente",
}
PRIORITY_LABELS = {"P1": "P1 · Alta", "P2": "P2 · Media", "P3": "P3 · Baja"}
# "closed" se muestra como "Resuelto" (SPEC §4.2): el valor de la API no cambia.
STATUS_LABELS = {"open": "Abierto", "in_progress": "En curso", "closed": "Resuelto"}
templates.env.globals.update(
    CATEGORY_LABELS=CATEGORY_LABELS,
    PRIORITY_LABELS=PRIORITY_LABELS,
    STATUS_LABELS=STATUS_LABELS,
)

_ALLOWED_FILTER_VALUES = {
    "category": ALLOWED_CATEGORIES,
    "priority": ALLOWED_PRIORITIES,
    "status": ALLOWED_STATUSES,
}


def _format_date(value: str) -> str:
    """'2026-06-29T09:30:00Z' -> '29/06/2026 09:30' (hora UTC, la que guarda la BD)."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return value


def _format_day(value: str) -> str:
    """'2026-06-29T23:59:59Z' -> '29/06/2026' (para la fecha límite)."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return value


templates.env.filters["fecha"] = _format_date
templates.env.filters["dia"] = _format_day


def _clean_assignee(assignee: str) -> str:
    """Filtro por responsable: texto libre, espacios normalizados. Vacío = sin filtro."""
    clean = " ".join(assignee.split())
    if len(clean) > MAX_ASSIGNEE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"El responsable no puede superar {MAX_ASSIGNEE_LENGTH} caracteres",
        )
    return clean


def _read_filters(
    category: str, priority: str, status: str, assignee: str = ""
) -> dict[str, str]:
    """Filtros del tablero. Un valor vacío ('Todos') significa 'sin filtro', no es un error."""
    raw = {"category": category.strip(), "priority": priority.strip(), "status": status.strip()}
    for name, value in raw.items():
        if value and value not in _ALLOWED_FILTER_VALUES[name]:
            raise HTTPException(status_code=422, detail=f"Valor no válido para '{name}': {value}")
    raw["assignee"] = _clean_assignee(assignee)
    return {name: value for name, value in raw.items() if value}


def _read_solo_vencidos(vencidos: str) -> bool:
    """Casilla "Solo vencidos": envía vencidos=1 si está marcada y nada si no."""
    return vencidos.strip().lower() in {"1", "true", "on"}


def _as_tickets(rows: list[dict]) -> list[Ticket]:
    """Filas de la BD -> modelos Ticket, que añaden due_at y overdue (SPEC §4.1)."""
    return [Ticket.model_validate(row) for row in rows]


def _board_context(filtros: dict[str, str], solo_vencidos: bool) -> dict:
    """Datos del tablero: tickets filtrados + total de vencidos de toda la base."""
    tickets = _as_tickets(db.list_tickets(**filtros))
    if solo_vencidos:
        tickets = [ticket for ticket in tickets if ticket.overdue]
    total_vencidos = sum(1 for ticket in _as_tickets(db.list_tickets()) if ticket.overdue)
    return {
        "tickets": tickets,
        "filtros": filtros,
        "solo_vencidos": solo_vencidos,
        "total_vencidos": total_vencidos,
    }


def _board_url(filtros: dict[str, str], solo_vencidos: bool) -> str:
    """URL de la página completa con los filtros activos (para HX-Push-Url)."""
    params = dict(filtros)
    if solo_vencidos:
        params["vencidos"] = "1"
    return "/?" + urlencode(params) if params else "/"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    yield


app = FastAPI(title="TriageBot", lifespan=lifespan)


def _classify(title: str, description: str) -> dict:
    """Red de seguridad genérica alrededor del clasificador.

    No contiene lógica de IA: solo garantiza que, pase lo que pase dentro de
    classifier.classify_ticket (o de un sustituto en los tests), el endpoint
    recibe una clasificación válida y nunca devuelve 5xx por culpa del LLM.
    """
    try:
        result = classifier.classify_ticket(title, description)
        return Classification.model_validate(result).model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clasificación fallida (%s); se aplica el fallback", type(exc).__name__)
        return classifier.fallback_classification()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets", response_model=Ticket, status_code=201)
def create_ticket(payload: TicketCreate) -> dict:
    # Orden: validar (Pydantic, antes de entrar aquí) -> clasificar -> guardar -> responder.
    classification = _classify(payload.title, payload.description)
    return db.insert_ticket(
        payload.title, payload.description, assignees=payload.assignees, **classification
    )


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    category: Category | None = None,
    priority: Priority | None = None,
    status: Status | None = None,
    assignee: str = "",
    overdue: bool | None = None,
) -> list[Ticket]:
    tickets = _as_tickets(
        db.list_tickets(
            category=category,
            priority=priority,
            status=status,
            assignee=_clean_assignee(assignee) or None,
        )
    )
    if overdue is not None:
        tickets = [ticket for ticket in tickets if ticket.overdue == overdue]
    return tickets


# OJO con el orden: "/tickets/tablero" tiene que declararse ANTES que "/tickets/{ticket_id}".
# Si no, FastAPI intentaría convertir "tablero" a entero y respondería 422.
@app.get("/tickets/tablero", response_class=HTMLResponse)
def tablero(
    request: Request,
    category: str = "",
    priority: str = "",
    status: str = "",
    assignee: str = "",
    vencidos: str = "",
) -> HTMLResponse:
    """Fragmento HTML (aviso de vencidos + tabla) para HTMX. El JSON está en GET /tickets."""
    filtros = _read_filters(category, priority, status, assignee)
    solo_vencidos = _read_solo_vencidos(vencidos)
    response = templates.TemplateResponse(
        request, "_tickets_table.html", _board_context(filtros, solo_vencidos)
    )
    # Mantiene los filtros en la URL del navegador: al refrescar (F5) no se pierden.
    response.headers["HX-Push-Url"] = _board_url(filtros, solo_vencidos)
    return response


@app.get("/tickets/{ticket_id}", response_model=Ticket)
def read_ticket(ticket_id: int) -> dict:
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict:
    ticket = db.update_ticket(ticket_id, payload.model_dump(exclude_none=True))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    category: str = "",
    priority: str = "",
    status: str = "",
    assignee: str = "",
    vencidos: str = "",
) -> HTMLResponse:
    # Los filtros llegan por la URL (/?category=bug): así sobreviven a un refresco.
    filtros = _read_filters(category, priority, status, assignee)
    context = _board_context(filtros, _read_solo_vencidos(vencidos))
    context["responsables"] = db.list_assignees()  # sugerencias del filtro por responsable
    return templates.TemplateResponse(request, "index.html", context)
