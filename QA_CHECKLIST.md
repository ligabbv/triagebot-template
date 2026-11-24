# Checklist de QA adversarial — TriageBot (Día 2, Lab 5)

Plan de pruebas **manuales en local** para los 10 escenarios. El comportamiento
esperado es el de [`SPEC.md` §10](SPEC.md#10-comportamiento-esperado-ante-el-checklist-de-qa-adversarial-día-2-lab-5).
Cada escenario se prueba por **API** (PowerShell) y, cuando aplica, en la **web**.

| # | Escenario | Responsable | Resultado (✅/❌) | Notas |
|---|-----------|-------------|-------------------|-------|
| 1 | Title vacío o solo espacios | Imanol (API, 24/11) | ⚠️ 5/6 | `""`, `"   "`, sin title y description en blanco → 422 y no se guarda nada. **Fallo:** un título solo con espacios invisibles (`​`) se acepta (201) y se gasta una llamada al LLM. |
| 2 | Title de 5000 caracteres | Imanol (API, 24/11) | ✅ 4/4 | 5000 y 201 → 422; 200 → 201; description 5001 → 422. |
| 3 | Emojis y unicode no latino | Imanol (API, 24/11) | ✅ 4/4 | Emojis, chino, árabe, griego, ruso, japonés y responsables `Iñigo`/`Zoë` se guardan y se ven idénticos. |
| 4 | HTML/JS en la descripción (XSS) | Imanol (API, 24/11) | ✅ 3/3 | `<img onerror>`, `<script>`, `<b>` y `javascript:` salen escapados en `GET /`. Pendiente comprobar en el navegador que no salta ningún `alert`. |
| 5 | Inyección SQL básica | Imanol (API, 24/11) | ✅ 6/6 | `DROP TABLE` se guarda como texto; filtros e ids con SQL → 422; filtro responsable `' OR 1=1 --` → 0 resultados. |
| 6 | LLM caído | Imanol (API, 24/11) | ⚠️ 5/6 | Key falsa → 201 `question/P3/[]` en 0,6 s y aviso "fallback tras 2 intentos" (causa 401); sin key → fallback en 0,04 s. La key no sale en el log. **Fallo:** no se ven los logs `INFO` de cada intento (el logging no está configurado). |
| 7 | Mismo ticket dos veces seguidas | Imanol (API, 24/11) | ✅ 1/1 | Dos tickets con ids distintos, como dice el SPEC. Doble clic en la web: pendiente de navegador. |
| 8 | IDs malformados (`-1`, `abc`, `99999999999`) | Imanol (API, 24/11) | ✅ 10/10 | `-1`, `0`, `99999999999` y `99999999999999999999` → 404; `abc`, `1.5` → 422; ningún 500. |
| 9 | PATCH con valores inválidos | Imanol (API, 24/11) | ✅ 12/12 | `inventado`, `CLOSED`, `P9`, `{}`, campos prohibidos, responsables inválidos, `null` y JSON roto → 422, sin modificar el ticket. |
| 10 | 20 POSTs concurrentes | Imanol (API, 24/11) | ✅ 2/2 | Con LLM real: 20×201 en 1,5 s, todos clasificados. Sin LLM: 20×201 en 1,0 s. Sin `database is locked`. |

Si algo falla: apuntadlo en "Notas" con lo que esperabais y lo que salió, y abrid
el arreglo en una rama aparte (no se arregla sobre la marcha en `main`).

**Arreglos:** los dos fallos (escenarios 1 y 6) se corrigen en la rama
`fix/qa-invisibles-y-logs`, con tests de regresión en `tests/test_qa_fixes.py`.
Tras el arreglo, `"​​"` da `422` sin llamar al LLM y el log muestra
`intento 1/2 fallido`, `intento 2/2 fallido` y `fallback tras 2 intentos`.

---

## 0. Preparación (una vez)

**Terminal 1 — arrancar la app con una base de datos de pruebas** (así no
ensuciáis vuestra `triagebot.db`; `*.db` está en `.gitignore`):

```powershell
cd C:\github_curso\triagebot-template
git switch main
git pull
.venv\Scripts\activate
$env:TRIAGEBOT_DB = "qa.db"
uvicorn app.main:app --reload
```

Dejad esta terminal a la vista: ahí salen los **logs** (escenarios 1 y 6).

**Terminal 2 — pegar esta función de ayuda** (enseña código HTTP + respuesta,
también en errores, cosa que `Invoke-RestMethod` no hace en PowerShell 5.1):

```powershell
function Send-Api($Method, $Path, $Body) {
  $p = @{ Method = $Method; Uri = "http://127.0.0.1:8000$Path"; UseBasicParsing = $true
          ContentType = "application/json; charset=utf-8" }
  if ($null -ne $Body) { $p.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress)) }
  try { $r = Invoke-WebRequest @p; "$($r.StatusCode)  $($r.Content)" }
  catch {
    $resp = $_.Exception.Response
    if ($resp) { "$([int]$resp.StatusCode)  $((New-Object IO.StreamReader($resp.GetResponseStream())).ReadToEnd())" }
    else { "SIN RESPUESTA: $($_.Exception.Message)" }
  }
}
function Count-Tickets { (Invoke-RestMethod http://127.0.0.1:8000/tickets).Count }

Send-Api GET /health      # debe dar: 200  {"status":"ok"}
```

**Navegador:** http://127.0.0.1:8000 (y la API interactiva en http://127.0.0.1:8000/docs).

---

## 1. Title vacío o solo espacios

**Esperado:** `422`, **no se llama al LLM** y **no se guarda nada**.

- [ ] API:
  ```powershell
  $antes = Count-Tickets
  Send-Api POST /tickets @{ title = "";    description = "Descripción válida" }   # 422
  Send-Api POST /tickets @{ title = "   "; description = "Descripción válida" }   # 422
  Send-Api POST /tickets @{ description = "Falta el título" }                     # 422
  Send-Api POST /tickets @{ title = "Título válido"; description = "  " }         # 422
  "Antes: $antes  Después: $(Count-Tickets)"                                       # iguales
  ```
- [ ] Logs (terminal 1): **no** aparece ninguna línea `Clasificador:` → no se llamó al LLM.
- [ ] Web: enviar el formulario con el título en blanco (o solo espacios) → mensaje
      legible en castellano junto al formulario, no un JSON; la tabla no cambia.

## 2. Title de 5000 caracteres

**Esperado:** `422` (máx. 200). Comprobar también los bordes.

- [ ] API:
  ```powershell
  Send-Api POST /tickets @{ title = "x" * 5000; description = "ok" }   # 422
  Send-Api POST /tickets @{ title = "x" * 201;  description = "ok" }   # 422 (justo por encima)
  Send-Api POST /tickets @{ title = "x" * 200;  description = "ok" }   # 201 (justo en el límite)
  Send-Api POST /tickets @{ title = "ok"; description = "x" * 5001 }   # 422 (descripción > 5000)
  ```
- [ ] Web: pegar un título muy largo → el campo no deja pasar de 200 (`maxlength`);
      y si se fuerza, mensaje de error legible.
- [ ] El título de 200 caracteres no rompe el diseño de la tabla.

## 3. Emojis y caracteres unicode no latinos

**Esperado:** se guarda y se muestra **tal cual** (UTF-8).

- [ ] API:
  ```powershell
  Send-Api POST /tickets @{ title = "La app peta 😱🔥 ñandú"; description = "中文 · العربية · Ελληνικά · русский · 日本語 ✅" }
  ```
  → `201` y en la respuesta el título/descripción salen **idénticos** (sin `?` ni `Ã±`).
- [ ] `Send-Api GET /tickets/<id>` devuelve exactamente el mismo texto.
- [ ] Web: el ticket aparece en el tablero con emojis y alfabetos bien dibujados.
- [ ] Responsables con tildes/eñes (`"Iñigo"`) se guardan y muestran bien.

## 4. HTML/JS en la descripción (XSS potencial)

**Esperado:** se guarda tal cual y se muestra **escapado**: el JS **no** se ejecuta.

- [ ] API:
  ```powershell
  Send-Api POST /tickets @{ title = "<img src=x onerror=alert('XSS-titulo')>"; description = "<script>alert('XSS')</script>" }
  Send-Api POST /tickets @{ title = "<b>negrita</b> y <a href='javascript:alert(1)'>enlace</a>"; description = "html" }
  ```
  → `201`; el JSON devuelve el texto literal.
- [ ] Web: recargar http://127.0.0.1:8000 → **no salta ningún `alert`**; el título se ve
      como texto (`<img src=x ...>`), no como imagen rota; `<b>` no se pone en negrita.
- [ ] Ver código fuente (Ctrl+U) → aparece escapado: `&lt;img src=x ...&gt;`.
- [ ] Lo mismo con un responsable `<script>alert(1)</script>`.

## 5. Inyección SQL básica

**Esperado:** se guarda como **texto literal**; la base de datos no cambia
(consultas parametrizadas con `?`).

- [ ] API:
  ```powershell
  $antes = Count-Tickets
  Send-Api POST /tickets @{ title = "'); DROP TABLE tickets;--"; description = "' OR '1'='1" }   # 201
  "Antes: $antes  Después: $(Count-Tickets)"                                                       # +1, la tabla sigue viva
  Send-Api GET "/tickets?category=bug' OR '1'='1"     # 422 (no es un valor del enum)
  Send-Api GET "/tickets/1 OR 1=1"                    # 422 (no es un entero)
  Send-Api GET "/tickets/tablero?status=open'--"       # 422
  ```
- [ ] El ticket aparece con el texto literal `'); DROP TABLE tickets;--`.
- [ ] Web: el tablero sigue funcionando y los filtros también. Probar en el filtro
      **Responsable**: `' OR 1=1 --` → no devuelve todos los tickets, solo "No hay tickets".

## 6. LLM caído

**Esperado:** `201` con el **fallback** (`question` / `P3` / `[]`) y un **aviso en el log**;
la app no se cae.

Cómo simularlo (elegid una):

- **A. Key falsa (recomendada):** en `.env` cambiad temporalmente el valor a
  `OPENROUTER_API_KEY=sk-or-v1-clave-falsa` y **reiniciad** uvicorn (Ctrl+C y volver a
  lanzarlo: el `.env` solo se lee al arrancar).
- **B. Sin key:** comentad la línea con `#` en `.env` y reiniciad.
- **C. Sin red:** desactivad el wifi un momento (simula OpenRouter inalcanzable/timeout).

- [ ] API:
  ```powershell
  Send-Api POST /tickets @{ title = "Servidor caído"; description = "Nadie puede entrar desde las 9" }
  ```
  → `201` con `"category":"question","priority":"P3","tags":[]`.
- [ ] Logs: con A o C → `Clasificador: intento 1/2 fallido ...`, `intento 2/2 fallido ...`
      y `Clasificador: fallback tras 2 intentos` (**un solo reintento**). Con B →
      `Clasificador: falta OPENROUTER_API_KEY; se aplica el fallback` (sin llamar a la API).
- [ ] Logs: **la key no aparece** en ninguna línea.
- [ ] Web: crear un ticket desde el formulario → se crea, sale como Duda / P3, sin error.
- [ ] Con C (sin red): la petición no se queda colgada más de ~40 s (timeout 20 s × 2 intentos).
- [ ] **Al terminar: restaurad la key real en `.env` y reiniciad uvicorn.**

## 7. Mismo ticket enviado dos veces seguidas

**Esperado:** **dos** tickets (decisión del SPEC §5: no se deduplica).

- [ ] API:
  ```powershell
  1..2 | ForEach-Object { Send-Api POST /tickets @{ title = "Duplicado"; description = "Mismo texto" } }
  ```
  → dos `201` con **ids distintos**.
- [ ] Web: pulsar "Crear ticket" dos veces muy rápido → el botón se desactiva mientras
      clasifica (`hx-disabled-elt`), así que con un doble clic **solo se crea uno**.
      Enviarlo dos veces a propósito (esperando) → dos tickets.

## 8. IDs malformados

**Esperado:** `404` para enteros que no existen; `422` para lo que no es entero.

- [ ] API:
  ```powershell
  Send-Api GET /tickets/-1                     # 404
  Send-Api GET /tickets/0                      # 404
  Send-Api GET /tickets/abc                    # 422
  Send-Api GET /tickets/1.5                    # 422
  Send-Api GET /tickets/99999999999            # 404
  Send-Api GET /tickets/99999999999999999999   # 404 (más grande que un entero de 64 bits; no debe dar 500)
  Send-Api PATCH /tickets/abc   @{ status = "closed" }   # 422
  Send-Api PATCH /tickets/-1    @{ status = "closed" }   # 404
  Send-Api GET /tickets/tablero                # 200 HTML (no confundir "tablero" con un id)
  ```
- [ ] Ninguna respuesta es `500`.

## 9. PATCH con valores inválidos

**Esperado:** `422` **sin modificar** el ticket.

- [ ] Crear uno y guardar su id:
  ```powershell
  $t = Invoke-RestMethod -Method Post http://127.0.0.1:8000/tickets -ContentType "application/json" -Body '{"title":"Para PATCH","description":"x"}'
  $id = $t.id; $t | Select-Object id, status, priority, updated_at
  ```
- [ ] Casos (todos `422`):
  ```powershell
  Send-Api PATCH /tickets/$id @{ status = "inventado" }
  Send-Api PATCH /tickets/$id @{ status = "CLOSED" }          # mayúsculas: inválido
  Send-Api PATCH /tickets/$id @{ priority = "P9" }
  Send-Api PATCH /tickets/$id @{}                             # body vacío
  Send-Api PATCH /tickets/$id @{ title = "cambiado" }         # campo no permitido
  Send-Api PATCH /tickets/$id @{ status = "closed"; category = "bug" }   # mezcla válido + prohibido
  Send-Api PATCH /tickets/$id @{ assignees = @("x" * 51) }    # nombre > 50 caracteres
  ```
- [ ] Comprobar que **no cambió nada** (mismo `status`, `priority` y `updated_at`):
  ```powershell
  Invoke-RestMethod http://127.0.0.1:8000/tickets/$id | Select-Object id, status, priority, updated_at
  ```
- [ ] Control: `Send-Api PATCH /tickets/$id @{ status = "in_progress" }` → `200` y cambia `updated_at`.

## 10. 20 POSTs concurrentes

**Esperado:** **20** tickets creados, sin `500` ni `database is locked`.

- [ ] API (lanza 20 peticiones a la vez en segundo plano):
  ```powershell
  $antes = Count-Tickets
  $jobs = 1..20 | ForEach-Object {
    Start-Job -ArgumentList $_ -ScriptBlock {
      param($n)
      $body = [Text.Encoding]::UTF8.GetBytes((@{ title = "Concurrente $n"; description = "Prueba de carga" } | ConvertTo-Json))
      try { (Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8000/tickets -ContentType "application/json; charset=utf-8" -Body $body -UseBasicParsing).StatusCode }
      catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { "ERROR" } }
    }
  }
  $codes = $jobs | Wait-Job | Receive-Job; $jobs | Remove-Job
  $codes | Group-Object | Select-Object Name, Count        # esperado: 201 -> 20
  "Antes: $antes  Después: $(Count-Tickets)"               # +20
  ```
- [ ] Logs: ningún `database is locked` ni traza de error.
- [ ] Ids sin huecos ni repetidos: `Invoke-RestMethod "http://127.0.0.1:8000/tickets" | Where-Object title -like "Concurrente*" | Measure-Object` → 20.
- [ ] Nota: con la key real son 20 llamadas al LLM (unos pocos miles de tokens). Para
      probar solo la base de datos, usad la opción B del escenario 6 (sin key).

---

## Al terminar

- [ ] Parar uvicorn (Ctrl+C) y borrar la BD de pruebas: `Remove-Item qa.db`.
- [ ] `.env` con la key real restaurada (escenario 6).
- [ ] Rellenar la tabla de arriba y anotar en `SPEC.md` §13 cualquier decisión nueva.
- [ ] Los fallos encontrados → rama + PR con su arreglo (y, si se puede, un test que lo cubra).
