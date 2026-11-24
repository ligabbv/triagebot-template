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

# Responsables (SPEC §4): relación N:M, una fila por persona asignada a un ticket.
ASSIGNEES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_assignees (
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    assignee     TEXT    NOT NULL,
    assignee_key TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (ticket_id, assignee_key)
)
"""


def assignee_key(name: str) -> str:
    """Clave para comparar responsables sin mayúsculas, también con tildes y eñes.

    COLLATE NOCASE de SQLite solo ignora mayúsculas en ASCII ('IÑIGO' != 'Iñigo').
    """
    return " ".join(name.split()).casefold()

# Fragmentos SQL constantes: los valores siempre van como parámetros `?`.
_FILTER_CLAUSES = {
    "category": "category = ?",
    "priority": "priority = ?",
    "status": "status = ?",
    # El ticket tiene a esa persona entre sus responsables (aunque tenga más).
    "assignee": "id IN (SELECT ticket_id FROM ticket_assignees WHERE assignee_key = ?)",
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
        conn.execute(ASSIGNEES_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict:
    ticket = dict(row)
    ticket["tags"] = json.loads(ticket["tags"])
    ticket["assignees"] = []
    return ticket


def _tickets_with_assignees(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """Convierte filas en dicts y les añade sus responsables (una sola consulta extra)."""
    tickets = [_row_to_dict(row) for row in rows]
    if not tickets:
        return tickets
    by_id = {ticket["id"]: ticket for ticket in tickets}
    placeholders = ", ".join("?" for _ in by_id)
    assigned = conn.execute(
        "SELECT ticket_id, assignee FROM ticket_assignees "
        f"WHERE ticket_id IN ({placeholders}) ORDER BY ticket_id, position",
        list(by_id),
    ).fetchall()
    for ticket_id, assignee in assigned:
        by_id[ticket_id]["assignees"].append(assignee)
    return tickets


def _set_assignees(conn: sqlite3.Connection, ticket_id: int, assignees: list[str]) -> None:
    """Sustituye la lista completa de responsables de un ticket."""
    conn.execute("DELETE FROM ticket_assignees WHERE ticket_id = ?", (ticket_id,))
    conn.executemany(
        "INSERT INTO ticket_assignees (ticket_id, assignee, assignee_key, position) "
        "VALUES (?, ?, ?, ?)",
        [
            (ticket_id, name, assignee_key(name), position)
            for position, name in enumerate(assignees)
        ],
    )


def _fetch_one(conn: sqlite3.Connection, ticket_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _tickets_with_assignees(conn, [row])[0] if row else None


def init_db() -> None:
    """Crea la tabla si no existe."""
    with _connection():
        pass


def insert_ticket(
    title: str,
    description: str,
    category: str,
    priority: str,
    tags: list[str],
    assignees: list[str] | None = None,
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
        _set_assignees(conn, cursor.lastrowid, assignees or [])
        ticket = _fetch_one(conn, cursor.lastrowid)
    return ticket


def get_ticket(ticket_id: int) -> dict | None:
    try:
        with _connection() as conn:
            return _fetch_one(conn, ticket_id)
    except OverflowError:  # id mayor que un entero de 64 bits: no puede existir
        return None


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
) -> list[dict]:
    filters = {
        "category": category,
        "priority": priority,
        "status": status,
        "assignee": assignee_key(assignee) if assignee else None,
    }
    clauses = [_FILTER_CLAUSES[name] for name, value in filters.items() if value is not None]
    params = [value for value in filters.values() if value is not None]

    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"

    with _connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return _tickets_with_assignees(conn, rows)


def list_assignees() -> list[str]:
    """Nombres ya asignados a algún ticket (para sugerirlos en el filtro), sin duplicados."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT MIN(assignee) AS assignee FROM ticket_assignees "
            "GROUP BY assignee_key ORDER BY assignee_key"
        ).fetchall()
    return [row["assignee"] for row in rows]


# Consultas fijas (el nombre de columna no puede ir como parámetro `?`).
_COUNT_QUERIES = {
    "category": "SELECT category AS valor, COUNT(*) AS n FROM tickets GROUP BY category",
    "priority": "SELECT priority AS valor, COUNT(*) AS n FROM tickets GROUP BY priority",
    "status": "SELECT status AS valor, COUNT(*) AS n FROM tickets GROUP BY status",
}


def count_tickets() -> dict:
    """Conteos para GET /tickets/stats: {"total": n, "category": {...}, "priority": ..., ...}.

    Solo trae los valores que existen en la BD; rellenar con 0 es cosa de quien lo use.
    """
    with _connection() as conn:
        counts: dict = {"total": conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]}
        for field, query in _COUNT_QUERIES.items():
            counts[field] = {row["valor"]: row["n"] for row in conn.execute(query)}
    return counts


def update_ticket(ticket_id: int, changes: dict) -> dict | None:
    """Actualiza status, priority y/o assignees. Devuelve None si el ticket no existe."""
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
            if "assignees" in changes:
                _set_assignees(conn, ticket_id, changes["assignees"])
            return _fetch_one(conn, ticket_id)
    except OverflowError:
        return None
