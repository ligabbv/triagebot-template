# TriageBot — Especificación funcional completa (referencia, Equipo B)

> **Documento de trabajo del Equipo B.** No está en el repo: lo recibís a mitad
> del Lab 1 como referencia. Vosotros habéis empezado a escribir vuestra spec a
> partir de `BRIEF.md`; esto es el patrón con el que **reconciliarla**, no un
> reemplazo. Comparad la vuestra con esta, adoptad lo que mejore vuestra versión
> y conservad lo vuestro que ya esté bien. Las decisiones que toméis distintas a
> esta, apuntadlas en la sección 13.

---

## 1. Objetivo

TriageBot es una aplicación web interna donde un usuario crea **tickets**
(incidencias descritas en lenguaje natural) y el sistema, vía LLM, los
**clasifica automáticamente** por categoría, prioridad y tags. El usuario puede
consultar, filtrar y actualizar los tickets desde un tablero web.

El proyecto debe estar **funcionando end-to-end** al final del Día 2: backend,
base de datos, integración con LLM, frontend mínimo, tests verdes y CI verde en
GitHub Actions.

**Usuario:** el equipo de soporte de Marta Iturri. No hay login: es una
herramienta interna de un solo espacio compartido.

---

## 2. Stack técnico (innegociable)

| Capa | Tecnología |
|------|------------|
| Lenguaje | Python 3.11+ |
| Framework web | FastAPI + uvicorn |
| Modelos / validación | Pydantic (v2) |
| Persistencia | SQLite con el módulo estándar `sqlite3` (archivo local `triagebot.db`). **Sin ORM**: nada de SQLModel ni SQLAlchemy |
| Plantillas | Jinja2 (`templates/`) |
| Frontend | HTMX + Tailwind CSS por CDN (sin build tools) |
| LLM | `openai/gpt-oss-120b` vía OpenRouter, con el SDK de OpenAI (`openai`) |
| Configuración | `.env` cargado con `python-dotenv` |
| Tests | pytest + `fastapi.testclient.TestClient` |
| Lint | ruff |
| CI | GitHub Actions (`.github/workflows/ci.yml`, incluido en el repo) |

No se permite cambiar de framework ni de base de datos, añadir un ORM ni build
tools (webpack, vite…). Todas las dependencias van **fijadas** en
`requirements.txt` (`paquete==versión`); no se añade ninguna sin acordarlo en el
equipo y anotarlo en la sección 12.

---

## 3. Arquitectura y estructura de módulos

```
app/
├── __init__.py
├── main.py          # FastAPI: app, endpoints y render de plantillas. Nada de SQL ni de LLM aquí.
├── models.py        # Modelos Pydantic (entrada, salida) y enums.
├── db.py            # Toda la persistencia con sqlite3: init, insert, get, list, update.
├── deadlines.py     # Fecha límite y vencidos (§4.1). Funciones puras: sin BD, FastAPI ni LLM.
└── classifier.py    # La ÚNICA llamada al LLM. Expone classify_ticket().
templates/
├── index.html             # Página completa (GET /). Incluye los bloques del §7
├── _filtros.html          # Bloques 2 y 3: los 3 filtros + el contenedor #tablero
└── _tickets_table.html    # Fragmento: la tabla (lo reutilizan GET / y GET /tickets/tablero)
tests/
└── test_acceptance.py   # Obligatorio. NO se modifica.
```

Reglas de dependencia (quién puede importar a quién):

- `main.py` → `models`, `db`, `classifier`.
- `db.py` → `models`. Nunca importa FastAPI ni el clasificador.
- `classifier.py` → `openai`, `models`. **Nunca importa FastAPI** (si veis
  `from fastapi import …` en `classifier.py`, algo se ha torcido).

> **Regla importante:** todo lo que llame a cualquier API de IA (OpenRouter,
> Anthropic, etc.) vive **solo** en `app/classifier.py`: el import del SDK, la
> `base_url`, el modelo, el prompt, la lectura de `OPENROUTER_API_KEY`, el
> reintento y el parseo. Si veis llamadas al SDK en `main.py` (o en cualquier
> otro módulo), hay un problema. Comprobación rápida (PowerShell):
> `Select-String -Path app\*.py -Pattern "openai|openrouter|anthropic"` solo debe
> devolver líneas de `classifier.py`.

**Red de seguridad en `main.py`:** aunque el clasificador nunca lanza
excepciones, `main.py` envuelve la llamada en un `try/except Exception` genérico
y, si algo falla (o la salida no valida contra `models.Classification`), usa
`classifier.fallback_classification()`. No es lógica de IA: no importa el SDK
ni captura tipos del SDK. La exige el test 4, que sustituye el clasificador por
una función que lanza `RuntimeError`.

**Cómo llama `main.py` al clasificador (importante para los tests):** los tests
sustituyen `app.classifier.classify_ticket` con `monkeypatch`. Para que esa
sustitución surta efecto, `main.py` debe llamarlo **a través del módulo**:

```python
from app import classifier          # ✅
...
result = classifier.classify_ticket(title, description)
```

y **no** con `from app.classifier import classify_ticket` (❌: guarda una
referencia a la función original y el mock no se aplica, con lo que los tests
llamarían al LLM de verdad).

---

## 4. Modelo de datos

Una sola entidad: `Ticket`.

