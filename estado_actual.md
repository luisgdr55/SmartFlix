# SmartFlixVE Bot — Estado del Sistema

## Stack
- Runtime: Python 3.11+, FastAPI + Uvicorn
- Bot: python-telegram-bot (webhook)
- Base de datos: Supabase (PostgreSQL)
- Cache/Estado: Upstash Redis (TTL 30 min)
- OCR pagos: Google Gemini Vision API
- Scheduler: APScheduler (America/Caracas)
- Deploy: Railway.app (auto-deploy desde git push a master)

---

## Historial de cambios

### 2026-06-19 — Sesión 17 — Lote 0: Bs en tickets WhatsApp + fix send_to_admin bool

#### Mejoras y fixes

| # | Cambio | Archivos | Commit |
|---|--------|----------|--------|
| 1 | `formato_monto_usd_bs()` en exchange_service: formatea `5USD / 3.850 Bs` con tasa Binance fijada. Sin fallback 36.0: si no hay tasa, muestra solo USD | `services/exchange_service.py` | f672f43 |
| 2 | `send_to_admin` ahora retorna `bool` (True si al menos un admin recibió el mensaje) — antes retornaba `None`, rompiendo el `if success:` en los bloques de cliente externo | `services/notification_service.py` | f672f43 |
| 3 | Tickets WhatsApp de cliente externo en `send_expiry_reminder` y `send_expiry_notification` reemplazan `$5.00 USD` por `5USD / 3.850 Bs` usando `formato_monto_usd_bs`. Import lazy para evitar ciclo de arranque | `services/notification_service.py` | f672f43 |

#### Notas de diseño
- `_fmt_bs_ve()` usa punto de miles (formato venezolano): `3.850`, no `3,850`
- USD sin decimales si es entero (`5USD`), con 2 decimales si no (`5.50USD`)
- Bs redondeado a entero — en montos grandes los céntimos no son relevantes
- Import lazy en `notification_service.py` mantiene el patrón del archivo (todos los imports de servicios/DB son lazy) y elimina riesgo de ciclo en el arranque

---

### 2026-06-14 — Sesión 17 — Fix código de verificación Disney+ (CSS color false match)

#### Bug corregido

| # | Bug | Causa raíz | Archivos | Commit |
|---|-----|------------|----------|--------|
| 1 | Código de verificación Disney+ devolvía número incorrecto (707070) en vez del OTP real | `_strip_html` no eliminaba el contenido de bloques `<style>` — colores CSS como `color: #707070` quedaban como texto plano y `\b(\d{6})\b` los matcheaba antes del OTP | `services/imap_reader.py` | 2b4cd4c |

#### Diagnóstico
- El email de Disney+ llega como HTML puro (sin parte text/plain)
- `_extract_body` hace walk() → cae a text/html → llama `_strip_html`
- `_strip_html` aplicaba `re.sub(r"<[^>]+>", " ", html)` que elimina tags pero deja el contenido de `<style>` intacto
- Los primeros 500 chars del body eran CSS de @font-face con colores como `707070`
- `re.search(r"\b(\d{6})\b", body)` matcheaba ese color antes del código OTP real

#### Fix aplicado
```python
# Antes
text = re.sub(r"<[^>]+>", " ", html)

# Después
text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
text = re.sub(r"<[^>]+>", " ", text)
```

#### Notas operativas
- Fix no afecta Netflix — sus emails tienen parte text/plain y nunca llegan a `_strip_html`
- El mismo fix protege contra cualquier otro número de 6 dígitos en CSS (tracking IDs, font hashes, etc.)
- Verificar con el próximo OTP Disney real en producción

---

### 2026-06-13 — Sesión 21 (cont.) — Caché Redis dashboard stats

#### Mejora de rendimiento

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Caché Redis TTL=60s para `get_dashboard_stats()` — carga del dashboard de 2-5s → ~20-50ms | `database/analytics.py`, `admin_panel/router.py` | 45c1d22 |

#### Causa raíz de la lentitud
`get_dashboard_stats()` ejecutaba **10 queries paralelas a Supabase** en cada carga del dashboard sin ningún caché. Con latencia Railway→Supabase de 200–500ms por query (aunque paralelas, la más lenta define el total), la carga variaba entre **2.1s y 4.7s** según medición en logs de Railway.

#### Solución aplicada
- **`database/analytics.py`**: `get_dashboard_stats()` verifica primero Redis key `"dashboard:stats:cache"`. En cache hit retorna inmediatamente el JSON deserializado. En cache miss ejecuta las 10 queries como siempre, guarda el resultado con `SETEX TTL=60s`, y retorna los datos reales. Nunca devuelve dict vacío en cache miss (evita bug de sesión 8). Errores de Redis se capturan silenciosamente con fallback a Supabase.
- **`admin_panel/router.py`**: invalidación activa (`DELETE "dashboard:stats:cache"`) en 4 puntos de alto impacto:
  - `payment_approve` — path de renovación
  - `payment_approve` — path de nueva suscripción
  - `payment_reject`
  - `subscription_release`

