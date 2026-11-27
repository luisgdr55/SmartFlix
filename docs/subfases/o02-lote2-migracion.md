# O02 Lote 2 — Migración (órdenes de pago, historial y aprobación atómica)

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **en la rama, sin fusionar** (viaja en el PR #2).
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md` (Lote 2). Arranque aprobado por el Orquestador el 27/11/2026.

> **Nada de esto se ha ejecutado contra Supabase ni producción.** Se probó en un PostgreSQL 16 local desechable con datos ficticios. La migración la ejecuta el Orquestador, en el editor SQL de Supabase, antes de desplegar código que la use.

## Entregables
- `docs/migraciones/o02-lote2-checkout.sql`: una transacción, idempotente, con precondición de columnas, verificación (solo `SELECT`) y reversa comentada.
- `supabase_schema.sql`: se agregan las tablas, columnas e índices nuevos (referencia; los triggers, RLS, permisos y funciones viven en la migración).
- `tests/sql/` (`run_o02_lote2.sh`, helpers, escenarios, README): pruebas de la migración en una base desechable `o02_test`; los archivos **abortan si la base tiene otro nombre**, porque borran datos.

## Qué crea (solo agrega; no toca datos existentes)
1. `checkout_orders` (según la orden, más `decided_at` y `result` para devolver lo aplicado en una 2.ª llamada) con `CHECK` de estados, `total_usd > 0`, índice único de `payment_reference` aprobada y un trigger que refleja la máquina de estados (aprobada inmutable salvo anular; `rejected`/`cancelled`/`voided` terminales; no se borra una aprobada).
2. `subscription_periods` (historial; un trigger prohíbe `UPDATE` y `DELETE`; único `(order_id, subscription_id)`; sin `CASCADE` hacia `subscriptions`).
3. `subscriptions.void_reason` y `voided_at` (el estado `voided` aún no se usa) e índices `(user_id, platform_id, status)` y `(status, end_date)`.
4. `approve_checkout_order(p_order_id, p_actor, p_reason, p_payment_reference)` y dos auxiliares internas (`checkout_pick_profile`, `checkout_release_profile`).
5. RLS activado **sin políticas** en las dos tablas nuevas y `EXECUTE` de las funciones solo para `service_role` (en Supabase las funciones nuevas quedan visibles para `anon` por defecto; se revoca).

## Cómo se comporta `approve_checkout_order`
- Bloquea la orden (`FOR UPDATE`): dos aprobaciones simultáneas se serializan. Ya aprobada → devuelve lo aplicado con `already_applied: true`, sin efectos nuevos. `expired` exige motivo (comprobante tardío); otros estados → `invalid_state`.
- **Todo o nada**: cualquier fallo lanza excepción y revierte toda la función; la orden sigue pendiente. Códigos (campo `MESSAGE`, `DETAIL` en JSON): `order_not_found`, `actor_required`, `reason_required`, `invalid_state`, `invalid_manifest`, `subscription_not_found`, `subscription_not_owned`, `subscription_not_renewable`, `subscription_not_overdue`, `profile_unavailable`, `duplicate_reference`. Lote 3 los traduce a mensajes humanos con salida.
- Por ítem (siempre por `subscription_id`, nunca por usuario+plataforma): `renovacion` extiende desde el `end_date` actual (fin de día de Caracas + N meses de calendario), reinicia `reminder_sent`, `expiry_notified` y `debt_reminder_count` (N-B) y comprueba el perfil (N-A: lo conserva si es suyo; si lo usa otra suscripción activa, asigna otro y lo reporta con `profile_changed`); `nueva` crea la suscripción activa con perfil libre y el periodo arranca en la aprobación (N-F); `cambio` solo sobre vencida: libera el perfil origen con guardas y rota PIN, asigna otro y muta la misma suscripción.
- Un renglón en `subscription_periods` por ítem y `result` guardado en la orden.

## Verificación (ejecutada, en PostgreSQL 16 local; datos ficticios)
- Migración aplicada **dos veces** sin errores (idempotente) y la sección de verificación devuelve lo esperado (RLS activo, 0 filas, 3 funciones visibles solo para el dueño y `service_role`, 7 índices).
- Escenarios (`tests/sql/run_o02_lote2.sh`): nueva mensual y express, renovación de vencida con N periodos, dos servicios de la misma plataforma (solo cambia el renovado), fin de mes y bisiesto, medianoche UTC/Caracas, perfil reasignado a otro cliente / liberado / sin stock, cambio de plataforma, orden mixta atómica (el 3.er ítem falla y revierten los dos primeros), idempotencia, estados y motivos, suscripción ajena o inexistente, importes que no suman, referencia duplicada que revierte los efectos, inmutabilidad del historial y de las órdenes aprobadas. **Todos en verde.**
- Comprobé que los escenarios detectan defectos: no reiniciar los avisos, ignorar que otra suscripción activa usa el perfil y quitar la idempotencia hacen fallar al menos un escenario.
- La aritmética de meses de Postgres coincide con `utils.dates.add_months` en **4.216 casos, 0 diferencias** (una vez, manual).
- Dos aprobaciones simultáneas: la segunda esperó el candado y devolvió `already_applied`; quedó una suscripción y un periodo.
- `anon` no puede ejecutar la función; la precondición detiene la migración sin cambiar nada si falta una columna.
- `supabase_schema.sql` carga completo en una base limpia. La suite Python no cambió (169 OK).

## No verificado
- **Todo contra Supabase real**: versión de Postgres, roles reales (`anon`/`authenticated`/`service_role`), que la service role tenga privilegios sobre las tablas nuevas (en Supabase los recibe por defecto en `public`; localmente no se probó), RLS efectivo.
- La firma exacta de `sb.rpc(...)` en `supabase==2.7.4` y cómo expone `MESSAGE`/`DETAIL` de las excepciones (Lote 3).
- Que existan en producción `profiles.reserved_for` y `reserved_until` (el repo no los declara): la propia migración se detiene si faltan; el BLOQUE 0 del script de diagnóstico del Lote 0 también lo muestra.
- El rendimiento con tu volumen real (la tabla es pequeña; `CREATE INDEX` toma un candado de escritura breve).

## Pasos para el Orquestador
1. Ejecutar el BLOQUE 0 del script de diagnóstico (`docs/subfases/o02-lote0-consultas-anomalias.sql`) y confirmar que `profiles.reserved_for/reserved_until` existen; si el BLOQUE 0b muestra una `CHECK` sobre `subscriptions.status`, avisarme (afecta al estado `voided`).
2. En horario de baja actividad, pegar y ejecutar **todo** `docs/migraciones/o02-lote2-checkout.sql` en el editor SQL (está en una transacción: o queda completo o no queda nada). Si falla, pegar solo el mensaje de error en el chat.
3. Ejecutar la sección «VERIFICACIÓN» del final del archivo (solo `SELECT`) y pegar el resultado.
4. Reversa, si hiciera falta: sección «REVERSA» al final (comentada); solo mientras las tablas nuevas estén vacías.

## Consecuencias a tener en cuenta
- **Borrar una suscripción o un cliente con periodos registrados fallará** (clave foránea sin `CASCADE`): es intencional («borrado solo para suscripciones sin periodos»). Hoy no hay periodos, así que no cambia nada hasta que se enciende el flag; el Lote 5 trae la anulación con motivo como salida.
- Si el panel o `/renovar` pasan una referencia fija como `PANEL-MANUAL` a `p_payment_reference`, la segunda orden aprobada chocará con el índice único (`duplicate_reference`). El Lote 3 debe enviar `NULL` o una referencia real.
- La función es `SECURITY INVOKER`: la llama la service role (bot y panel). No se ofrece a `anon` ni `authenticated`.

## Preguntas abiertas
1. **Reactivar una `cancelled`** (de la revisión del Lote 1; aún sin decidir): ¿arranca desde el `end_date` viejo o desde la aprobación? Antes del Lote 3. Hasta entonces la función la rechaza.
2. ¿El `CHECK` de `subscriptions.status` (hallazgo N13) se agrega en esta migración o después? Lo dejé fuera hasta ver el resultado del BLOQUE 0b.

## Siguiente
Lote 3 (motor): `database/orders.py` y `checkout_service.approve` llamando a la función por `rpc`. Depende de que ejecutes la migración y de la pregunta 1.