| Campo | Tipo | Notas |
|-------|------|-------|
| `id` | int | Primary key, autoincremental |
| `title` | str | Obligatorio. Se aplica `strip()`; tras él, longitud 1–200 |
| `description` | str | Obligatorio. Se aplica `strip()`; tras él, longitud 1–5000 |
| `category` | str | Uno de: `bug`, `feature_request`, `question`, `urgent` |
| `priority` | str | Uno de: `P1`, `P2`, `P3` |
| `tags` | list[str] | Lista (puede estar vacía). Máx. 5 tags, cada uno máx. 30 chars, en minúscula y sin duplicados |
| `status` | str | Uno de: `open`, `in_progress`, `closed`. Default: `open` (ver §4.2) |
| `assignees` | list[str] | Responsables del ticket (puede estar vacía = "Sin asignar"). Ver "Responsables" abajo |
| `created_at` | datetime | UTC, generado en servidor |
| `updated_at` | datetime | UTC. Igual a `created_at` al crear; se actualiza en cada `PATCH` |
| `due_at` | datetime | **Calculado, no se guarda.** Fecha límite según la prioridad (ver §4.1) |
| `overdue` | bool | **Calculado, no se guarda.** `true` si el ticket va vencido (ver §4.1) |

`category`, `priority` y `tags` los rellena el clasificador (sección 6) en el
momento de crear el ticket. `due_at` y `overdue` aparecen en **todas** las
respuestas JSON de ticket (`POST`, `GET`, `PATCH`), pero no son columnas de la BD.

### 4.1 Fecha límite (`due_at`) y vencidos (`overdue`)

La fecha límite depende de la prioridad y se cuenta desde el día de creación
(`created_at`), hasta el final de ese día (23:59:59 UTC):

| Prioridad | Plazo | Ejemplo (creado el 24/11 a las 10:00) |
|-----------|-------|----------------------------------------|
| `P1` | Hoy mismo | `due_at` = 24/11 23:59:59 |
| `P2` | Mañana | `due_at` = 25/11 23:59:59 |
| `P3` | Un par de días | `due_at` = 26/11 23:59:59 |

- `overdue = true` si **ahora > `due_at`** y el ticket **no está resuelto**
  (`status != "closed"`). Un ticket resuelto nunca cuenta como vencido.
- Se calcula en cada respuesta, no se guarda: si alguien cambia la prioridad con
  `PATCH`, la fecha límite se recalcula sola (desde el `created_at` original).
- La lógica vive en `app/deadlines.py` (funciones puras, sin BD ni FastAPI).

### 4.2 Ciclo de vida del estado

Los valores de la API **no cambian** (`open`, `in_progress`, `closed`); en el
tablero se muestran como Abierto, En curso y **Resuelto**:

```text
  Abierto ──Empezar──▶ En curso ──Resolver──▶ Resuelto
     │                                           │
     └──────────────Resolver──────────────▶      │
     ▲                                           │
     └───────────────────Reabrir─────────────────┘
```

| Estado actual | Acciones en el tablero | `PATCH` que envían |
|---------------|------------------------|--------------------|
| Abierto (`open`) | Empezar · Resolver | `{"status": "in_progress"}` · `{"status": "closed"}` |
| En curso (`in_progress`) | Resolver | `{"status": "closed"}` |
| Resuelto (`closed`) | Reabrir | `{"status": "open"}` |

La API **no impone** este flujo: `PATCH` acepta cualquier estado válido (lo
necesitan el test de aceptación 5 y el agente del Día 3). El flujo lo guía el
tablero, que solo ofrece los botones de la tabla. Un ticket reabierto conserva su
`created_at`, así que si ya pasó su plazo aparece como vencido.

### Responsables (`assignees`)

Un ticket puede tener **varios responsables** (técnicos trabajando a la vez en él).
No hay usuarios ni login (fuera de alcance): un responsable es un **nombre en texto
libre**.

- Se aceptan como lista JSON (`["Ana", "Luis"]`) o como texto separado por comas
  (`"Ana, Luis"`, lo que envía el formulario web).
- Normalización: `strip()`, espacios internos repetidos → uno, se descartan los
  vacíos y los duplicados **sin distinguir mayúsculas** (`"ana"` y `"Ana"` son la
  misma persona; se conserva la primera escritura). Se mantiene el orden.
- Límites: máx. **10** responsables por ticket y máx. **50** caracteres por nombre.
  Si se superan → `422` (no se recorta en silencio: es dato del usuario, no del LLM).
- Por defecto `[]` ("Sin asignar"). El clasificador **no** los toca.

> **Los enums son vinculantes.** Si vuestro código devuelve `"URGENT"` en
> mayúsculas o un valor fuera de la lista, los tests fallan.

### Esquema SQLite

```sql
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    priority    TEXT    NOT NULL,
    tags        TEXT    NOT NULL DEFAULT '[]',   -- lista serializada con json.dumps
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,               -- ISO 8601 UTC, p. ej. 2026-06-29T09:30:00Z
    updated_at  TEXT    NOT NULL
);

-- Relación N:M ticket ↔ responsable (una fila por responsable asignado).
CREATE TABLE IF NOT EXISTS ticket_assignees (
    ticket_id    INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    assignee     TEXT    NOT NULL,   -- nombre tal y como se escribió ("Iñigo")
    assignee_key TEXT    NOT NULL,   -- clave para comparar: casefold() en Python ("iñigo")
    position     INTEGER NOT NULL,   -- orden en que se asignaron
    PRIMARY KEY (ticket_id, assignee_key)
);
```

- Los responsables van en tabla propia (no en una columna JSON como `tags`): así
  filtrar por responsable es SQL normal con `?`, y una base de datos ya existente
  solo gana la tabla nueva con `CREATE TABLE IF NOT EXISTS`, sin migración. Los
  tickets antiguos quedan "Sin asignar".