#### Resultado esperado
| Carga | Antes | Después |
|-------|-------|---------|
| Primera carga (cache miss) | 2–5s | 2–5s (sin cambio) |
| Cargas siguientes (cache hit) | 2–5s | ~20–50ms |
| Tras aprobar/rechazar pago | 2–5s | 2–5s (cache invalidado) → siguientes ~20ms |

#### Patrón
Idéntico a `exchange_service.py` (`get_current_rate` con `RATE_CACHE_KEY`). TTL de 60s elegido por ser suficientemente fresco para un dashboard admin sin sacrificar rendimiento.

---

### 2026-06-13 — Sesión 21 — Fix delete suscripción + Fix códigos OTP Disney+

#### Bugs corregidos

| # | Bug | Causa raíz | Archivos | Commit |
|---|-----|-----------|----------|--------|
| 1 | Borrar suscripción fallaba silenciosamente desde panel de cliente | FK `household_incidents.subscription_id` con `ON DELETE NO ACTION` causaba violación al borrar. La función `delete_subscription` atrapaba el error y retornaba `False` sin relanzarlo; el router ignoraba ese `False` y redirigía como éxito, haciendo el error invisible. | `database/subscriptions.py`, `admin_panel/router.py` | e5d1f3f |
| 2 | Códigos de verificación Disney+ no se entregaban automáticamente | `LOOKBACK_SECONDS=900` (15 min) era insuficiente: el correo con el OTP llegaba al buzón maestro antes de que el cliente presionara el botón en el bot, y el filtro de tiempo descartaba el correo antes de leerlo. Adicionalmente, el `break` en el loop de IMAP abortaba el escaneo completo al encontrar un mensaje fuera de la ventana, aunque pudiera haber mensajes más nuevos después. | `services/imap_reader.py` | e5d1f3f |

#### Detalle de cambios por archivo

**`database/subscriptions.py` — `delete_subscription()`**
- Antes de borrar la suscripción, hace `UPDATE household_incidents SET subscription_id=NULL WHERE subscription_id=<sub_id>` para desligar los incidentes sin eliminarlos (preserva historial para cooldown de 45 días)
- La función ahora re-lanza la excepción (`raise`) en lugar de retornar `False` silenciosamente

**`admin_panel/router.py` — `subscription_delete()`**
- Obtiene `user_id` con un SELECT previo al DELETE
- En éxito redirige a `/panel/users/{user_id}?success=Suscripcion+eliminada`
- En error redirige a `/panel/users/{user_id}?error=<mensaje>` — el error ahora es visible en la página de detalle del cliente donde ocurrió la acción

**`services/imap_reader.py` — `_imap_search_once()`**
- `LOOKBACK_SECONDS`: 900 → **2700** (45 minutos)
- `break` → `continue` en el filtro de tiempo: un mensaje fuera de la ventana ya no aborta el escaneo completo
- Agregado `logger.debug()` por cada mensaje evaluado con: `msg_id`, timestamp legible, `from_header`, `to_header`, y motivo de descarte (`too_old` / `from_mismatch` / `to_mismatch`) o `EVALUATING` si pasa todos los filtros

---

### 2026-06-07 — Sesión 20 — Rediseño menú /start y flujo cancel_and_buy

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | WELCOME_NEW_USER y MAIN_MENU rediseñados — más respiro y tono amigable | `bot/messages.py` | 9f39dfd |
| 2 | RETURNING_GREETINGS renovados — emojis, saltos de línea, sin nombre repetido | `bot/handlers/start.py` | 9f39dfd |
| 3 | Alerta deuda/vencimiento rediseñada — separadores ━, footer motivacional, tono cálido | `bot/handlers/start.py` | 9f39dfd |
| 4 | Nuevo botón "Quiero algo diferente" en alerta de subs vencidas | `bot/handlers/start.py` | 9f39dfd |
| 5 | Handler cancel_and_buy — cancela subs vencidas y redirige a compra nueva | `main.py` | 9f39dfd |

#### Flujo nuevo cancel_and_buy
- Solo aparece cuando hay suscripciones vencidas (no en pendientes de pago)
- Cancela todas las subs con status=expired del usuario
- Muestra confirmación y redirige directo al flujo de compra nueva

---

### 2026-06-07 — Sesión 19 (cont.) — Banner de bienvenida en /start

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Banner de bienvenida SmartFlixVE enviado como primer mensaje en /start | `bot/handlers/start.py`, `assets/banner.png` | 0be8e59 |
| 2 | Orden de mensajes en /start: banner → precios → menú | `bot/handlers/start.py` | 698264d |

#### Notas operativas
- El banner se lee desde `assets/banner.png` en el repo — para cambiarlo, reemplazar ese archivo y hacer push
- El orden final de /start: 1) banner imagen, 2) tabla de precios en Bs, 3) menú principal con botones

---

### 2026-06-07 — Sesión 19 (cont.) — Rediseño visual tabla de precios en /start

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Tabla de precios rediseñada en formato monoespaciado `<code>` con columnas alineadas | `bot/handlers/start.py` | 5f0e738 |
| 2 | Precios se envían como segundo mensaje separado — menú normal y alerta de deuda | `bot/handlers/start.py` | 5f0e738 |
| 3 | Express unificado en una sola fila al final de la tabla — eliminado el Express por plataforma | `bot/handlers/start.py` | 5f0e738 |
| 4 | Tasa muestra solo el número en Bs sin mencionar "Binance" | `bot/handlers/start.py` | 5f0e738 |

