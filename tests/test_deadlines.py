"""Tests de fecha límite, vencidos y ciclo de estados (SPEC §4.1, §4.2 y §7).

Ningún test llama al LLM real: el clasificador se sustituye con monkeypatch.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import db, deadlines
from app.main import app

CREATED = "2026-11-24T10:00:00Z"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P1", "tags": []},
    )
    return TestClient(app)


def create_ticket(client, title="La app no carga"):
    response = client.post("/tickets", json={"title": title, "description": "Pantalla en blanco"})
    assert response.status_code == 201
    return response.json()


# --- Lógica pura (app/deadlines.py) -------------------------------------------------------


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        ("P1", "2026-11-24T23:59:59Z"),  # hoy mismo
        ("P2", "2026-11-25T23:59:59Z"),  # mañana
        ("P3", "2026-11-26T23:59:59Z"),  # un par de días
    ],
)
def test_due_at_depends_on_priority(priority, expected):
    assert deadlines.due_at(CREATED, priority) == expected


def test_overdue_only_after_deadline():
    due = deadlines.due_at(CREATED, "P1")
    before = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)
    after = datetime(2026, 11, 25, 0, 0, 1, tzinfo=UTC)

    assert deadlines.is_overdue(due, "open", now=before) is False
    assert deadlines.is_overdue(due, "open", now=after) is True
    assert deadlines.is_overdue(due, "in_progress", now=after) is True


def test_resolved_ticket_is_never_overdue():
    due = deadlines.due_at(CREATED, "P1")
    much_later = datetime(2027, 1, 1, tzinfo=UTC)

    assert deadlines.is_overdue(due, "closed", now=much_later) is False


# --- API ----------------------------------------------------------------------------------


def test_ticket_json_includes_due_at_and_overdue(client):
    ticket = create_ticket(client)

    assert ticket["due_at"] == deadlines.due_at(ticket["created_at"], "P1")
    assert ticket["overdue"] is False


def test_changing_priority_recalculates_due_at(client):
    ticket = create_ticket(client)

    patched = client.patch(f"/tickets/{ticket['id']}", json={"priority": "P3"}).json()

    assert patched["due_at"] == deadlines.due_at(ticket["created_at"], "P3")


def test_status_lifecycle_open_in_progress_resolved_reopened(client):
    ticket_id = create_ticket(client)["id"]

    for status in ("in_progress", "closed", "open"):  # Empezar -> Resolver -> Reabrir
        response = client.patch(f"/tickets/{ticket_id}", json={"status": status})
        assert response.status_code == 200
        assert response.json()["status"] == status


def test_overdue_filter_in_json_and_board(client, monkeypatch):
    # Ticket antiguo: creado en enero, con P1 venció ese mismo día.
    real_now = db._now
    monkeypatch.setattr(db, "_now", lambda: "2026-01-05T09:00:00Z")
    old_id = create_ticket(client, title="Ticket antiguo")["id"]
    monkeypatch.setattr(db, "_now", real_now)
    new_id = create_ticket(client, title="Ticket de hoy")["id"]

    overdue = client.get("/tickets", params={"overdue": "true"}).json()
    assert [t["id"] for t in overdue] == [old_id]
    not_overdue = client.get("/tickets", params={"overdue": "false"}).json()
    assert [t["id"] for t in not_overdue] == [new_id]

    board = client.get("/tickets/tablero", params={"vencidos": "1"})
    assert board.status_code == 200
    assert "Ticket antiguo" in board.text
    assert "Ticket de hoy" not in board.text
    assert "Vencido" in board.text
    assert "1 ticket vencido" in board.text
    assert board.headers["HX-Push-Url"] == "/?vencidos=1"

    # Resolverlo lo quita de vencidos.
    client.patch(f"/tickets/{old_id}", json={"status": "closed"})
    assert client.get("/tickets", params={"overdue": "true"}).json() == []


def test_board_shows_spanish_status_and_actions(client):
    ticket_id = create_ticket(client)["id"]
    client.patch(f"/tickets/{ticket_id}", json={"status": "closed"})

    board = client.get("/tickets/tablero")

    assert "Resuelto" in board.text
    assert "Reabrir" in board.text
    assert f'hx-patch="/tickets/{ticket_id}"' in board.text