- Se compara por `assignee_key` (calculada con `casefold()` en Python) y no con
  `COLLATE NOCASE`: el de SQLite solo ignora mayúsculas en ASCII, así que
  `"IÑIGO"` no encontraría a `"Iñigo"`.

- La tabla se crea al arrancar la app (`db.init_db()` en el `lifespan` de
  FastAPI) **y también** con `CREATE TABLE IF NOT EXISTS` en cada conexión: los
  tests usan `TestClient(app)` sin `with`, así que en ellos no se ejecuta el
  arranque.
- La ruta del archivo se resuelve **en cada conexión** (no al importar), en este
  orden: `DATABASE_URL` (formato `sqlite:///ruta`, es la que fijan los tests) →
  `TRIAGEBOT_DB` → `triagebot.db`. Así cada test usa su propia base temporal.
- **Todas** las consultas usan parámetros (`?`), nunca f-strings ni
  concatenación de SQL.
- Una conexión por operación (abrir, ejecutar, `commit`, cerrar). Nada de
  conexiones globales compartidas entre hilos.

---

## 5. Endpoints HTTP

Siete endpoints. Todos viven en `app.main`. **JSON y HTML están separados por ruta**,
cada uno con un propósito claro (no se decide por cabeceras):

| Ruta | Devuelve | Para qué sirve |
|------|----------|----------------|
| `GET /tickets` (y `POST`, `GET/PATCH /tickets/{id}`) | **JSON** | La API: tests, scripts y el agente del Día 3 |
| `GET /tickets/stats` | **JSON** | Conteos para informes (total, por categoría, prioridad y estado) |
| `GET /tickets/tablero` | **HTML** (solo la tabla) | Lo que pide HTMX para refrescar el tablero |
| `GET /` | **HTML** (página completa) | Lo que abre el usuario en el navegador |

Los tests usan siempre JSON.

### `POST /tickets`

Crea un ticket nuevo y lo clasifica. **La clasificación ocurre síncronamente
durante la petición**: la respuesta ya trae el ticket clasificado.

**Request body** (JSON):
```json
{
  "title": "La página de login no carga",
  "description": "Al pulsar el botón de login, sale un error 500 del servidor.",
  "assignees": ["Ana", "Luis"]
}
```

`assignees` es **opcional** (por defecto `[]`); ver reglas en la sección 4.

**Response `201 Created`**:
```json
{
  "id": 1,
  "title": "La página de login no carga",
  "description": "Al pulsar el botón de login, sale un error 500 del servidor.",
  "category": "bug",
  "priority": "P1",
  "tags": ["login", "error_500", "backend"],
  "status": "open",
  "assignees": ["Ana", "Luis"],
  "created_at": "2026-06-29T09:30:00Z",
  "updated_at": "2026-06-29T09:30:00Z"
}
```

**Errores**:
- `422 Unprocessable Entity` si `title` o `description` faltan, son vacíos o
  solo espacios, o exceden su longitud máxima; o si `assignees` incumple sus
  límites (más de 10, o un nombre de más de 50 caracteres). Se usa el formato estándar de
  FastAPI (`{"detail": [...]}`).

**Orden de operaciones:** validar → clasificar → guardar → responder. Si la
validación falla, **no se llama al LLM** (no se gastan tokens en basura).

> El `POST /tickets` **nunca** devuelve `5xx` por un fallo del LLM. Si el LLM
> falla, el clasificador aplica el fallback (sección 6) y el endpoint devuelve
> `201` igualmente.

**Duplicados:** dos `POST` idénticos seguidos crean **dos** tickets. Es una
decisión consciente (pueden ser dos clientes con el mismo problema). No se
deduplica.

### `GET /tickets`

Devuelve la lista de tickets, ordenada por `created_at` descendente (y por `id`
descendente a igualdad).

**Query params opcionales** (combinables, filtran por igualdad exacta):
`category`, `priority`, `status`, `assignee` y `overdue` (`true` = solo vencidos,
`false` = solo no vencidos; ver §4.1).

```text
GET /tickets?category=bug&priority=P1&status=open
GET /tickets?assignee=Ana
GET /tickets?overdue=true
```

- `assignee` (un solo nombre): devuelve los tickets que **tienen a esa persona entre
  sus responsables**, aunque tengan más. Sin distinguir mayúsculas; un valor vacío
  significa "sin filtro".

- **Response `200 OK`**: lista JSON de tickets (lista vacía `[]` si no hay).
- **Response `422`**: si un filtro trae un valor fuera de su enum
  (p. ej. `?priority=P9`) o `overdue` no es booleano.

### `GET /tickets/stats`

Conteos de **todos** los tickets, para informes (petición de Marta para la
presentación interna). Solo JSON, sin frontend. Sin parámetros.

**Response `200 OK`**:
```json
{
  "total": 47,
  "by_category": {"bug": 18, "feature_request": 12, "question": 10, "urgent": 7},
  "by_priority": {"P1": 5, "P2": 20, "P3": 22},
  "by_status": {"open": 30, "in_progress": 9, "closed": 8}
}
```

- Cada diccionario trae **siempre todas las claves** de su enum, en el orden del
  enum, con `0` si no hay ninguno: quien lo consuma no tiene que comprobar si
  falta una clave. Sin tickets → `total: 0` y todo a `0`.
- La suma de cada diccionario es igual a `total`.
- Los conteos los hace SQLite (`COUNT(*) ... GROUP BY`) en `db.py`, sin cargar los
  tickets en memoria.

> Como `GET /tickets/tablero`, debe declararse **antes** que `GET /tickets/{id}`.

### `GET /tickets/tablero`

