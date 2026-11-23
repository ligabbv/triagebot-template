# CLAUDE.md

> **Equipo B (Spec-Driven):** este archivo es vuestro. Completadlo con vuestras
> convenciones como parte del trabajo de hoy — es tan importante como el `SPEC.md`
> que vais a escribir. Claude Code lo lee automáticamente al abrir el repo.
>
> **Equipo A (Vibe):** podéis ignorarlo. No estáis obligados a tocarlo.

Este repo es una plantilla docente para construir **TriageBot**, una aplicación
FastAPI que clasifica tickets de soporte con un LLM (gpt-oss-120b vía OpenRouter).

## Stack (innegociable)

- Python 3.11+
- FastAPI
- SQLite
- HTMX + Jinja2
- Tailwind (por CDN)
- SDK de OpenAI (apuntado a OpenRouter)
- pytest
- ruff

## Reglas del taller (para todos los equipos)

Estas reglas no son metodología: son condiciones del bootcamp. Se cumplen seas
del equipo que seas.

1. No modifiques `tests/test_acceptance.py` salvo que el profesor lo indique
   expresamente.
2. Nunca hardcodees una API key en el código.
3. Lee la API key desde la variable de entorno `OPENROUTER_API_KEY`.
4. `.env` nunca se commitea. Comprueba que está en `.gitignore` antes de tu
   primer commit.

## Convenciones del Equipo B

La especificación vinculante es `SPEC.md`; las decisiones que se desvían de ella
van en su sección 13 con fecha y motivo.

### Regla de la IA (la más importante)

- Todo lo que llame a cualquier API de IA (OpenRouter, Anthropic, etc.) vive
  **solo** en `app/classifier.py`. Si hay llamadas al SDK en `main.py` o en otro
  módulo, hay un problema.
- `classifier.py` no importa FastAPI, Jinja2 ni `db`. Expone
  `classify_ticket(title, description) -> dict` (nunca lanza excepciones) y
  `fallback_classification()`.
- `main.py` importa el **módulo**: `from app import classifier` y llama
  `classifier.classify_ticket(...)`. Nunca `from app.classifier import
  classify_ticket` (los tests sustituyen la función del módulo con `monkeypatch`).

### Capas

| Módulo | Responsabilidad | No puede |
|---|---|---|
| `app/main.py` | Endpoints HTTP y render de plantillas | SQL, SDK de IA |
| `app/models.py` | Modelos Pydantic y enums | Importar FastAPI, db o el SDK |
| `app/db.py` | Todo el SQL con `sqlite3` (siempre con `?`, sin ORM) | Importar FastAPI o el clasificador |
| `app/classifier.py` | Única llamada al LLM | Importar FastAPI |

- Base de datos: `DATABASE_URL` (`sqlite:///ruta`) → `TRIAGEBOT_DB` →
  `triagebot.db`, resuelta en cada conexión. Una conexión por operación.
- No añadir dependencias fuera de `requirements.txt` sin acordarlo.

### Tests

- **No se crea ni se modifica ningún `test_*.py`** salvo que el equipo lo pida.
- pytest es el motor (viene en `requirements.txt`); `tests/test_acceptance.py`
  es lo que el motor lee: busca archivos `test_*.py` y ejecuta las funciones
  `test_*()`. Cada `assert` es una condición: verdadera → pasa; falsa → falla y
  pytest muestra qué esperaba y qué recibió.
- Ningún test llama al LLM real.

### Flujo de trabajo

- Antes de cada commit: `ruff check .` y `pytest -v` en verde.
- Commits pequeños (cada 20–30 min). Nunca `.env` ni API keys.

### Windows / PowerShell

- Venv con Python 3.13: `py -3.13 -m venv .venv` (con 3.14 falla `pydantic-core`).
- Activar: `.venv\Scripts\activate`.
- En PowerShell no existen `grep`, `cp` ni `source`: usar `Select-String`,
  `Copy-Item` y `.venv\Scripts\activate`.

## Comandos útiles

```bash
pytest -v
pytest --cov=app
ruff check .
uvicorn app.main:app --reload
```
