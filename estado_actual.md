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