#### Formato resultante
- Primer mensaje: saludo personalizado + disponibilidad + botones de menú
- Segundo mensaje: tabla `<code>` con columnas Plataforma / USD / Bs + fila Express al final

---

### 2026-06-07 — Sesión 19 — Precios en Bs en el inicio del bot

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Precios en Bs visibles al hacer /start — menú principal y alerta de deuda/vencimiento | `bot/handlers/start.py`, `database/analytics.py` | 3f586ef |

#### Cambios técnicos
- `get_platform_availability()` amplía su dict con `price_usd` y `price_express_usd`
- Nueva función `_build_prices_text()` en `start.py` — calcula Bs inline con `round(price * rate, 0)` sin llamadas async extra a Redis
- Ambos bloques de respuesta en `start_handler` (menú normal y alerta deuda) muestran el bloque de precios si está disponible

#### Bugs corregidos en esta sesión

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | `timedelta` no importado en `hogar.py` — NameError al ejecutar migración express | `bot/handlers/hogar.py` | be10201 |

---

### 2026-06-06 — Sesión 18 — Modal credenciales en ficha de cliente

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Clic en plataforma abre modal con email, contraseña, perfil y PIN — sin salir de la ficha | `user_detail.html`, `router.py` | 6236e06 |
| 2 | Selector de perfiles en edit mode muestra [email@cuenta] como prefijo — identifica a qué cuenta pertenece cada perfil | `user_detail.html`, `router.py` | 6236e06 |

#### Cambios técnicos
- `subs_res` en `user_detail` amplía join: `profiles(profile_name, pin, accounts(email, password))`
- `api_profiles_by_platform` amplía select: agrega `accounts(id, email)` a cada perfil
- Modal Alpine.js con evento `@open-credentials.window` y botón copiar con feedback visual

---

### 2026-06-06 — Sesión 18 — Fix onboarding usuarios sin @username

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Usuarios nuevos sin @username quedaban bloqueados pidiendo teléfono en bucle — nunca llegaban al menú principal | `bot/handlers/start.py` | (sesión) |

#### Cambios aplicados
- Eliminado bloque de verificación por teléfono obligatoria en `start_handler`
- `get_or_create_user` se llama directamente para todos los usuarios
- Usuarios sin @username ahora pasan directo al onboarding normal (pedir nombre → menú)

---

### 2026-06-06 — Sesión 17 — Fix migración express admin + UX selector perfiles

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | _admin_execute_express fallaba con int(uid_UUID) para clientes sin Telegram | `bot/handlers/hogar.py` | 73fa49c |
| 2 | _execute_express_migration intentaba send_message con telegram_id=None — excepción no controlada | `bot/handlers/hogar.py` | 73fa49c |

#### Mejoras aplicadas

| # | Mejora | Archivos |
|---|--------|----------|
| 1 | Selector de perfiles express muestra cuántos fueron excluidos por cooling 45 días | `bot/handlers/hogar.py` |

#### Notas operativas
- El filtro de 45 días en get_available_profiles_for_migration ya funcionaba correctamente — los cambios son el guard uid_ y el contexto visual
- Para clientes sin Telegram (uid_XXX), el ticket de migración express llega solo al admin
- El flujo confirm_express (cliente autoservicio) no requiere cambios — client_tid siempre es entero en ese flujo

---

### 2026-06-06 — Sesión 16 — Fixes módulo hogar + clientes sin Telegram

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Migración express no incluía contraseña en ticket WhatsApp ni notificación Telegram | `bot/handlers/hogar.py` | (sesión) |
| 2 | /hogar mostraba cliente duplicado cuando tenía 2 suscripciones Netflix | `bot/handlers/hogar.py` | (sesión) |
| 3 | Clientes afiliados manualmente (sin telegram_id) no aparecían en /hogar | `bot/handlers/hogar.py` | (sesión) |
| 4 | uid: como separador colisionaba con split(':') del dispatcher | `bot/handlers/hogar.py` | (sesión) |
| 5 | Button_data_invalid en select_profile, finalize_history, complete_history — UUIDs superaban 64 bytes | `bot/handlers/hogar.py` | (sesión) |
| 6 | get_netflix_subscription_for_user filtraba con !inner — retornaba vacío si platform_id era NULL | `database/hogar.py` | (sesión) |
| 7 | Migración con historial no incluía contraseña en ticket | `bot/handlers/hogar.py` | (sesión) |
| 8 | _admin_complete_history_with_profile fallaba con int(uid_UUID) para clientes sin Telegram | `bot/handlers/hogar.py` | (sesión) |
| 9 | `timedelta` no importado en `hogar.py` — NameError al ejecutar migración express (/hogar admin) | `bot/handlers/hogar.py` | be10201 |

#### Mejoras aplicadas

