"""Persistencia de tickets en SQLite con el módulo estándar sqlite3 (SPEC §4).

- Una conexión por operación (abrir, ejecutar, commit, cerrar).
- Todas las consultas usan parámetros `?`.
- La ruta de la base de datos se resuelve EN CADA CONEXIÓN (no al importar), porque los
  tests fijan DATABASE_URL después de importar la app.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

DEFAULT_DB_PATH = "triagebot.db"
SQLITE_URL_PREFIX = "sqlite:///"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    priority    TEXT    NOT NULL,
    tags        TEXT    NOT NULL DEFAULT '[]',
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
)
"""

# Fragmentos SQL constantes: los valores siempre van como parámetros `?`.
_FILTER_CLAUSES = {
    "category": "category = ?",
    "priority": "priority = ?",
    "status": "status = ?",
}
_UPDATE_CLAUSES = {
    "status": "status = ?",
    "priority": "priority = ?",
}


def _db_path() -> str:
    """DATABASE_URL (sqlite:///ruta) -> TRIAGEBOT_DB -> triagebot.db."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url.removeprefix(SQLITE_URL_PREFIX)
    return os.getenv("TRIAGEBOT_DB") or DEFAULT_DB_PATH


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Se crea la tabla también aquí: TestClient(app) sin `with` no ejecuta el arranque.
        conn.execute(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict:
    ticket = dict(row)
    ticket["tags"] = json.loads(ticket["tags"])
    return ticket


def init_db() -> None:
    """Crea la tabla si no existe."""
    with _connection():
        pass


def insert_ticket(
    title: str, description: str, category: str, priority: str, tags: list[str]
) -> dict:
    now = _now()
    tags_json = json.dumps(tags, ensure_ascii=False)
    with _connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tickets "
            "(title, description, category, priority, tags, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
            (title, description, category, priority, tags_json, now, now),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_ticket(ticket_id: int) -> dict | None:
    try:
        with _connection() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    except OverflowError:  # id mayor que un entero de 64 bits: no puede existir
        return None
    return _row_to_dict(row) if row else None


def list_tickets(
    category: str | None = None, priority: str | None = None, status: str | None = None
) -> list[dict]:
    filters = {"category": category, "priority": priority, "status": status}
    clauses = [_FILTER_CLAUSES[name] for name, value in filters.items() if value is not None]
    params = [value for value in filters.values() if value is not None]

    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"

    with _connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_ticket(ticket_id: int, changes: dict) -> dict | None:
    """Actualiza status y/o priority. Devuelve None si el ticket no existe."""
    clauses = [clause for field, clause in _UPDATE_CLAUSES.items() if field in changes]
    params: list = [changes[field] for field in _UPDATE_CLAUSES if field in changes]
    clauses.append("updated_at = ?")
    params.append(_now())

    try:
        with _connection() as conn:
            cursor = conn.execute(
                "UPDATE tickets SET " + ", ".join(clauses) + " WHERE id = ?",
                (*params, ticket_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    except OverflowError:
        return None
    return _row_to_dict(row)