Devuelve **HTML**, no JSON: solo el fragmento de la tabla (`_tickets_table.html`),
pensado para que HTMX lo inserte en `#tablero`. El JSON de los mismos datos es
`GET /tickets`.

**Query params opcionales** (combinables): `category`, `priority`, `status` y
`assignee` (mismo significado que en `GET /tickets`), y `vencidos` (`vencidos=1` =
solo vencidos; lo envía la casilla "Solo vencidos"). Un valor **vacío** (la
opción "Todos" de un `<select>` envía `category=`) significa "sin filtro", **no**
es un error.

```text
GET /tickets/tablero?category=bug&priority=&status=open
GET /tickets/tablero?vencidos=1
GET /tickets/tablero?assignee=Ana
```

- **Response `200 OK`**: `text/html` con el aviso de vencidos y la tabla; si no
  hay resultados, una fila "No hay tickets.".
- **Response `422`**: si un filtro trae un valor no vacío fuera de su enum
  (p. ej. `?category=URGENT`).
- **Cabecera `HX-Push-Url`**: la respuesta indica a HTMX la URL de la página
  completa con los filtros (`/?category=bug&status=open`, o `/` sin filtros), para
  que **al refrescar el navegador no se pierdan**.

> **Orden de rutas:** `GET /tickets/tablero` debe declararse **antes** que
> `GET /tickets/{id}` en `main.py`. Si no, FastAPI intenta convertir `"tablero"`
> a entero y devuelve `422`.

### `GET /tickets/{id}`

- **Response `200 OK`**: ticket JSON.
- **Response `404 Not Found`**: si no existe → `{"detail": "Ticket not found"}`.
- **Response `422`**: si el id no es un entero (`/tickets/abc`). Lo hace FastAPI
  solo al tipar el parámetro como `int`.

### `PATCH /tickets/{id}`

Actualiza **solo** `status`, `priority` y/o `assignees`. El resto de campos son
inmutables tras la creación.

**Request body** (cualquier combinación de los tres campos):
```json
{ "status": "in_progress", "priority": "P2" }
{ "assignees": ["Ana", "Luis", "Marta"] }
```

- `assignees` **sustituye** la lista completa (no añade). `[]` deja el ticket sin
  asignar. Mismas reglas y límites que al crear.

- **Response `200 OK`**: ticket actualizado, con `updated_at` renovado (y
  `due_at`/`overdue` recalculados).
- Cualquier transición entre estados válidos está permitida (el flujo
  Empezar → Resolver → Reabrir lo guía el tablero, ver §4.2).
- **Response `404 Not Found`**: si no existe.
- **Response `422`**: si algún valor no pertenece al enum permitido, si el body
  viene vacío (`{}`) o si trae campos no permitidos (`title`, `category`…).

### `GET /`

Renderiza `index.html` con el tablero (sección 7). **Devuelve HTML, no JSON**
— no confundir con `GET /tickets`.

Acepta los mismos filtros opcionales que `GET /tickets/tablero`
(`/?category=bug&status=open`) y los aplica al renderizar: la tabla sale ya
filtrada y los `<select>` aparecen con su valor seleccionado. Así, al refrescar
(F5) o compartir la URL, los filtros se conservan. `422` si algún valor no vacío
no es válido.

---

## 6. El módulo clasificador (`app/classifier.py`)

Encapsula toda la lógica de IA. **Es el único módulo que llama al LLM.** El
resto del código depende solo de su contrato.

### Contrato público

```python
def classify_ticket(title: str, description: str) -> dict:
    """
    Clasifica un ticket usando el LLM.

    Devuelve SIEMPRE un dict con esta forma exacta (nunca None, nunca excepción):
    {
        "category": "bug" | "feature_request" | "question" | "urgent",
        "priority": "P1" | "P2" | "P3",
        "tags": list[str]  # máx. 5 elementos, cada uno máx. 30 chars
    }
    """
```

### Llamada al modelo

- SDK de OpenAI apuntando a OpenRouter:
  `base_url="https://openrouter.ai/api/v1"`, `api_key` leída de
  `OPENROUTER_API_KEY`.
- Modelo: `openai/gpt-oss-120b` (formato `proveedor/modelo`, **sin** prefijo
  `openrouter/`), definido como constante al principio del módulo.
- La key se lee **dentro de la función** (o al crear el cliente bajo demanda),
  **no** al importar el módulo: en CI no hay key y el import no puede romper.
  Si no hay key → fallback directamente, sin llamar.
- `.env` se carga con `load_dotenv()` en `app/main.py`, antes de cualquier uso.
- Parámetros: `temperature=0`, `max_tokens=1024`, `timeout=20` segundos.
- La respuesta está en `response.choices[0].message.content`.

### Prompt

El prompt vive en `app/classifier.py` (`SYSTEM_PROMPT` y `FEW_SHOT_EXAMPLES`) y se
envía así:

1. Mensaje `system` con criterios explícitos, sacados del histórico de tickets
   (`seed_tickets.json`):
   - `urgent` (siempre P1) si hay **impacto masivo** (equipo, planta, almacén o
     clientes), un **incidente de seguridad**, **pérdida de datos compartidos**, o
     **una persona bloqueada con plazo inminente o un cliente esperando** (decisión
     del equipo, 2026-11-24).
   - `bug` aunque venga redactado como pregunta si algo existente está roto.
   - `feature_request` para "sería útil / propuesta / permitir…", aunque describa
     una molestia. Siempre P3.
   - `question` para dudas de uso, procedimiento o política sin nada roto. P3,
     salvo seguridad o fecha cercana (P2).
   - P1 en `bug` si impide trabajar del todo, pierde datos o genera importes
     erróneos a clientes. **P2 es la prioridad por defecto de un `bug`**, sobre todo
     si falla un servicio compartido (correo, Teams, intranet, CRM…) aunque sea en
     parte o vaya lento. P3 solo si es estético o menor, o si el propio texto dice
     que hay una alternativa que funciona. En caso de duda entre P2 y P3, P2.
   - `tags`: primero el sistema afectado con un vocabulario fijo (`vpn`, `crm`,
     `erp`, `correo`, `intranet`, `fichaje`, `gastos`, `seguridad`…), después 1–2
     tags concretos. Nunca "error", "problema" o "nada".