| # | Mejora | Archivos |
|---|--------|----------|
| 1 | Clientes con múltiples suscripciones Netflix muestran selector en /hogar | `bot/handlers/hogar.py` |
| 2 | Todos los callbacks del módulo hogar dentro del límite de 64 bytes — patrón Redis+índice | `bot/handlers/hogar.py` |
| 3 | Clientes sin telegram_id soportados en todo el flujo admin — uid_ como identificador | `bot/handlers/hogar.py` |
| 4 | Ticket de migración (express e historial) incluye contraseña de cuenta destino | `bot/handlers/hogar.py` |

#### Notas operativas
- El patrón Redis+índice se usa en: hogar_subs:{admin_tid}, hogar_profiles:{admin_tid}, hogar_incident_admin:{admin_tid}
- Clientes sin telegram_id (afiliados con /afiliar) se identifican con uid_{uuid} en callbacks
- Para clientes sin Telegram, el ticket de migración solo llega al admin — debe enviarse manualmente por WhatsApp
- El separador uid_ (con guión bajo) evita colisión con split(':') en el dispatcher

---

### 2026-06-05 — Sesión 15 — Módulo códigos de verificación + fix credenciales soporte

#### Features implementadas

| # | Feature | Archivos | Commit |
|---|---------|----------|--------|
| 1 | Filtro por email de cuenta en poll_for_code (IMAP) — evita entregar códigos de otras cuentas | `services/imap_reader.py` | d0e4d6e |
| 2 | Validación de suscripción activa antes de entregar código de verificación | `bot/handlers/support.py` | d0e4d6e |
| 3 | accounts(email, password) incluido en get_user_active_subscriptions | `database/subscriptions.py` | d0e4d6e |
| 4 | _send_credentials sin queries extra — lee datos del join directamente | `bot/handlers/support.py` | bf5e23b |
| 5 | Fix Button_data_invalid — callback_data dentro del límite 64 bytes | `bot/keyboards.py`, `main.py` | 00e0173 |
| 6 | handle_support_platform_selected localiza sub por prefijo UUID | `bot/handlers/support.py` | 1103319 |

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Ver credenciales fallaba con "Error al obtener credenciales" — get_account_by_id innecesario | `bot/handlers/support.py` | bf5e23b |
| 2 | Selección de plataforma no respondía — patrón callback_data no coincidía | `main.py` | 00e0173 |
| 3 | Código de verificación podía entregarse de cuenta incorrecta — sin filtro To: | `services/imap_reader.py` | d0e4d6e |

#### Notas operativas
- El filtro por To: en IMAP requiere que los emails de plataformas estén reenviados a la bandeja central con el header To: original preservado
- El módulo de códigos de verificación (IMAP) es independiente del flujo hogar (Gmail API)
- Pendiente prueba real de códigos de verificación con cliente activo de cada plataforma

---

### 2026-06-05 — Sesión 14 — Módulo Soporte Hogar Netflix

#### Features implementadas

| # | Feature | Archivos | Commit |
|---|---------|----------|--------|
| 1 | Flujo cliente autoservicio hogar Netflix (foto → Gemini → código/migración) | `bot/handlers/hogar.py` | 0ce35f6 |
| 2 | Comando /hogar admin — lista paginada de clientes Netflix activos | `bot/handlers/hogar.py` | 9c4e5f7 |
| 3 | Búsqueda de código/link en Gmail maestro con filtro por cuenta | `services/gmail_service.py` | 0ce35f6 |
| 4 | Análisis de pantalla Netflix con Gemini Vision (first_warning/second_warning) | `services/gemini_service.py` | 10193eb |
| 5 | Migración express automática con cooling 45 días | `bot/handlers/hogar.py`, `database/hogar.py` | f528fd3 |
| 6 | Migración con historial — ticket admin + flujo de completado | `bot/handlers/hogar.py` | 0ce35f6 |
| 7 | Sistema de salud de cuentas (healthy/warning/restricted) | `database/hogar.py` | 9e7a005 |
| 8 | Notificaciones admin detalladas con nombre, teléfono y credenciales | `bot/handlers/hogar.py` | 03cb959 |
| 9 | Botón "Restricción de Hogar Netflix" en menú de soporte | `bot/keyboards.py`, `main.py` | cb5db9f |
| 10 | Soporte email tipo código directo (662727) y tipo link | `services/gmail_service.py` | 0ce35f6 |

#### Cambios en BD
```sql
ALTER TABLE accounts ADD COLUMN household_incidents INT DEFAULT 0;
ALTER TABLE accounts ADD COLUMN account_health VARCHAR(20) DEFAULT 'healthy';
ALTER TABLE accounts ADD COLUMN last_incident_at TIMESTAMPTZ;

CREATE TABLE household_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    account_id UUID REFERENCES accounts(id),
    profile_id UUID REFERENCES profiles(id),
    subscription_id UUID REFERENCES subscriptions(id),
    stage VARCHAR(20),
    type VARCHAR(30),
    new_profile_id UUID REFERENCES profiles(id),
    new_account_id UUID REFERENCES accounts(id),
    gmail_link TEXT,
    admin_notes TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
```

#### Variables de entorno nuevas
- `GMAIL_MASTER_CREDENTIALS_JSON` — JSON OAuth2 del Gmail maestro smartflixve.codigos@gmail.com

#### Archivos nuevos
- `bot/handlers/hogar.py`
- `database/hogar.py`

