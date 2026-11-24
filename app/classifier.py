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

SYSTEM_PROMPT = """\
Eres el sistema de triage del equipo de Soporte interno de una empresa. Recibirás el \
título y la descripción de un ticket escrito por un empleado. Devuelve EXCLUSIVAMENTE \
un objeto JSON válido con tres campos: category (uno de: bug, feature_request, \
question, urgent), priority (uno de: P1, P2, P3) y tags (lista de 1 a 5 strings cortos \
en minúscula). Sin markdown, sin explicaciones, sin texto antes ni después. No \
inventes categorías.

Categorías:
- urgent: siempre P1. Úsala si se cumple CUALQUIERA de estas condiciones:
  * Impacto masivo: un servicio caído o bloqueado para un equipo, una planta, un \
almacén o para clientes (VPN de todo el equipo, centralita, web pública, datáfono).
  * Incidente de seguridad: datos enviados a quien no debía, cuenta posiblemente \
comprometida, intentos de acceso sospechosos.
  * Pérdida de datos compartidos (una carpeta o unidad de red borrada).
  * Una persona bloqueada con un plazo inminente (hoy, en minutos u horas) o con un \
cliente esperando (demo, reunión, firma, presentación).
- bug: algo que existía no funciona como debería, aunque venga redactado como \
pregunta ("¿es normal que falte una factura?", "¿hay que refrescar algo?").
- feature_request: pide algo nuevo o un cambio de comportamiento ("sería útil", \
"propuesta", "permitir", "añadir", "me gustaría poder"), aunque describa una molestia.
- question: duda de uso, de procedimiento o de política ("¿cómo...?", "¿dónde...?", \
"¿puedo...?") sin que nada esté roto.

Prioridad:
- P1: hay que atenderlo hoy. Todo urgent; y los bug que impiden trabajar del todo, \
pierden datos o generan importes erróneos a clientes (facturas, precios).
- P2: importante, mañana. Es la prioridad POR DEFECTO de un bug que no es P1. En \
particular, P2 si falla un servicio compartido (correo, Outlook, Teams, intranet, \
buscador, login, CRM, ERP, informes, impresión, sincronización del móvil) aunque \
siga funcionando en parte o vaya lento, o si da datos o resultados incorrectos. \
También question con implicación de seguridad o con fecha cercana (tarjeta \
perdida, alta de alguien que empieza el lunes).
- P3: puede esperar. Toda feature_request y casi toda question. Un bug solo es P3 \
si es estético o menor (un widget, un orden, un color, un periférico personal) o si \
el PROPIO texto dice que hay una alternativa que funciona ("si lo descargo está \
completa", "al recargar se arregla"). En caso de duda entre P2 y P3 en un bug, P2.

Tags: prioriza el sistema afectado con estos nombres cuando apliquen: vpn, red, \
correo, crm, erp, intranet, portal-rrhh, nominas, fichaje, gastos, facturacion, \
pedidos, tickets, impresora, portatil, movil, telefonia, teams, acceso, seguridad, \
datos, rendimiento, informes. Después, 1 o 2 tags concretos del problema. Nunca \
tags vacíos de significado como "error", "problema" o "nada".\
"""