2. **13 ejemplos** del histórico como pares `user`/`assistant` (*few-shot*), elegidos
   para cubrir los casos dudosos de cada categoría. Son los marcados con
   `ejemplo_prompt: true` en `data/tickets_etiquetados.json`.
3. El ticket a clasificar como último mensaje `user`:
   `Título: {title}\nDescripción: {description}`.

Tamaño: unos 1.700 tokens de entrada por ticket (antes, unos 200). Enviar los 140
tickets del histórico en cada llamada serían unos 11.000 y, al no estar etiquetados,
no enseñarían el criterio.

**Histórico etiquetado y evaluación.** `data/tickets_etiquetados.json` contiene los
140 tickets de `seed_tickets.json` con su `category` y `priority` de referencia
(48 bug, 41 question, 37 feature_request, 14 urgent).
`scripts/evaluar_clasificador.py` clasifica con el LLM real los que no son ejemplos
del prompt y muestra el porcentaje de aciertos, la matriz de confusión y los
fallos. **No es un test**: gasta tokens y necesita `OPENROUTER_API_KEY`. Se lanza a
mano antes de dar por bueno cualquier cambio del prompt.

Resultados (2026-11-24, 127 tickets, `openai/gpt-oss-120b`):

| Prompt | Categoría | Prioridad | Las dos | Urgentes detectados |
|--------|-----------|-----------|---------|---------------------|
| Original (una línea de criterios) | 97,6 % | 72,4 % | 72,4 % | 10 de 11 |
| Criterios + 13 ejemplos | 97,6 % | 90,6 % | 89,0 % | 11 de 11 |
| + regla "P2 por defecto en bug" (actual) | 98,4 % | 93,7 % | 92,9 % | 11 de 11 |

Los fallos que quedan van casi todos hacia **más** prioridad (un `bug` menor como
P2 en vez de P3), que es el error menos dañino. Las etiquetas de referencia las
puso el equipo con estos mismos criterios, y la regla del P2 se ajustó viendo los
fallos de la ronda anterior, así que las cifras son algo optimistas: para validar
de verdad hacen falta tickets nuevos etiquetados por Soporte.

### Parseo y validación de la salida

1. Si el texto viene envuelto en ```` ```json … ``` ````, quitar el envoltorio.
2. Extraer el primer objeto `{…}` del texto y parsearlo con `json.loads`.
3. Validar con un modelo Pydantic:
   - `category` y `priority` deben estar **exactamente** en sus enums
     (`"URGENT"` o `"Bug"` → inválido; no se "arreglan" a mano).
   - `tags`: si falta → `[]`. Si no es lista → inválido. Se normalizan: minúscula,
     `strip()`, recorte a 30 chars, sin vacíos ni duplicados, máximo 5.
4. Si cualquier paso falla, la respuesta se considera **inválida**.

### Reintento y fallback

- Si la llamada lanza excepción (timeout, 5xx, 429, error de autenticación) **o**
  la respuesta es inválida → **un** reintento. No más: se gastan tokens y
  latencia sin garantía.
- Si el reintento también falla → **fallback**:

```python
{"category": "question", "priority": "P3", "tags": []}
```

- Cada fallback se registra con `logging.warning` indicando la causa (excepción
  o salida inválida). **Nunca** se registra la API key.
- Nunca se propaga una excepción del SDK al endpoint.

---

## 7. Frontend mínimo

Una sola página HTML servida en `GET /` con `templates/index.html`. Debe
contener:

1. **Formulario** con los campos `title`, `description` y `assignees`
   ("Responsables", opcional, nombres separados por comas) y un botón "Crear
   ticket".
   - Envío con HTMX: `hx-post="/tickets"`, `hx-ext="json-enc"` (la extensión
     json-enc de HTMX, por CDN, para enviar JSON y reutilizar el mismo
     endpoint que usan los tests), `hx-target` = cuerpo de la tabla.
   - El botón se desactiva mientras se clasifica (`hx-disabled-elt`) y se
     muestra un indicador "Clasificando…" (`hx-indicator`): la llamada al LLM
     tarda unos segundos.
   - Tras crear, el formulario se limpia.
   - Si el backend devuelve `422`, se muestra un mensaje legible junto al
     formulario, no un JSON crudo.