#### Archivos modificados
- `services/gmail_service.py` — get_netflix_household_link, get_netflix_access_code
- `services/gemini_service.py` — analyze_netflix_screen
- `database/profiles.py` — get_profile_by_subscription
- `database/users.py` — search_users
- `bot/keyboards.py` — botón hogar en support_keyboard
- `main.py` — registro de handlers hogar
- `scheduler/jobs.py` — job_account_health_alerts

#### Notas operativas
- El flujo de "Estoy de viaje" (link) está implementado pero pendiente prueba con cliente real con Smart TV
- El modelo OpenRouter activo es google/gemini-2.5-flash-lite
- El token OAuth2 del Gmail maestro se refresca automáticamente en cada uso
- El cooling de 45 días aplica por relación usuario-perfil, no por perfil global

---

### 2026-06-03 — Sesión 13 — Fix flujo de renovación y corte automático

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Suscripción pending_payment cancelada a los 45 min — admin no podía aprobar si tardaba más | `subscriptions.py`, `jobs.py` | (este commit) |
| 2 | Carrito de renovación incluía subs activas próximas a vencer — sistema reportaba "pago múltiple" incorrectamente | `subscriptions.py`, `subscription.py` | (este commit) |
| 3 | Cliente sin acceso directo a renovar — tenía que usar /start para ver el botón | `main.py` | (este commit) |
| 4 | Corte automático día 7 nunca se ejecutaba — PART 2 de job_debt_reminders_and_cuts faltaba completamente | `jobs.py` | (este commit) |
| 5 | Notificación de corte al admin sin email ni password de la cuenta | `subscriptions.py`, `jobs.py` | (este commit) |
| 6 | get_subscriptions_past_grace_period() no incluía pin ni accounts en el join — admin recibía "—" en credenciales | `subscriptions.py` | (este commit) |

#### Mejoras añadidas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | TTL de pending_payment extendido de 45 min a 4 horas | `subscriptions.py`, `jobs.py` | (este commit) |
| 2 | Subs "próximas a vencer" separadas de "expiradas" — aviso informativo sin interferir en el carrito | `subscriptions.py`, `subscription.py` | (este commit) |
| 3 | Cliente puede escribir "renovar" o "pagar" en cualquier momento para acceder al flujo de renovación | `main.py` | (este commit) |
| 4 | Corte día 7: rota PIN, libera perfil, notifica cliente y admin con email + password + PIN anterior + PIN nuevo | `jobs.py`, `subscriptions.py` | (este commit) |

#### Notas operativas
- Un cliente con 10+ días cortado puede renovar escribiendo "renovar" — el sistema detecta su sub cancelada y abre el carrito
- El corte automático corre diariamente con job_debt_reminders_and_cuts — días 1-6 aviso, día 7 corte real
- Las subs que vencen en 3 días muestran aviso informativo pero NO van al carrito de renovación anticipada

### 2026-06-03 — Sesión 12 — Fix de costos en reporte diario y reporte mensual de cierre

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | `cost_usd_monthly` no existía en tabla `accounts` — costos siempre $0.00 en reporte diario | `supabase_schema.sql` + Supabase SQL Editor | (este commit) |
| 2 | `debt_reminder_count` no existía en tabla `subscriptions` — recordatorio de deuda siempre enviaba "Día 1" y nunca cortaba el servicio | `supabase_schema.sql` + Supabase SQL Editor | (este commit) |
| 3 | `monthly_revenue_usd` excluía suscripciones expiradas del mes — ingresos subestimados | `analytics.py` | (este commit) |
| 4 | `monthly_cost_usd` sumaba costo total de todas las cuentas sin prorratear — comparación injusta a mitad de mes | `analytics.py` | (este commit) |

#### Mejoras añadidas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Reporte diario: costos prorrateados por días transcurridos del mes (`days_elapsed / days_in_month`) | `analytics.py` | (este commit) |
| 2 | Reporte diario: label cambiado a "Costos al día de hoy" para reflejar el prorrateo | `jobs.py` | (este commit) |
| 3 | Reporte mensual de cierre — Job 11 — se ejecuta el día 1 de cada mes a las 9:00 AM: ingresos reales, costos completos, ganancia neta, margen, desglose por plataforma y cuenta, nuevos clientes, renovaciones y no-renovaciones | `database/monthly_report.py` (nuevo), `scheduler/jobs.py` | (este commit) |

#### Notas operativas
- Entrar el costo mensual de cada cuenta en Panel → Cuentas → Editar (campo `cost_usd_monthly`) para que los reportes muestren costos reales
- El reporte mensual calcula el mes ANTERIOR completo — los costos no se prorratean porque el cliente ya pagó 30 días por adelantado