# Ejemplos reales del histórico (data/tickets_etiquetados.json, ejemplo_prompt=true),
# elegidos para cubrir los casos dudosos de cada categoría. Se envían como pares
# user/assistant antes del ticket nuevo (few-shot).
FEW_SHOT_EXAMPLES = [
    (
        "La VPN rechaza a todo el equipo de teletrabajo",
        "Desde primera hora ninguno de los que trabajamos en remoto podemos conectar a la "
        "VPN. Sale 'authentication failed' aunque la contraseña es correcta.",
        {"category": "urgent", "priority": "P1", "tags": ["vpn", "acceso", "teletrabajo"]},
    ),
    (
        "Posible fuga: he enviado datos a un destinatario equivocado",
        "Acabo de mandar por error un correo con datos de clientes a una dirección externa "
        "que no era. ¿Qué hago de inmediato?",
        {"category": "urgent", "priority": "P1", "tags": ["seguridad", "correo", "datos"]},
    ),
    (
        "No puedo acceder al CRM y tengo una demo con cliente en 30 minutos",
        "Al entrar en el CRM me sale 'error 500' una y otra vez. Tengo una demostración con "
        "un cliente importante dentro de media hora y necesito sacar su ficha.",
        {"category": "urgent", "priority": "P1", "tags": ["crm", "acceso", "demo-cliente"]},
    ),
    (
        "El total de la factura sale mal en el ERP",
        "Al generar una factura con descuento, el total no resta el descuento. El subtotal "
        "está bien pero el importe final está inflado.",
        {"category": "bug", "priority": "P1", "tags": ["erp", "facturacion", "descuentos"]},
    ),
    (
        "No me llega el código de verificación por SMS para entrar",
        "Al iniciar sesión con doble factor, el SMS con el código no llega nunca y no puedo "
        "acceder.",
        {"category": "bug", "priority": "P1", "tags": ["acceso", "doble-factor", "sms"]},
    ),
    (
        "La numeración de las facturas tiene un salto",
        "He notado que entre dos facturas consecutivas hay un número que falta. ¿Es normal o "
        "se ha perdido una factura?",
        {"category": "bug", "priority": "P2", "tags": ["facturacion", "numeracion"]},
    ),
    (
        "El visor de PDF de la intranet no muestra la última página",
        "Al abrir documentos largos en el visor integrado, siempre falta la última página. "
        "Si lo descargo y lo abro aparte, está completa.",
        {"category": "bug", "priority": "P3", "tags": ["intranet", "visor-pdf"]},
    ),
    (
        "Sería útil poder exportar los tickets a Excel",
        "Para hacer informes mensuales tendría que pasar los datos a una hoja de cálculo. "
        "¿Podríais añadir un botón de exportar a Excel en el listado de tickets?",
        {"category": "feature_request", "priority": "P3", "tags": ["tickets", "exportacion"]},
    ),
    (
        "Petición: aumentar el tiempo antes de que se cierre la sesión",
        "La sesión del portal se cierra muy rápido por inactividad y pierdo lo que estoy "
        "haciendo. ¿Se puede ampliar ese tiempo?",
        {"category": "feature_request", "priority": "P3", "tags": ["intranet", "sesion"]},
    ),
    (
        "Permitir asignar un ticket a varias personas",
        "A veces una incidencia la tenemos que resolver entre dos. Estaría bien poder asignar "
        "el mismo ticket a más de una persona.",
        {"category": "feature_request", "priority": "P3", "tags": ["tickets", "asignacion"]},
    ),
    (
        "¿Cómo añado una excepción para una web bloqueada que necesito?",
        "Una web que necesito para trabajar aparece bloqueada por el filtro. ¿Cómo pido que "
        "la habiliten?",
        {"category": "question", "priority": "P3", "tags": ["red", "filtro-web"]},
    ),
    (
        "¿Qué hago si pierdo la tarjeta de acceso al edificio?",
        "Creo que he extraviado mi tarjeta de acceso. ¿A quién aviso y cómo consigo una nueva?",
        {"category": "question", "priority": "P2", "tags": ["seguridad", "tarjeta-acceso"]},
    ),
    (
        "El corrector del editor de documentos marca todo en inglés",
        "El procesador de textos me subraya todas las palabras en español como erróneas "
        "porque está corrigiendo en inglés y no encuentro dónde cambiar el idioma.",
        {"category": "question", "priority": "P3", "tags": ["ofimatica", "idioma"]},
    ),
]


def _user_message(title: str, description: str) -> str:
    return f"Título: {title}\nDescripción: {description}"


def _build_messages(title: str, description: str) -> list[dict]:
    """System + ejemplos (user/assistant) + el ticket a clasificar."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example_title, example_description, expected in FEW_SHOT_EXAMPLES:
        messages.append(
            {"role": "user", "content": _user_message(example_title, example_description)}
        )
        messages.append(
            {"role": "assistant", "content": json.dumps(expected, ensure_ascii=False)}
        )
    messages.append({"role": "user", "content": _user_message(title, description)})
    return messages


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

    messages = _build_messages(title, description)

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
