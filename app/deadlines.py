"""Fecha límite y vencidos de los tickets (SPEC §4.1).

Funciones puras: no tocan la BD, ni FastAPI, ni el LLM. `due_at` y `overdue` se
calculan en cada respuesta a partir de `created_at`, `priority` y `status`; no se
guardan, así que si cambia la prioridad la fecha límite se recalcula sola.
"""

from datetime import UTC, datetime, time, timedelta

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Días de plazo desde el día de creación: P1 hoy mismo, P2 mañana, P3 un par de días.
DAYS_BY_PRIORITY = {"P1": 0, "P2": 1, "P3": 2}
END_OF_DAY = time(23, 59, 59)

# Un ticket resuelto nunca cuenta como vencido.
RESOLVED_STATUS = "closed"


def parse_timestamp(value: str) -> datetime:
    """'2026-11-24T10:00:00Z' -> datetime con zona UTC."""
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def due_at(created_at: str, priority: str) -> str:
    """Fecha límite: final del día de creación + los días de plazo de su prioridad."""
    created = parse_timestamp(created_at)
    due_day = created.date() + timedelta(days=DAYS_BY_PRIORITY[priority])
    return format_timestamp(datetime.combine(due_day, END_OF_DAY, tzinfo=UTC))


def is_overdue(due: str, status: str, now: datetime | None = None) -> bool:
    """True si ya pasó la fecha límite y el ticket sigue sin resolver."""
    if status == RESOLVED_STATUS:
        return False
    now = now or datetime.now(UTC)
    return now > parse_timestamp(due)