### 2026-04-05 — Sesión de debugging masivo

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Error al obtener perfil en clientes nuevos — fallback `get_or_create_user` en 4 handlers | `subscription.py` | 5c5fe88 |
| 2 | Renovación creaba suscripción nueva en vez de extender fecha — fix en filtro de status, `payment_reference` persistido, fallback cuando `profile_id=None` | `subscriptions.py`, `admin.py` | 5c5fe88 |
| 3 | Carrito de renovaciones múltiples no acumulaba — migrado de `context.user_data` a Redis | `subscription.py`, `cart_service.py` | 5c5fe88 |
| 4 | Menú `/admin` no respondía — `NameError: telegram_id`, ramas `admin:stock` y `admin:config` faltantes | `admin.py` | 5c5fe88 |
| 5 | Afiliación manual sin rollback si falla creación de suscripción | `afiliar.py` | 5c5fe88 |
| 6 | Scheduler: import dentro del loop, doble procesamiento en express release | `jobs.py` | 5c5fe88 |
| 7 | `REDIS_URL` sin esquema `rediss://` — todos los errores de Redis en producción | Variable de entorno Railway | manual |
| 8 | Carrito de compra no acumulaba items — `cart_service` usaba cliente Redis de `gemini_service` con pool agotado | `cart_service.py` | 9e0b6a5 |
| 9 | Panel admin: liberar suscripción express no rotaba PIN | `router.py` | (último commit) |
| 10 | `admin:income` no mostraba nada — llamaba `cmd_ingresos` incompatible con callbacks | `admin.py` | (último commit) |
| 11 | `admin:config` lanzaba error inesperado — `reply_text` vs `edit_message_text` | `admin.py` | (último commit) |
| 12 | Detalle de cliente sin datos de perfil ni fecha de vencimiento | `analytics.py`, `admin.py` | (último commit) |
| 13 | Error inesperado en algunos clientes al abrirlos — sin guard try/except | `admin.py` | (último commit) |
| 14 | Lista de clientes congela spinner al pulsar varios botones — sin guard `MessageNotModified` | `admin.py` | (último commit) |
| 15 | Notificaciones de vencimiento no llegaban al admin | `notification_service.py` | (último commit) |

#### Mejoras añadidas

| # | Mejora | Archivos |
|---|--------|----------|
| 1 | Cancelación manual de suscripción activa desde `/admin` — libera perfil, rota PIN, notifica cliente | `admin.py`, `keyboards.py`, `subscriptions.py` |
| 2 | `job_express_release` notifica al admin con PIN anterior y PIN nuevo al liberar cuenta express | `jobs.py` |

### 2026-04-12 — Sesión de mejoras panel admin y notificaciones

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Panel web: liberar suscripción express no rotaba PIN | `router.py` | (sesión anterior) |
| 2 | `admin:income` no mostraba nada — incompatible con callbacks | `admin.py` | (sesión anterior) |
| 3 | `admin:config` error inesperado — `reply_text` vs `edit_message_text` | `admin.py` | (sesión anterior) |
| 4 | Detalle de cliente sin datos de perfil ni fecha de vencimiento | `analytics.py`, `admin.py` | (sesión anterior) |
| 5 | Error inesperado al abrir algunos clientes — sin guard try/except | `admin.py` | (sesión anterior) |
| 6 | Lista de clientes congela spinner al pulsar varios botones | `admin.py` | (sesión anterior) |
| 7 | Notificaciones de vencimiento no llegaban al admin | `notification_service.py` | (sesión anterior) |
| 8 | Ficha de cliente en /admin no mostraba PIN del perfil | `admin.py` | 5723d5e |
| 9 | Nombres de suscriptores invisibles en panel (texto blanco sobre blanco) | `subscriptions.html` | 5723d5e |

#### Mejoras añadidas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Modal de detalle de suscripción en panel web — muestra plataforma, correo, contraseña, perfil, PIN y fecha de vencimiento al pulsar el nombre del cliente | `subscriptions.html`, `router.py` | 5723d5e |
| 2 | Notificaciones de vencimiento D-3 y D+0 ahora llegan también al admin vía Telegram | `notification_service.py` | (sesión anterior) |
| 3 | Cancelación manual de suscripción activa desde /admin con liberación de perfil y rotación de PIN | `admin.py`, `keyboards.py`, `subscriptions.py` | (sesión anterior) |

### 2026-04-26 — Sesión 9 (cont.) — Rebranding y cierre de sesión

#### Cambios aplicados

| Cambio | Detalle | Commit |
|--------|---------|--------|
| Rebranding completo | StreamVip → SmartFlixVE en 28 archivos de producción | 9eee871 |

#### Archivos actualizados en rebranding
- Templates HTML del panel web
- bot/messages.py, afiliar.py, renovar.py — tickets con SMARTFLIXVE
- config.py, auth.py — defaults actualizados
- services/flyer_service.py — @SmartFlixVE
- services/gemini_service.py — https://smartflixve.app
- main.py, estado_actual.md, features.md, SETUP.md

#### Pendiente para próxima sesión
- Fase 4: Afiliación y renovación vía Dashboard web
- Fase 5: Migración de cuenta Netflix

---

### 2026-04-26 — Sesión 11 (cont.) — Fase 3: Renovación manual vía Telegram

#### Features implementadas

| # | Feature | Archivos | Commit |
|---|---------|----------|--------|
| 3A | /renovar para admin — lista clientes, elige suscripción, genera ticket | `renovar.py`, `subscriptions.py`, `main.py` | a4e60d5 |
| 3B | Compatible con clientes sin telegram_id — ticket solo al admin para copiar | `renovar.py` | a4e60d5 |

---

### 2026-04-26 — Sesión 11 — Fase 2: Afiliación manual mejorada

#### Features implementadas

