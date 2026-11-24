"""Modelos Pydantic y enums de dominio de TriageBot (SPEC §4)."""

import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from app import deadlines

ALLOWED_CATEGORIES = {"bug", "feature_request", "question", "urgent"}
ALLOWED_PRIORITIES = {"P1", "P2", "P3"}
ALLOWED_STATUSES = {"open", "in_progress", "closed"}

# Enums vinculantes: exactamente estos valores (los tests fallan con "URGENT", "Bug"...).
Category = Literal["bug", "feature_request", "question", "urgent"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "in_progress", "closed"]

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 5000
MAX_TAGS = 5
MAX_TAG_LENGTH = 30
MAX_ASSIGNEES = 10
MAX_ASSIGNEE_LENGTH = 50

def _is_invisible(char: str) -> bool:
    """Espacios (incluido el no separable \\u00a0) y caracteres de formato invisibles (categoría
    Unicode "Cf": \\u200b espacio de ancho cero, \\u200d, \\u2060, \\ufeff...)."""
    return char.isspace() or unicodedata.category(char) == "Cf"


def strip_invisible(value: object) -> object:
    """Como strip(), pero quitando también los caracteres invisibles de los extremos.

    Así "\\u200b\\u200b" queda vacío y da 422, igual que "   " (QA, escenario 1). Solo toca los
    extremos: un \\u200d dentro de un emoji compuesto (👩‍💻) se conserva. Si no es texto, lo
    devuelve tal cual para que Pydantic dé su error de tipo.
    """
    if not isinstance(value, str):
        return value
    start, end = 0, len(value)
    while start < end and _is_invisible(value[start]):
        start += 1
    while end > start and _is_invisible(value[end - 1]):
        end -= 1
    return value[start:end]


# Se quitan espacios e invisibles de los extremos y después se comprueba la longitud:
# "   " o "​" quedan vacíos -> 422.
Title = Annotated[
    str,
    BeforeValidator(strip_invisible),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
Description = Annotated[
    str,
    BeforeValidator(strip_invisible),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


def normalize_assignees(value: object) -> list[str]:
    """Responsables (SPEC §4): lista o texto con comas -> lista limpia y sin duplicados.

    Lanza ValueError (-> 422) si se superan los límites: es dato del usuario, no se recorta.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise ValueError("Los responsables deben ser una lista de nombres")

    normalized: list[str] = []
    seen: set[str] = set()
    for name in value:
        if not isinstance(name, str):
            raise ValueError("Cada responsable debe ser un nombre de texto")
        clean = " ".join(strip_invisible(name).split())
        if not clean or clean.casefold() in seen:
            continue
        if len(clean) > MAX_ASSIGNEE_LENGTH:
            raise ValueError(
                f"El nombre '{clean[:20]}…' supera {MAX_ASSIGNEE_LENGTH} caracteres"
            )
        seen.add(clean.casefold())
        normalized.append(clean)

    if len(normalized) > MAX_ASSIGNEES:
        raise ValueError(f"Como máximo {MAX_ASSIGNEES} responsables por ticket")
    return normalized


class TicketCreate(BaseModel):
    """Body de POST /tickets."""

    title: Title
    description: Description
    assignees: list[str] = []

    @field_validator("assignees", mode="before")
    @classmethod
    def _assignees(cls, value: object) -> list[str]:
        return normalize_assignees(value)


class TicketUpdate(BaseModel):
    """Body de PATCH /tickets/{id}: solo status, priority y/o assignees; nada más."""

    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    priority: Priority | None = None
    # None = no tocar; [] = dejar sin asignar. Si viene, SUSTITUYE la lista entera.
    assignees: list[str] | None = None

    @field_validator("assignees", mode="before")
    @classmethod
    def _assignees(cls, value: object) -> list[str] | None:
        return None if value is None else normalize_assignees(value)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TicketUpdate":
        if self.status is None and self.priority is None and self.assignees is None:
            raise ValueError("Indica al menos 'status', 'priority' o 'assignees'")
        return self


class Ticket(BaseModel):
    """Ticket tal y como lo devuelve la API."""

    id: int
    title: str
    description: str
    category: Category
    priority: Priority
    tags: list[str]
    status: Status
    assignees: list[str]
    created_at: str  # ISO 8601 UTC, p. ej. 2026-06-29T09:30:00Z
    updated_at: str

    # Campos calculados (SPEC §4.1): salen en el JSON pero no se guardan en la BD.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def due_at(self) -> str:
        """Fecha límite según la prioridad."""
        return deadlines.due_at(self.created_at, self.priority)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overdue(self) -> bool:
        """True si ya pasó la fecha límite y el ticket no está resuelto."""
        return deadlines.is_overdue(self.due_at, self.status)


class Classification(BaseModel):
    """Salida validada del clasificador (SPEC §6)."""

    category: Category
    priority: Priority
    tags: list[str] = []

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> list[str]:
        """Minúscula, strip, máx. 30 chars, sin vacíos ni duplicados, máx. 5."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags debe ser una lista")
        normalized: list[str] = []
        for tag in value:
            if not isinstance(tag, str):
                continue
            clean = tag.strip().lower()[:MAX_TAG_LENGTH].strip()
            if clean and clean not in normalized:
                normalized.append(clean)
            if len(normalized) == MAX_TAGS:
                break
        return normalized
