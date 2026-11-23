"""Tests del módulo app/classifier.py (SPEC §6 y §9.2).

Ningún test llama al LLM real: se sustituye app.classifier._create_client por un
cliente falso que imita la forma de respuesta del SDK de OpenAI
(response.choices[0].message.content).
"""

from types import SimpleNamespace

import pytest

from app import classifier

FALLBACK = {"category": "question", "priority": "P3", "tags": []}


class FakeClient:
    """Cliente falso: devuelve (o lanza) las respuestas indicadas, una por llamada."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        message = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture()
def fake_llm(monkeypatch):
    """Instala un cliente falso con las respuestas dadas y devuelve el cliente."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def install(*responses):
        client = FakeClient(responses)
        monkeypatch.setattr(classifier, "_create_client", lambda api_key: client)
        return client

    return install


def test_valid_response_returns_classification(fake_llm):
    client = fake_llm('{"category": "bug", "priority": "P1", "tags": ["Login", " crash "]}')

    result = classifier.classify_ticket("La app no carga", "Pantalla en blanco al iniciar")

    assert result == {"category": "bug", "priority": "P1", "tags": ["login", "crash"]}
    assert client.calls == 1
    assert client.last_kwargs["model"] == "openai/gpt-oss-120b"
    assert client.last_kwargs["max_tokens"] == 1024


def test_response_wrapped_in_markdown_is_parsed(fake_llm):
    fake_llm('```json\n{"category": "feature_request", "priority": "P3", "tags": []}\n```')

    result = classifier.classify_ticket("Exportar a PDF", "Querría exportar informes")

    assert result == {"category": "feature_request", "priority": "P3", "tags": []}


def test_invalid_category_uses_fallback_after_one_retry(fake_llm):
    invalid = '{"category": "URGENT", "priority": "P1", "tags": []}'
    client = fake_llm(invalid, invalid)

    result = classifier.classify_ticket("Urge", "Demo el viernes")

    assert result == FALLBACK
    assert client.calls == 2  # 1 intento + 1 reintento, nunca más


def test_sdk_exception_twice_uses_fallback_without_raising(fake_llm):
    client = fake_llm(RuntimeError("OpenRouter caído"), TimeoutError("timeout"))

    result = classifier.classify_ticket("Algo", "Lo que sea")

    assert result == FALLBACK
    assert client.calls == 2


def test_retry_succeeds_on_second_attempt(fake_llm):
    client = fake_llm(
        RuntimeError("fallo puntual"),
        '{"category": "question", "priority": "P2", "tags": ["vpn"]}',
    )

    result = classifier.classify_ticket("¿Cómo conecto la VPN?", "No sé configurarla")

    assert result == {"category": "question", "priority": "P2", "tags": ["vpn"]}
    assert client.calls == 2


def test_missing_api_key_uses_fallback_without_calling_llm(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def must_not_be_called(api_key):
        raise AssertionError("No debe crearse el cliente sin API key")

    monkeypatch.setattr(classifier, "_create_client", must_not_be_called)

    result = classifier.classify_ticket("Algo", "Lo que sea")

    assert result == FALLBACK