2. **Tablero** con la lista de tickets, mostrando: `id`, `title`, `category`
   (color según valor), `priority` (badge: P1 rojo, P2 ámbar, P3 gris), `tags`
   (chips), `status`, `created_at` (formato `dd/mm/aaaa hh:mm`, hora UTC).
   - Todo el texto visible está **en castellano**. Los valores internos de la API
     no cambian (`bug`, `P1`, `open`…); solo se traducen al mostrarlos:

     | Campo | Valores mostrados |
     |-------|-------------------|
     | `category` | `bug` → Error · `feature_request` → Mejora · `question` → Duda · `urgent` → Urgente |
     | `priority` | `P1` → P1 · Alta · `P2` → P2 · Media · `P3` → P3 · Baja |
     | `status` | `open` → Abierto · `in_progress` → En curso · `closed` → Resuelto |

   - Cabeceras: ID, Título, Categoría, Prioridad, Etiquetas, Responsables, Estado,
     Vence, Creado (UTC), Acciones.
   - Sin tags se muestra "—". Sin tickets, una fila "No hay tickets.".
   - **Responsables**: un chip por persona, o "Sin asignar". Un "Editar" por fila
     despliega un campo con los nombres separados por comas y un botón "Guardar",
     que hace `PATCH /tickets/{id}` con `assignees` y lanza el evento
     `ticket-actualizado` (el tablero lo escucha igual que `ticket-creado`).
   - **Vencidos de un vistazo** (§4.1):
     - Columna **Vence** con la fecha límite (`dd/mm/aaaa`). Si va vencido, la
       fila se pinta en rojo claro y lleva la etiqueta roja **"Vencido"**.
     - Encima de la tabla, un aviso rojo **"⚠ N tickets vencidos"** con el total
       de vencidos de toda la base (no solo de los filtrados) y un enlace
       "Ver solo vencidos". Si no hay ninguno, el aviso no aparece.
3. **Filtros**: tres `<select>` (`category` / `priority` / `status`), cada uno
   con una opción "Todas"/"Todos", y un campo de texto **Responsable**
   (`assignee`) con sugerencias (`<datalist>`) de los nombres ya asignados, para
   que cada técnico vea lo suyo. Al cambiar cualquiera (en el texto: al salir del
   campo o pulsar Enter), HTMX hace
   `GET /tickets/tablero?category=…&priority=…&status=…&assignee=…` (el formulario
   de filtros envía los cuatro valores) y reemplaza el contenido de `#tablero`. Hay un enlace
   "Limpiar filtros" (`/`). Los filtros activos viven en la URL (ver
   `GET /tickets/tablero` y `GET /`), así que sobreviven a un refresco.
   Además, una casilla **"Solo vencidos"** (`vencidos=1`).
4. **Cambio de estado** desde la tabla: en la columna **Acciones**, botones según
   el estado (§4.2): Abierto → *Empezar* y *Resolver*; En curso → *Resolver*;
   Resuelto → *Reabrir*. Cada botón hace `PATCH /tickets/{id}` con el nuevo
   `status` (JSON, con `json-enc`) y, si va bien, lanza el evento
   `ticket-actualizado`; `#tablero` lo escucha y se recarga respetando los
   filtros. Si el `PATCH` falla, se muestra un aviso en castellano.
   - `#tablero` lleva `hx-disinherit="*"`: sin eso, los botones heredarían su
     `hx-include="#filtros"` y el `PATCH` enviaría también los campos de los
     filtros (`422` por campos no permitidos).
5. **Estado vacío**: si no hay tickets (o el filtro no devuelve nada), una fila
   con "No hay tickets".

### 7.1 Contrato entre el formulario (bloque 1) y el tablero (bloques 2 y 3)

El formulario y el tablero se desarrollan por separado y se conectan **solo por un
evento HTMX**:

- El contenedor `#tablero` (en `_filtros.html`) escucha
  `hx-trigger="ticket-creado from:body, ticket-actualizado from:body"` y, al recibirlo, hace
  `GET /tickets/tablero` incluyendo los filtros activos (`hx-include="#filtros"`).
- **Quien cree un ticket debe lanzar el evento `ticket-creado`** cuando el
  `POST /tickets` responda `201`; por ejemplo con
  `hx-on::after-request="if (event.detail.successful) htmx.trigger('body', 'ticket-creado')"`
  en el `<form>`. Sin esto, el ticket se guarda pero la lista no se actualiza hasta
  recargar.
- El formulario **no** debe usar `#tablero` como `hx-target`: no sabe nada de los
  filtros ni de la tabla, solo avisa de que hay un ticket nuevo.

Reglas:

- Todo el HTML vive en `templates/`. Nada de HTML grande como string en
  `main.py`.
- Jinja2 con **autoescape activado** (lo está por defecto con
  `Jinja2Templates`). Nunca usar `|safe` con datos del usuario: un ticket con
  `<script>` en la descripción se muestra como texto.
- No se exige diseño espectacular: la gracia es que sea funcional.

---

## 8. Configuración y secretos

- `.env` (no se commitea) con `OPENROUTER_API_KEY=sk-or-...`.
- `.env.example` (sí se commitea) con la misma variable sin valor real.
- Variables opcionales: `DATABASE_URL` (`sqlite:///ruta`, tiene prioridad) o
  `TRIAGEBOT_DB` (ruta de la base de datos).
- Arranque: `uvicorn app.main:app --reload` → `http://localhost:8000`.

---

## 9. Tests

### 9.1 Tests de aceptación obligatorios

Están en `tests/test_acceptance.py`. **Este fichero no se modifica.** Son cinco
y todos deben estar verdes al final del Día 2:

| # | Test | Verifica |
|---|------|----------|
| 1 | `test_post_ticket_creates_ticket_with_classification` | `POST /tickets` devuelve `201` con el ticket clasificado (category, priority y tags válidos). |
| 2 | `test_created_ticket_is_persisted_and_listed` | El ticket creado se guarda y aparece en `GET /tickets`. |
| 3 | `test_post_ticket_rejects_invalid_input` | `POST /tickets` con entrada inválida devuelve `422`. |
| 4 | `test_classifier_failure_uses_safe_fallback` | Si el clasificador falla, el ticket se crea igualmente con el fallback. |
| 5 | `test_update_ticket_and_filter_by_status_priority_category` | `PATCH /tickets/{id}` actualiza el ticket y `GET /tickets` filtra por status, priority y category. |

