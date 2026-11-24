"""Tests de regresión de los fallos encontrados en el QA adversarial (QA_CHECKLIST.md).

- Escenario 1: un título solo con caracteres invisibles (\\u200b...) se aceptaba (201).
- Escenario 6: no se veían los logs INFO de cada intento del clasificador.

Ningún test llama al LLM real.
"""

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import classifier
from app.main import app, configure_logging
from app.models import strip_invisible

INVISIBLES = [
    "​​",  # espacio de ancho cero
    "  ",  # espacio no separable
    "﻿",  # BOM / espacio de ancho cero no separable
    " ​ ⁠ \t\n",  # mezcla de espacios normales e invisibles
]


@pytest.fixture()
def calls(tmp_path, monkeypatch):
    """Cliente con BD temporal y clasificador falso que cuenta las llamadas."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    made = []

    def fake_classifier(title, description):
        made.append(title)
        return {"category": "bug", "priority": "P2", "tags": []}

    monkeypatch.setattr("app.classifier.classify_ticket", fake_classifier)
    return SimpleNamespace(client=TestClient(app), made=made)


# --- Escenario 1: caracteres invisibles -------------------------------------------------


@pytest.mark.parametrize("title", INVISIBLES)
def test_invisible_title_is_rejected_without_calling_llm(calls, title):
    response = calls.client.post("/tickets", json={"title": title, "description": "Válida"})

    assert response.status_code == 422
    assert calls.made == []  # no se gastan tokens en un título vacío
    assert calls.client.get("/tickets").json() == []  # no se guarda nada


@pytest.mark.parametrize("description", INVISIBLES)
def test_invisible_description_is_rejected(calls, description):
    response = calls.client.post("/tickets", json={"title": "Válido", "description": description})

    assert response.status_code == 422


def test_invisible_chars_at_the_edges_are_removed(calls):
    response = calls.client.post(
        "/tickets", json={"title": "​La app no carga ", "description": "﻿Detalle"}
    )

    assert response.status_code == 201
    assert response.json()["title"] == "La app no carga"
    assert response.json()["description"] == "Detalle"


def test_zero_width_joiner_inside_emoji_is_kept():
    # 👩‍💻 = 👩 + ‍ + 💻: el ‍ de dentro no se toca, solo los de los extremos.
    assert strip_invisible("Bug de 👩‍💻") == "Bug de 👩‍💻"


def test_invisible_assignees_are_ignored_and_deduplicated(calls):
    response = calls.client.post(
        "/tickets",
        json={"title": "Con responsables", "description": "x", "assignees": ["​Ana", "Ana", "​"]},
    )

    assert response.status_code == 201
    assert response.json()["assignees"] == ["Ana"]


# --- Escenario 6: logs de cada intento del clasificador ---------------------------------


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_app_logger_shows_info_by_default(monkeypatch):
    monkeypatch.delenv("TRIAGEBOT_LOG_LEVEL", raising=False)
    configure_logging()

    app_logger = logging.getLogger("app")
    assert app_logger.handlers  # tiene handler propio: no depende de la config de uvicorn
    assert app_logger.isEnabledFor(logging.INFO)


def test_log_level_can_be_changed_and_invalid_values_fall_back_to_info(monkeypatch):
    monkeypatch.setenv("TRIAGEBOT_LOG_LEVEL", "warning")
    configure_logging()
    assert logging.getLogger("app").level == logging.WARNING

    monkeypatch.setenv("TRIAGEBOT_LOG_LEVEL", "inventado")
    configure_logging()
    assert logging.getLogger("app").level == logging.INFO


def test_each_classifier_attempt_is_logged_without_the_key(monkeypatch):
    monkeypatch.delenv("TRIAGEBOT_LOG_LEVEL", raising=False)
    configure_logging()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-clave-secreta-de-test")

    def failing_create(**kwargs):
        raise RuntimeError("401 para sk-or-clave-secreta-de-test")

    completions = SimpleNamespace(create=failing_create)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(classifier, "_create_client", lambda api_key: fake_client)

    handler = _ListHandler()
    app_logger = logging.getLogger("app")
    app_logger.addHandler(handler)
    try:
        result = classifier.classify_ticket("Título", "Descripción")
    finally:
        app_logger.removeHandler(handler)

    assert result == {"category": "question", "priority": "P3", "tags": []}
    log = "\n".join(handler.messages)
    assert "intento 1/2 fallido" in log
    assert "intento 2/2 fallido" in log
    assert "fallback tras 2 intentos" in log
    assert "sk-or-clave-secreta-de-test" not in log  # la key nunca se registra
