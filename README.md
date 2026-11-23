# TriageBot Template

Repo plantilla para el bootcamp **Prompt & Commit: Desarrollo de aplicaciones con IA Generativa**.

Durante el bootcamp construiréis una aplicación web interna para clasificar tickets de soporte usando IA generativa.
Hola todos
## Qué vais a construir

TriageBot permite:

- Crear tickets con `title` y `description`.
- Clasificarlos automáticamente con un LLM (gpt-oss-120b vía OpenRouter) en:
  - `category`: `bug`, `feature_request`, `question`, `urgent`
  - `priority`: `P1`, `P2`, `P3`
  - `tags`: lista de etiquetas cortas.
- Persistirlos en SQLite.
- Verlos en un tablero web con filtros.
- Ejecutar tests automáticos y CI en GitHub Actions.

## Stack obligatorio

| Capa | Herramienta |
|---|---|
| Lenguaje | Python 3.11+ |
| Backend | FastAPI |
| Datos | SQLite |
| Frontend | HTML + HTMX + Tailwind CDN |
| LLM | gpt-oss-120b vía OpenRouter (SDK de `openai`) |
| Tests | pytest |
| CI/CD | GitHub Actions |
| IDE + IA | VS Code + Claude Code |

## Setup local

```bash
git clone https://github.com/<tu-usuario>/triagebot-template.git
cd triagebot-template
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

### Windows (PowerShell)

Usa **Python 3.11–3.13**. Con Python 3.14 la instalación falla: `pydantic==2.10.4`
no tiene paquete precompilado de `pydantic-core` para 3.14 e intenta compilarlo
con Rust (`linker link.exe not found`). Comprueba tus versiones con `py --list`
e instala 3.13 si hace falta (`winget install Python.Python.3.13`).

```powershell
cd triagebot-template
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Si al activar aparece "la ejecución de scripts está deshabilitada":
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Edita `.env` y añade tu API key:

```bash
OPENROUTER_API_KEY=sk-or-...
```

Comprueba que `.env` está ignorado por Git:

```bash
git status
```

`.env` **no debe aparecer**.

## Ejecutar tests

Con el venv activado:

```bash
pytest        # ejecuta todos los tests
pytest -v     # los lista uno a uno
```

- **pytest** es el motor: una herramienta externa (viene en `requirements.txt`)
  que busca archivos `test_*.py` y ejecuta las funciones `test_*()`.
- **`tests/test_acceptance.py`** es lo que el motor lee: los 5 tests del curso.
  **No se modifica.**
- Cada test usa `assert`: si la condición es verdadera, pasa (verde, `PASSED`);
  si es falsa, falla (rojo, `FAILED`) y pytest muestra qué esperaba y qué recibió.

Al clonar el repo plantilla, los tests de aceptación fallan: todavía no está
implementado. Con el backend actual deben salir **`5 passed`**. Los tests no
llaman al LLM real (sustituyen el clasificador), así que no gastan tokens ni
necesitan la API key.

Lint (lo ejecuta también la CI):

```bash
ruff check .
```

## Ejecutar la app

```bash
uvicorn app.main:app --reload
```

Abre:

```text
http://127.0.0.1:8000
```

## Contrato mínimo del producto

Los detalles obligatorios están en:

- [`BRIEF.md`](BRIEF.md): briefing del cliente.
- [`SPEC.md`](SPEC.md): contrato funcional recomendado.
- [`CLAUDE.md`](CLAUDE.md): instrucciones del repo para Claude Code.
- [`tests/test_acceptance.py`](tests/test_acceptance.py): los 5 tests obligatorios.

## Reglas del bootcamp

1. Lo que no acaba en GitHub no existe.
2. No se commitean API keys.
3. Commit pequeño cada 20–30 minutos.
4. Leed el diff antes de aceptar cambios de la IA.
5. Si Claude Code propone una dependencia, verificad que existe antes de instalarla.
6. Los tests son la red de seguridad.

## Equipo

Nombres: Imanol, Iñigo

Metodología: B (Spec-Driven)

Las decisiones del equipo que se desvían de la spec están en la
[sección 13 de `SPEC.md`](SPEC.md#13-decisiones-del-equipo).