Los tests sustituyen el clasificador con `monkeypatch` para no consumir tokens
en CI (de ahí la regla de importación de la sección 3). **Leed el fichero
entero antes de programar**: los detalles exactos (códigos, campos, mensajes)
los fijan sus `assert`.

### 9.2 Tests propios recomendados (`tests/test_classifier.py`, `tests/test_api.py`)

- El clasificador con respuesta válida del LLM (mockeada) devuelve el dict
  correcto.
- Respuesta envuelta en ```` ```json ```` → se parsea bien.
- `category: "URGENT"` o inventada → fallback.
- El SDK lanza excepción dos veces → fallback, sin excepción hacia fuera.
- Sin `OPENROUTER_API_KEY` → fallback sin llamar al SDK.
- `GET /tickets/99999` → `404`. `GET /tickets/abc` → `422`.
- `PATCH` con `status: "inventado"` → `422`. `PATCH` a id inexistente → `404`.
- `GET /` devuelve `200` con `text/html`.
- `tests/test_deadlines.py`: plazos P1/P2/P3, vencido / no vencido / resuelto
  nunca vencido, `due_at`/`overdue` en la respuesta JSON, flujo
  Abierto → En curso → Resuelto → Abierto, filtro `?overdue=true` y
  `?vencidos=1` en el tablero.

Ningún test propio llama al LLM real.

### 9.3 Cobertura

`pytest --cov=app` debe ejecutarse sin errores; objetivo orientativo ≥ 70 %.

---

## 10. Comportamiento esperado ante el checklist de QA adversarial (Día 2, Lab 5)

| # | Escenario | Comportamiento esperado |
|---|-----------|-------------------------|
| 1 | Title vacío o solo espacios | `422`, no se llama al LLM, no se guarda nada |
| 2 | Title de 5000 caracteres | `422` (máx. 200) |
| 3 | Emojis y Unicode no latino | Se guarda y se muestra tal cual (UTF-8) |
| 4 | HTML/JS en la descripción | Se guarda tal cual y se muestra **escapado**; el JS no se ejecuta |
| 5 | Inyección SQL básica | Se guarda como texto literal; la base de datos no cambia (consultas parametrizadas) |
| 6 | LLM caído (key falsa) | `201` con fallback; aviso en el log |
| 7 | Mismo ticket dos veces | Dos tickets (decisión de la sección 5) |
| 8 | IDs malformados (`-1`, `abc`, `99999999999`) | `404` para enteros inexistentes; `422` para no enteros |
| 9 | `PATCH` con valores inválidos | `422` sin modificar el ticket |
| 10 | 20 POSTs concurrentes | 20 tickets creados, sin `500` ni errores de base de datos bloqueada |

---

## 11. Criterios de aceptación finales (entrega del Día 2)

- [ ] Repo en GitHub con todos los commits del equipo.
- [ ] Los 5 tests obligatorios verdes (`pytest`).
- [ ] CI verde en GitHub Actions (ruff + pytest en el último commit de `main`).
- [ ] `main` protegida: solo se mergea vía PR con CI verde.
- [ ] App arranca con `uvicorn app.main:app --reload` y funciona en
      `http://localhost:8000`.
- [ ] Se puede crear un ticket por web y verlo en el tablero; los filtros funcionan.
- [ ] El clasificador devuelve resultados sensatos para al menos 3 tickets
      distintos del `seed_tickets.json` durante la demo.
- [ ] `README.md` actualizado con instrucciones de arranque y las decisiones
      de la sección 12.

### Fuera de alcance

Autenticación y usuarios, paginación, borrado de tickets, edición de título o
descripción, reclasificación manual, despliegue en la nube, notificaciones.

---

## 12. No negociable

- No commitear `.env`.
- No hardcodear API keys (leer de `OPENROUTER_API_KEY`).
- No propagar excepciones del SDK del LLM al endpoint.
- No modificar los tests de aceptación para hacerlos pasar.
- No introducir React, ORM ni build tools.
- No mergear a `main` con la CI en rojo.

---

## 13. Decisiones del equipo

Todo lo que la spec no cubre, o que decidáis hacer distinto, se anota aquí (y en
el README) con fecha y motivo. Ejemplo:

