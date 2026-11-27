# ESTADO.md — SmartFlixVE

Última actualización: 27/11/2026 — auditoría general del sistema, órdenes O01 a O05 redactadas y primeros lotes construidos en rama (sin fusionar). El código de `master` no cambió desde `5a25457` (sesión 18).

> Bot de Telegram para alquiler de perfiles de streaming en Venezuela (Netflix, Disney+, Paramount+, HBO Max, Prime Video). **En producción.** Lectura al iniciar una orden: **§0 y §1**. Lo demás se consulta si la orden lo cita:
> reglas → `CLAUDE.md` · roadmap → `features.md` (raíz) · instalación → `SETUP.md` · pendientes y hallazgos → `docs/RECORDATORIOS.md` · deuda con disparador → `docs/DEUDA.md` ·
> documentos del Arquitecto → `docs/arquitecto/README.md` · **histórico completo (sesiones 5 a 21) → `docs/archivo/estado_actual-historico.md`**.

## 0. En curso

**O01 Lote 0** (endurecimiento del panel) y **O05 Lote 5** (confirmaciones de la IA del admin): construidos en la rama `claude/keen-mendel-vn6j7b`, **pendientes de revisión del PR y de veredicto**; no están en `master`. Detalle: `docs/subfases/o01-lote0-seguridad-panel.md` y `docs/subfases/o05-lote5-ia-admin-confirmaciones.md`. **Precondiciones antes de fusionar:** variables `SECRET_KEY`, `ADMIN_PANEL_PASSWORD` y `WEBHOOK_SECRET` presentes y propias en Railway (si no, el servicio no arranca) y rotación de claves (R01-1, S-001).
**O02 Lote 0 entregado (27/11/2026): decisiones y diagnóstico, sin cambios de código.** Mapa de estados verificado, informe de decisiones para el Orquestador y script SQL de solo lectura que ejecuta él mismo: `docs/subfases/o02-lote0-mapa-de-estados.md`, `o02-lote0-decisiones.md`, `o02-lote0-consultas-anomalias.sql`. **Decisiones 1 a 6 y N-A a N-F APROBADAS (27/11/2026) con las recomendaciones del informe** (registradas en la orden O02). Sin construir aún: falta el veredicto de arranque del Lote 1 y de la mini-orden O02-H, y el resultado del script SQL; no ejecutar nada contra producción sin autorización.
**O02 Lote 3 construido (27/11/2026), en la rama, sin fusionar:** motor de aprobación (`database/orders.py`, `checkout_service.approve`, `void_subscription`); sin conectar a handlers ni al bot (no cambia producción); 196 pruebas `unittest` OK; requiere la migración del Lote 2 ya ejecutada. Pendiente de decisión: reactivar una `cancelled`. Detalle: `docs/subfases/o02-lote3-motor.md`.
**O02 Lote 2 construido (27/11/2026), en la rama, sin fusionar y SIN ejecutar en Supabase:** migración `docs/migraciones/o02-lote2-checkout.sql` (tablas `checkout_orders` y `subscription_periods`, función atómica `approve_checkout_order`, RLS y permisos) probada en un PostgreSQL local desechable; **la ejecuta el Orquestador antes de desplegar código que la use** (el flag `CHECKOUT_V2_ENABLED` sigue apagado, así que el código actual no depende de ella). Detalle, pasos y preguntas abiertas: `docs/subfases/o02-lote2-migracion.md`.
**O02 Lote 1 construido (27/11/2026), en la rama `claude/keen-mendel-vn6j7b`, sin fusionar:** `utils/dates.py`, `services/checkout_service.py` y `services/locks.py`, lógica pura sin llamadores en producción (no cambia el bot ni el panel); 169 pruebas `unittest` OK. Detalle y 4 preguntas abiertas: `docs/subfases/o02-lote1-servicios-base.md`.
**O02-H construida (27/11/2026), en la rama `claude/keen-mendel-vn6j7b`, pendiente de revisión del PR y veredicto; no está en `master`.** Guardas de aprobar/rechazar (solo `pending_payment`), `cancel_and_buy` libera perfil y express procesa las `expired` de <24 h. Sin migración ni variable nueva; 115 pruebas `unittest` OK. Detalle: `docs/subfases/o02h-guardas-pagos-y-perfiles.md`.
Órdenes redactadas (borradores, sin iniciar): `docs/arquitecto/ordenes/` — O01 (Lote 1), O02 pagos y estados, O03 rendimiento y rediseño del panel, O04 API y PWA, O05 (Lotes 0 a 4 y 6).
Decisiones del Orquestador (27/11/2026): corte de mora **opción B** (sin corte automático; corte manual; recordatorios D+1 a D+6; `CLAUDE.md` y la tabla de §2 se actualizan al cerrar O02), agente de IA **solo texto** y Impeccable como auditor de diseño. Hasta entonces el código y la tabla de §2 siguen describiendo el corte D+7.
Hallazgos del rescate y de la auditoría que requieren decisión: repositorio público (R00-1), TCP Proxy del Postgres (R00-2), rotar claves (R01-1). Verificado en Railway el 15/11/2026: sin `watchPatterns` (cualquier fusión redespliega y reinicia el bot).

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
| 2026-11-27 | O02 Lote 3 (motor de aprobación: repositorio de órdenes, `approve`, `void_subscription`; sin conectar) — **en rama, sin fusionar** |
| 2026-11-27 | O02 Lote 2 (migración de órdenes de pago e historial; escrita y probada en local, **sin ejecutar en Supabase**) — **en rama, sin fusionar** |
| 2026-11-27 | O02 Lote 1 (servicios base: fechas, checkout, candado) — **en rama, sin fusionar** |
| 2026-11-27 | O02-H (guardas de pagos y fuga de perfiles) — **en rama, sin fusionar** |
| 2026-11-27 | O02 Lote 0 (decisiones y diagnóstico de estados, solo documentación y SQL de lectura) — **en rama, sin fusionar** |
| 2026-11-27 | O01 Lote 0 (seguridad del panel) y O05 Lote 5 (confirmaciones de la IA del admin) — **en rama, sin fusionar** |
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