| # | Feature | Archivos | Commit |
|---|---------|----------|--------|
| 2A | /afiliar pregunta cliente nuevo o existente con lista paginada | `afiliar.py`, `users.py` | eaf55b3 |
| 2B | Selección manual de perfil con nombre y PIN visibles | `afiliar.py` | eaf55b3 |
| 2C | Ticket detallado copiable al finalizar con credenciales completas | `afiliar.py` | eaf55b3 |

---

### 2026-04-26 — Sesión 10 — Fase 1: Reportes mejorados y costos

#### Features implementadas

| # | Feature | Archivos | Commit |
|---|---------|----------|--------|
| 1A | Reporte diario muestra nuevos clientes últimos 7 días (antes solo hoy) | `analytics.py`, `jobs.py` | ffe7433 |
| 1B | Ganancia neta real en reporte diario — campo cost_usd_monthly por cuenta, cálculo automático ingreso-costo | `analytics.py`, `jobs.py`, `router.py`, `account_form.html` | ffe7433 |

#### Notas operativas
- Entrar costos mensuales por cuenta en Panel → Cuentas → Editar
- Netflix 4K estimado: $8.53 USD/mes (gift card $16 USDT / 1.875 meses)
- La ganancia neta se calcula: ingresos confirmados del mes - suma de costos de cuentas activas

---

### 2026-04-26 — Sesión 9 — Recuperación de bot caído por cambio de dominio Railway

#### Problema
Bot de Telegram sin responder y webhook retornando 404. Causa raíz: Railway reasignó el dominio del servicio de `smartflix-production.up.railway.app` a `smartflixve.up.railway.app`. El webhook quedó apuntando a la URL vieja.

#### Diagnóstico
- `getWebhookInfo` → `last_error_message: "Wrong response from the webhook: 404"`
- `curl /health` al dominio viejo → `{"status":"error","code":404,"message":"Application not found"}` (respuesta del router de Railway, no de FastAPI)
- App corriendo internamente (confirmado por Railway logs con requests desde IPs `100.64.x.x`)
- Código sin errores — el revert de la sesión 8 fue limpio

#### Solución aplicada

| Paso | Acción |
|------|--------|
| 1 | Usuario actualizó `APP_URL` en Railway dashboard → `https://smartflixve.up.railway.app` |
| 2 | Re-registro manual del webhook: `setWebhook` con URL nueva + `WEBHOOK_SECRET=smartflix2025ve` |
| 3 | Commit vacío para forzar redeploy (lifespan re-registra webhook automáticamente al arrancar) |

#### Commits
| Commit | Descripción |
|--------|-------------|
| 6d4113b | fix: force redeploy — webhook URL actualizada a smartflixve.up.railway.app |

#### Notas de infraestructura
- `APP_URL` en Railway debe coincidir exactamente con el dominio activo del servicio
- `WEBHOOK_SECRET` del proyecto: `smartflix2025ve` (Railway env var)
- Al arrancar, el `lifespan` re-registra el webhook automáticamente — no requiere intervención manual si `APP_URL` es correcto
- Si Railway vuelve a cambiar el dominio: actualizar `APP_URL` en Railway vars y pushear cualquier commit

---

### 2026-04-26 — Sesión 8 — Optimización de rendimiento del dashboard

#### Mejoras aplicadas

| Cambio | Antes | Después | Commit |
|--------|-------|---------|--------|
| get_dashboard_stats | 6 queries sync seriales | asyncio.gather paralelo | 5bf76cb |
| get_platform_availability | loop N×2+1 queries | 1 query bulk + agrupación Python | 5bf76cb |
| Revenue chart | 7 queries (loop diario) | 1 query + agrupación Python | 83d44ce |
| Sweep auto-expire | Bloqueante en cada carga | Scheduler Job 10 cada 15 min | 83d44ce |
| Timing logs | Sin métricas | Dashboard gather/stats/queries loggeados | ac57cde |

#### Intento de caché Redis — revertido

| Commit | Razón |
|--------|-------|
| 07401a3, 3f344f0, 0a4b60d | Error HTTP/2 de Supabase al leer keys vacías en arranque — dashboard mostraba todos los datos en 0 |
| a986199 | Revert a versión estable ac57cde |

#### Estado actual del dashboard
- Tiempo de carga: ~2-3s (latencia de red Railway → Supabase)
- Todas las queries corren en paralelo (asyncio.gather)
- El caché Redis queda pendiente — requiere inicialización de keys al arrancar el servidor, no en el primer request

#### Notas para retomar el caché
- El error fue: caché devuelve datos vacíos antes de que Redis tenga las keys
- Solución correcta: warm-up del caché en el lifespan de FastAPI al arrancar
- No implementar en el request handler directamente

### 2026-04-12 — Sesión 7 — Optimización de rendimiento del dashboard

#### Mejoras de rendimiento

| Cambio | Antes | Después | Commit |
|--------|-------|---------|--------|
| Queries del dashboard | 17 seriales | 9 en paralelo (asyncio.gather) | 83d44ce |
| Revenue chart | 7 queries (loop) | 1 query + agrupación en Python | 83d44ce |
| Sweep de suscripciones vencidas | Bloqueante en cada carga | Scheduler cada 15 min (Job 10) | 83d44ce |