| Fecha | Decisión | Motivo |
|-------|----------|--------|
| 2026-11-23 | La BD se resuelve con `DATABASE_URL` → `TRIAGEBOT_DB` → `triagebot.db` en cada conexión, y la tabla se crea en cada conexión además de en el arranque. | El fixture de `tests/test_acceptance.py` fija `DATABASE_URL` después de importar la app y usa `TestClient` sin `with`. Sin esto todos los tests comparten BD y el test 5 (`len == 1`) falla. |
| 2026-11-23 | `main.py` envuelve `classifier.classify_ticket()` en un `try/except Exception` y valida su salida con `models.Classification`; ante fallo usa `classifier.fallback_classification()`. | El test 4 sustituye el clasificador por uno que lanza `RuntimeError`. Es una red genérica: la IA sigue viviendo solo en `classifier.py`. |
| 2026-11-23 | El cliente OpenAI se crea con `max_retries=0` y el reintento (uno solo) lo hace nuestro código. | El SDK reintenta 2 veces por defecto; superaría el máximo de un reintento del §6. |
| 2026-11-23 | Se envía `extra_body={"reasoning": {"effort": "low"}}` a OpenRouter. | `gpt-oss-120b` es un modelo de razonamiento: con `max_tokens=1024` el razonamiento podría consumir todo el presupuesto y dejar la respuesta vacía (→ fallback). |
| 2026-11-23 | Fase backend: sin tests propios. **Lab 2:** se añade `tests/test_classifier.py` (§9.2) con un cliente de IA falso; `tests/test_acceptance.py` sigue sin tocarse. | El Lab 2 exige al menos un test del módulo clasificador. Los tests de aceptación sustituyen el clasificador entero, así que no lo prueban. |
| 2026-11-23 | El backend inicial (`7f0a142`) se subió directo a `main`. **Desde el Lab 2** se trabaja en ramas y se integra con PR a `main` con CI verde. | Requisito del Lab 2 y del §11. |
| 2026-11-23 | En Windows se usa Python 3.13 para el venv (`py -3.13 -m venv .venv`). | Con Python 3.14 `pydantic==2.10.4` no tiene wheel de `pydantic-core` y la instalación intenta compilar Rust y falla. La CI usa 3.11. |
| 2026-11-24 | **Frontend repartido en dos ramas:** `frontend-tablero-filtros` (bloques 2 y 3: tablero y filtros) y otra rama con el formulario (bloque 1). Se integran con PR a `main`. El cambio de estado por fila (§7.4) queda pendiente. | Trabajar en paralelo sin pisarse. Cada bloque vive en su propia plantilla para reducir conflictos en `index.html`. |
| 2026-11-24 | **JSON y HTML separados por ruta:** `GET /tickets` → JSON, `GET /tickets/tablero` → HTML. Se elimina la idea de decidir por la cabecera `HX-Request` (§5 original). | Propósito claro por endpoint; los tests y el agente del Día 3 solo ven JSON y no dependen de cabeceras. |
| 2026-11-24 | El tablero está en castellano: se traducen solo las etiquetas mostradas (Error/Mejora/Duda/Urgente, Alta/Media/Baja, Abierto/En curso/Cerrado). Los valores de la API no cambian. | Marta y su equipo lo usan en castellano; los enums de la API son vinculantes para los tests. |
| 2026-11-24 | Los filtros viajan por la URL (`/?category=bug`) y `GET /tickets/tablero` responde `HX-Push-Url`. Un filtro vacío ("Todos") se trata como "sin filtro". | Error típico del Lab 4: "mi filtro funciona pero al refrescar la página se pierde". Un `<select>` en "Todos" envía `category=` vacío, que con un enum estricto daría `422`. |
| 2026-11-24 | `created_at` se muestra en UTC (cabecera "Creado (UTC)") con formato `dd/mm/aaaa hh:mm`. | Es la hora que guarda la BD; convertir a hora local requeriría `tzdata` en Windows (dependencia nueva). |
| 2026-11-24 | **Fecha límite por prioridad** (P1 hoy, P2 mañana, P3 en dos días, hasta las 23:59:59 UTC) y **vencidos**. `due_at` y `overdue` se **calculan** en cada respuesta, no se guardan en la BD. | Petición de Marta: "saber cuándo se me echa el tiempo encima". Al calcularlas no hace falta migrar la tabla y siempre cuadran con la prioridad actual. |
| 2026-11-24 | **"Resuelto" = `closed`**: no se añade un estado nuevo; se reutiliza `closed` y se muestra como "Resuelto". Reabrir = volver a `open`. | Petición de Marta: abierto → en curso → resuelto, y reabrir. Los enums de la API son vinculantes (tests, agente del Día 3); cambiar `closed` por `resolved` rompería el contrato. |
| 2026-11-24 | El flujo de estados (§4.2) lo **guía el tablero** con botones; la API sigue aceptando cualquier estado válido. | El test de aceptación 5 pasa de `open` a `in_progress` por `PATCH` y el agente del Día 3 necesitará libertad; un `409` por transición inválida sería un contrato nuevo. |
| 2026-11-24 | **Varios responsables por ticket** (cambio de requisitos del cliente): campo `assignees: list[str]` en texto libre, en tabla propia `ticket_assignees` (N:M). Se asignan al crear (opcional) y se sustituyen con `PATCH`. Filtro `assignee` en `GET /tickets`, `GET /tickets/tablero` y `GET /`, que devuelve los tickets donde esa persona es **una de** sus responsables. Se aceptan como lista JSON o texto separado por comas. | Marta: "muchas veces hay dos o tres técnicos en el mismo ticket" y "que cada técnico vea lo suyo". Texto libre porque no hay usuarios ni login (fuera de alcance). Tabla propia para filtrar con SQL normal y sin migrar la BD existente. El texto con comas evita JavaScript propio en el formulario (`json-enc` envía el campo como string). |
| 2026-11-24 | **Prompt con criterios del histórico y 13 ejemplos (few-shot)** en lugar de enviar los 140 tickets antiguos en cada llamada. Los 140 se etiquetan a mano en `data/tickets_etiquetados.json` y sirven para medir el acierto con `scripts/evaluar_clasificador.py`. Una persona bloqueada con plazo inminente o cliente esperando → `urgent` P1. | Los tickets antiguos no traían etiqueta, así que enviarlos tal cual no enseña el criterio y multiplicaría por ~50 los tokens y la latencia. La regla del plazo inminente la decide el equipo y encaja con el BRIEF ("esto urge porque tenemos demo el viernes"). |
| 2026-11-24 | **`GET /tickets/stats`**: conteos totales, por categoría, por prioridad y por estado, en JSON y sin frontend. Siempre con todas las claves de cada enum (0 si no hay). | Petición de Marta para la presentación interna. Las claves fijas evitan que quien consuma el JSON tenga que comprobar si falta alguna. |
