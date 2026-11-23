"""Clasificador de tickets con LLM (SPEC §6).

REGLA DE ARQUITECTURA: este es el ÚNICO módulo que habla con una API de IA
(OpenRouter, Anthropic, etc.). No importa FastAPI. El resto de la app solo usa
`classify_ticket()` y `fallback_classification()`.
"""

import json
import logging
import os

from openai import OpenAI

from app.models import Classification

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
API_KEY_ENV_VAR = "OPENROUTER_API_KEY"

TEMPERATURE = 0
MAX_TOKENS = 1024
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 2  # 1 intento + 1 reintento, nunca más

FALLBACK_CLASSIFICATION = {"category": "question", "priority": "P3", "tags": []}

SYSTEM_PROMPT = (
    "Eres un sistema de clasificación de tickets de soporte técnico. Recibirás el título "
    "y la descripción de un ticket. Devuelve EXCLUSIVAMENTE un objeto JSON válido con tres "
    "campos: category (uno de: bug, feature_request, question, urgent), priority (uno de: "
    "P1, P2, P3) y tags (lista de máx. 5 strings cortos en minúscula). Sin markdown, sin "
    "explicaciones, sin texto antes ni después. No inventes categorías.\n"
    "Criterios: urgent = servicio caído o bloqueo total de un cliente; bug = algo no "
    "funciona como debería; feature_request = petición de algo nuevo o mejora; "
    "question = duda de uso. P1 = hay que atenderlo hoy; P2 = importante, mañana; "
    "P3 = puede esperar."
)


def fallback_classification() -> dict:
    """Copia nueva del fallback (para que nadie modifique la constante compartida)."""
    return {**FALLBACK_CLASSIFICATION, "tags": []}


def _create_client(api_key: str) -> OpenAI:
    # max_retries=0: los reintentos los controlamos nosotros (máximo 1).
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        timeout=TIMEOUT_SECONDS,
        max_retries=0,
    )


def _parse_response(content: str | None) -> dict:
    """Extrae el primer objeto JSON del texto (también si viene en ```json) y lo valida.

    Lanza una excepción si la salida no cumple el contrato.
    """
    if not content:
        raise ValueError("respuesta vacía del modelo")
    start = content.find("{")
    if start == -1:
        raise ValueError("la respuesta no contiene un objeto JSON")
    data, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(data, dict):
        raise ValueError("el JSON no es un objeto")
    return Classification.model_validate(data).model_dump()


def classify_ticket(title: str, description: str) -> dict:
    """Clasifica un ticket. Devuelve SIEMPRE un dict válido; nunca lanza excepciones."""
    api_key = os.getenv(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        logger.warning("Clasificador: falta %s; se aplica el fallback", API_KEY_ENV_VAR)
        return fallback_classification()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Título: {title}\nDescripción: {description}"},
    ]

    last_error = "desconocido"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            client = _create_client(api_key)
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                # gpt-oss es un modelo de razonamiento: esfuerzo bajo para que el
                # razonamiento no se coma los max_tokens y deje la respuesta vacía.
                extra_body={"reasoning": {"effort": "low"}},
            )
            return _parse_response(response.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001 - nunca se propaga nada del SDK
            # Nunca registrar la API key, ni siquiera si aparece en el mensaje de error.
            last_error = f"{type(exc).__name__}: {exc}".replace(api_key, "***")
            logger.info(
                "Clasificador: intento %d/%d fallido (%s)", attempt, MAX_ATTEMPTS, last_error
            )

    logger.warning(
        "Clasificador: fallback tras %d intentos. Última causa: %s", MAX_ATTEMPTS, last_error
    )
    return fallback_classification()
