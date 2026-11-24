"""Modelos Pydantic y enums de dominio de TriageBot (SPEC §4)."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
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

# Se aplica strip() y después se comprueba la longitud: "   " queda vacío -> 422.
Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class TicketCreate(BaseModel):
    """Body de POST /tickets."""

    title: Title
    description: Description


class TicketUpdate(BaseModel):
    """Body de PATCH /tickets/{id}: solo status y/o priority; nada más."""

    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TicketUpdate":
        if self.status is None and self.priority is None:
            raise ValueError("Indica al menos 'status' o 'priority'")
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
