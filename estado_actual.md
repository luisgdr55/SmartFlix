# StreamVip Bot — Estado del Sistema

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
