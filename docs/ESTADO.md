# ESTADO.md — SmartFlixVE

Última actualización: 15/11/2026 — rescate r00 al entorno nube (solo documentación; el código de `master` no cambió desde `5a25457`, sesión 18, 26/06/2026).

> Bot de Telegram para alquiler de perfiles de streaming en Venezuela (Netflix, Disney+, Paramount+, HBO Max, Prime Video). **En producción.** Lectura al iniciar una orden: **§0 y §1**. Lo demás se consulta si la orden lo cita:
> reglas → `CLAUDE.md` · roadmap → `features.md` (raíz) · instalación → `SETUP.md` · pendientes y hallazgos → `docs/RECORDATORIOS.md` · deuda con disparador → `docs/DEUDA.md` ·
> documentos del Arquitecto → `docs/arquitecto/README.md` · **histórico completo (sesiones 5 a 21) → `docs/archivo/estado_actual-historico.md`**.

## 0. En curso

**Ninguna orden abierta.** No hay bugs conocidos pendientes (cierre de la sesión 18). Estudios fuera del repo, sin orden de construcción: factibilidad de migrar el bot a una PWA tipo tienda (02/11/2026) y flyers/video de promoción (14/11/2026); ver `docs/RECORDATORIOS.md` R00-6.
Verificado en Railway el 15/11/2026: proyecto con servicio del bot (`SmartFlix`, rama `master`), Postgres y Redis; **no hay `watchPatterns`**: cualquier fusión a `master`, incluso solo de docs, redespliega y reinicia el bot.
Hallazgos del rescate que requieren decisión del Orquestador: repositorio **público** (R00-1) y TCP Proxy público activo en el Postgres de Railway (R00-2).

## 1. Última orden cerrada

**Sesión 18 ✅ (26/06/2026)** — «Message is not modified» en los selectores de plataforma: `except BadRequest` en 5 puntos de edición (`subscription.py`, `express.py`, `afiliar.py`, `main.py`), y el aviso de «próximo a vencer» deja de ser un callejón: ahora ofrece Renovar anticipadamente, Contratar otro servicio y Menú principal.
Commits `90099a6`, `033af73` y el de documentación `5a25457`, en `master` (despliegue Railway SUCCESS el 26/06). Aprendizaje pendiente: sweep de `except Exception` demasiado amplios y de funciones que silencian errores con `return []` (ver `docs/DEUDA.md`).
Antes: sesión 17 (migración hogar, OTP Disney+, tickets WhatsApp), sesión 21 (caché Redis del dashboard), sesión 20 (menú `/start`), sesiones 14-15 (soporte Hogar Netflix, códigos de verificación).

## 2. Funcionalidades (estado al 26/06/2026, tomado del histórico)

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

## 3. Historial de sesiones (detalle en `docs/archivo/estado_actual-historico.md`)

| Fecha | Sesión |
|---|---|
| 2026-06-26 | Sesión 18 — Fix doble-toque / "Message is not modified" en pickers de plataforma |
| 2026-06-20 | Sesión 17 — Fix migración hogar: cuentas restricted visibles para admin |
| 2026-06-19 | Sesión 17 — Lote 0: Bs en tickets WhatsApp + fix send_to_admin bool |
| 2026-06-14 | Sesión 17 — Fix código de verificación Disney+ (CSS color false match) |
| 2026-06-13 | Sesión 21 (cont.) — Caché Redis dashboard stats |
| 2026-06-13 | Sesión 21 — Fix delete suscripción + Fix códigos OTP Disney+ |
| 2026-06-07 | Sesión 20 — Rediseño menú /start y flujo cancel_and_buy |
| 2026-06-07 | Sesión 19 (cont.) — Banner de bienvenida en /start |
| 2026-06-07 | Sesión 19 (cont.) — Rediseño visual tabla de precios en /start |
| 2026-06-07 | Sesión 19 — Precios en Bs en el inicio del bot |
| 2026-06-06 | Sesión 18 — Modal credenciales en ficha de cliente |
| 2026-06-06 | Sesión 18 — Fix onboarding usuarios sin @username |
| 2026-06-06 | Sesión 17 — Fix migración express admin + UX selector perfiles |
| 2026-06-06 | Sesión 16 — Fixes módulo hogar + clientes sin Telegram |
| 2026-06-05 | Sesión 15 — Módulo códigos de verificación + fix credenciales soporte |
| 2026-06-05 | Sesión 14 — Módulo Soporte Hogar Netflix |
| 2026-06-03 | Sesión 13 — Fix flujo de renovación y corte automático |
| 2026-06-03 | Sesión 12 — Fix de costos en reporte diario y reporte mensual de cierre |
| 2026-04-05 | Sesión de debugging masivo |
| 2026-04-12 | Sesión de mejoras panel admin y notificaciones |
| 2026-04-26 | Sesión 9 (cont.) — Rebranding y cierre de sesión |
| 2026-04-26 | Sesión 11 (cont.) — Fase 3: Renovación manual vía Telegram |
| 2026-04-26 | Sesión 11 — Fase 2: Afiliación manual mejorada |
| 2026-04-26 | Sesión 10 — Fase 1: Reportes mejorados y costos |
| 2026-04-26 | Sesión 9 — Recuperación de bot caído por cambio de dominio Railway |
| 2026-04-26 | Sesión 8 — Optimización de rendimiento del dashboard |
| 2026-04-12 | Sesión 7 — Optimización de rendimiento del dashboard |
| 2026-04-12 | Sesión 6 — Express y credenciales en renovación |
| 2026-04-12 | Sesión 5 — Reserva temporal de perfiles |
| 2026-05-02 | Sesión 10 — Dashboard mejorado y notificaciones |

## 4. Mantenimiento de este archivo

§0 y §1 siempre al día; al cerrar una orden, la anterior de §1 baja a una línea y se agrega su fila en §3. El detalle de cada orden va en `docs/subfases/<orden>.md`. Roadmap en `features.md`; no se duplica aquí.
