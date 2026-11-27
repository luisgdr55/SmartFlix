# FLUJOS — Máquinas de estado (cierre de O02)

Fuente: código de la rama de O02. Detalle y rutas `archivo:línea` del estado previo: `docs/subfases/o02-lote0-mapa-de-estados.md`. Lo marcado «flag» solo rige con `CHECKOUT_V2_ENABLED=true`.

## Suscripción (`subscriptions.status`)
- `active` → vigente o **en mora**: la mora no es un estado, se deriva de `end_date` (Caracas) con `is_overdue`.
- `cancelled` → corte manual del admin (libera el perfil con PIN nuevo si nadie más lo usa) o anulación de una orden aprobada.
- `pending_payment`, `expired`, `expired_payment` → **legado**: nada nuevo los escribe; las lecturas los aceptan (`expired` se lee como mora). Saneamiento: `scripts/o02_saneamiento.py`.
- Reactivar una `cancelled` está **pendiente de decisión** (fecha de inicio).

## Mora y avisos (sin corte automático)
`D-3` aviso previo · `D+0` vencida (solo últimas 48 h) · `D+1..D+6` un recordatorio por día · desde `D+7` solo el **resumen diario al admin**; el servicio sigue activo hasta que el admin corte a mano. El corte advierte si hay un pago pendiente. Cada aviso se reclama de forma atómica antes de enviarse (sin duplicados entre instancias) y los jobs usan candado Redis.

## Orden de pago (`checkout_orders.status`, flag)
`pending` → `approved` (motor atómico en SQL: crea/renueva suscripciones y periodos) · `rejected` · `expired` (job `expire_orders`, 24 h) · `cancelled` (cliente) · aprobación **parcial**: la orden se divide y la hija (`parent_order_id`) queda `pending` · **anulación** de una aprobada: asiento contrario (periodos `reversa`). Transiciones validadas por `check_order_transition`. Solo el admin aprueba, rechaza, divide o anula; el OCR solo marca señales.

## Carritos
`cart:{tid}` y `renewal_cart:{tid}` viven en Redis (TTL 30 min). Con el flag encendido el cliente crea una orden por pedido en vez de usar los carritos.

## Perfil
`available` ↔ `occupied` (reserva 30 min con `reserved_for/reserved_until`). Liberación manual o por express (24 h): `release_profile_for_cut` no libera si el perfil no está ocupado o lo usa otra suscripción activa.
