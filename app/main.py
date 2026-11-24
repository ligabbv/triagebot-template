"""TriageBot: app FastAPI y endpoints HTTP (SPEC §5).

Aquí no hay SQL (va en db.py) ni llamadas a ninguna API de IA (van SOLO en classifier.py).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import classifier, db
from app.models import (
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

# Evento HTMX que emite POST /tickets: la tabla lo escucha y se recarga.
TICKET_CREATED_EVENT = "ticket-created"


def _fecha(iso: str) -> str:
    """'2026-06-29T09:30:00Z' -> '29/06/2026 09:30' (SPEC §7)."""
    try:
        fecha, hora = iso.split("T", 1)
        anio, mes, dia = fecha.split("-")
        return f"{dia}/{mes}/{anio} {hora[:5]}"
    except ValueError:
        return iso


templates.env.filters["fecha"] = _fecha


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
def create_ticket(payload: TicketCreate, response: Response) -> dict:
    # Orden: validar (Pydantic, antes de entrar aquí) -> clasificar -> guardar -> responder.
    classification = _classify(payload.title, payload.description)
    ticket = db.insert_ticket(payload.title, payload.description, **classification)
    # La respuesta sigue siendo JSON (la usan los tests); la cabecera solo la lee HTMX.
    response.headers["HX-Trigger"] = TICKET_CREATED_EVENT
    return ticket


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    category: Category | None = None,
    priority: Priority | None = None,
    status: Status | None = None,
) -> list[dict]:
    return db.list_tickets(category=category, priority=priority, status=status)


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
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"tickets": db.list_tickets()})


@app.get("/partials/tickets", response_class=HTMLResponse)
def tickets_rows(
    request: Request,
    category: Category | None = None,
    priority: Priority | None = None,
    status: Status | None = None,
) -> HTMLResponse:
    """Solo las filas <tr> del tablero, para que HTMX las sustituya sin recargar."""
    tickets = db.list_tickets(category=category, priority=priority, status=status)
    return templates.TemplateResponse(request, "_tickets_rows.html", {"tickets": tickets})