### 2026-04-12 — Sesión 6 — Express y credenciales en renovación

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Express liberado: admin no recibía PIN anterior ni credenciales de cuenta | `jobs.py`, `subscriptions.py` | 1896ee4 |
| 2 | Renovación aprobada: cliente no recibía credenciales completas, solo perfil y fecha | `admin.py`, `subscriptions.py` | 1896ee4 |
| 3 | Cliente con aviso D-3 no podía renovar — sistema solo permitía renovar suscripciones ya vencidas | `subscriptions.py` | (commit anterior) |

### 2026-04-12 — Sesión 5 — Reserva temporal de perfiles

#### Mejora añadida

| Mejora | Detalle |
|--------|---------|
| Reserva temporal de perfil al renovar | Al confirmar pago, el perfil disponible queda reservado 30 min para ese usuario. Evita que otro cliente lo tome mientras espera aprobación del admin. |

#### Cambios en BD
- `profiles.reserved_until` (TIMESTAMPTZ) — cuándo expira la reserva
- `profiles.reserved_for` (UUID FK → users) — qué usuario tiene la reserva
- `idx_profiles_reserved` — índice para el scheduler

#### Flujo de reserva
1. Cliente confirma renovación → `reserve_profile()` → status="reserved", TTL 30 min
2. Scheduler cada 10 min → `job_release_expired_reservations()` → libera reservas vencidas
3. Admin aprueba pago → `assign_profile()` → status="occupied", limpia reserved_for/reserved_until
4. `get_available_profiles()` excluye reservas vigentes, incluye reservas expiradas

---

## Funcionalidades principales

| Módulo | Estado | Notas |
|--------|--------|-------|
| Compra nueva (1 plataforma) | ✅ Operativo | |
| Compra nueva (carrito múltiple) | ✅ Operativo | Redis `cart:{tid}` |
| Renovación (1 plataforma) | ✅ Operativo | |
| Renovación (carrito múltiple) | ✅ Operativo | Redis `renewal_cart:{tid}` |
| Aprobación/rechazo de pagos | ✅ Operativo | |
| Afiliación manual `/afiliar` | ✅ Operativo | Solo admin |
| Panel `/admin` — Pendientes | ✅ Operativo | |
| Panel `/admin` — Clientes | ✅ Operativo | |
| Panel `/admin` — Ingresos | ✅ Operativo | Muestra mes actual |
| Panel `/admin` — Stock | ✅ Operativo | |
| Panel `/admin` — Config | ✅ Operativo | |
| Panel `/admin` — Precios | ✅ Operativo | |
| Cancelación manual de suscripción | ✅ Operativo | Desde detalle de cliente |
| Notificaciones D-3 (próximo a vencer) | ✅ Operativo | Scheduler 10AM diario |
| Notificaciones D+0 (vencido) | ✅ Operativo | Scheduler cada hora |
| Grace period D+1 a D+6 | ✅ Operativo | Scheduler 9AM diario |
| Corte automático D+7 | ✅ Operativo | Con guard anti-corte si hay pago pendiente |
| Express release (24h) | ✅ Operativo | Scheduler cada 5 min |
| OCR comprobantes de pago | ✅ Operativo | Gemini Vision |
| Tasa de cambio Binance | ✅ Operativo | Cache Redis 30 min |
| Soporte / códigos 2FA | ✅ Operativo | |

---

### 2026-05-02 — Sesión 10 — Dashboard mejorado y notificaciones

#### Bugs corregidos

| # | Bug | Archivos | Commit |
|---|-----|----------|--------|
| 1 | Banners de alertas tapaban el dashboard y no hacían scroll en móvil | `dashboard.html`, `base.html` | 12cf7d2 |
| 2 | Cliente con suscripción activa no podía renovar aunque recibiera aviso D-3 | `subscriptions.py` | ee8bd8f |

#### Mejoras aplicadas

| # | Mejora | Archivos | Commit |
|---|--------|----------|--------|
| 1 | Alertas rediseñadas como tarjetas colapsables en grid 3 columnas con badge contador | `dashboard.html`, `base.html` | 12cf7d2 |
| 2 | Ticket WhatsApp copiable para clientes externos en recordatorios de vencimiento | `notification_service.py` | af3ff2d |
| 3 | Botones de notificación por Telegram y ticket WhatsApp en cada alerta del dashboard | `dashboard.html`, `router.py` | 26b5e5c |
| 4 | Modal de ticket WhatsApp con botón copiar al portapapeles | `dashboard.html` | 26b5e5c |
| 5 | Deep link 🤖 Bot hacia SmartFlixVEBot para llevar cliente directo a renovación | `dashboard.html` | 26b5e5c |

---

## Bugs conocidos pendientes
_Ninguno al cierre de esta sesión._

---

## Notas de infraestructura
- `REDIS_URL` debe tener esquema `rediss://` (con doble s) para Upstash con TLS
- Railway redespliega automáticamente en cada `git push origin master`
- El webhook de Telegram se reregistra automáticamente al arrancar (lifespan en `main.py`)
- Supabase usa service role key (no anon key) — verificar RLS si algún update falla silenciosamente
