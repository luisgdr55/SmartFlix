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

## Bugs conocidos pendientes
_Ninguno al cierre de esta sesión._

---

## Notas de infraestructura
- `REDIS_URL` debe tener esquema `rediss://` (con doble s) para Upstash con TLS
- Railway redespliega automáticamente en cada `git push origin master`
- El webhook de Telegram se reregistra automáticamente al arrancar (lifespan en `main.py`)
- Supabase usa service role key (no anon key) — verificar RLS si algún update falla silenciosamente
