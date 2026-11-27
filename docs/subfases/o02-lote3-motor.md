# O02 Lote 3 — Motor de aprobación

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **en la rama, sin fusionar** (viaja en el PR #2).
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md` (Lote 3). Arranque aprobado por el Orquestador el 27/11/2026 con las recomendaciones del informe del Lote 2.

> **No conectado a ningún handler ni al bot: con el flag `CHECKOUT_V2_ENABLED` apagado (y no existe aún ninguna orden) el comportamiento en producción no cambia.** Requiere que el Orquestador haya ejecutado la migración del Lote 2 antes de usarlo; sin ella, `approve` falla con un error genérico registrado en el log (la función SQL no existe).

## Cómo se aplicaron las recomendaciones del Lote 2
- **`CHECK` de `subscriptions.status`:** se deja **fuera** hasta ver el resultado del Bloque 0b del script de diagnóstico (el informe lo proponía así). Hoy la columna acepta cualquier texto; `voided` funciona sin él.
- **Reactivar una `cancelled`:** el informe no traía una recomendación, así que **no la decidí**: sigue **abierta**. El motor la rechaza (`subscription_not_renewable`), igual que la función SQL. No bloquea el Lote 3; hay que resolverla antes de poder reactivar clientes cortados.

## Qué entrega
- `services/checkout_service.py` (puro): `approve(order_id, actor, reason, payment_reference)` llama a la función atómica y devuelve `{ok, already_applied, order_id, items}` (una segunda llamada devuelve lo aplicado sin efectos). `ApprovalError` con `code` estable y `user_message` apto para el admin (todos dicen qué hacer y, como la aprobación es atómica, que no se cambió nada); `parse_approval_error`; `approval_notices(result)` resume lo que el admin debe atender (perfil cambiado en una renovación, PIN rotado en un cambio de plataforma, perfil de origen no liberado).
- `database/orders.py` (nuevo): `create_order` (valida el manifiesto, vence a las 24 h), `get_order`, `get_pending_order_for_user`, `list_pending_orders`, `attach_receipt` (solo mientras esté pendiente), `decide_order` (rechazar con motivo, cancelar o vencer, con UPDATE condicional al estado leído: si otro proceso decidió primero devuelve False), `expire_stale_orders` (sin programar: el job es del Lote 6) y `approve_order` (el `rpc`).
- `database/subscriptions.py::void_subscription(sub_id, actor, reason)`: anula con motivo (estado `voided`, `void_reason`, `voided_at`; nunca borra) y libera el perfil con las guardas de `release_profile_for_cut` (no lo hace si otra suscripción activa lo usa). Acepta cualquier estado de origen, es idempotente y devuelve el PIN anterior y el nuevo para avisar al admin. Sin llamadores todavía: el Lote 5 la conecta a la pantalla de anomalías y registra la acción en `admin_log`.

## Decisiones de diseño
1. **No se usa el candado Redis (`services/locks.py`) al aprobar**, aunque la orden lo mencionaba. La garantía real es el candado de fila de la función SQL (probado con dos sesiones simultáneas en el Lote 2); y un candado que falla cerrado dejaría al admin sin poder aprobar pagos cuando Redis cae. El candado sigue disponible para los jobs (Lote 6).
2. Un fallo de conexión al aprobar se informa como `unavailable` («vuelve a pulsar Aprobar, es seguro»): la petición pudo haberse confirmado y perderse la respuesta; repetir es seguro por la idempotencia.
3. **El cableado a Telegram, panel y `/renovar` NO se hizo aquí.** Hoy no puede existir ninguna orden (las crea el bot del Lote 4) y las pantallas de aprobación por orden son del Lote 5; cablearlas antes dejaría botones sin datos. Con el flag apagado, los botones actuales (`admin:approve:<sub_id>`) siguen igual.
4. **No se cambiaron `cancel_subscription` ni `delete_subscription`** (la orden pedía liberar perfil y rotar PIN al cancelar/anular/borrar): modificarlos hoy cambiaría flujos con clientes reales (borrados tras renovar, rechazos, panel). Queda la función nueva `void_subscription` y su cableado, junto con «borrado solo para suscripciones sin periodos», en el Lote 5.
5. La reversa de una orden **aprobada** (asiento contrario) y los avisos al cliente tras aprobar (mensaje con el acceso, `increment_user_purchases`) son del Lote 5; el motor devuelve los datos para armarlos.

## Verificación (ejecutada)
- `python -m unittest discover -s tests -t .`: **196 pruebas OK** (169 de antes + 27 nuevas en `tests/test_checkout_engine.py`, con una base y un `rpc` falsos): creación y TTL, manifiesto manipulado que no se escribe, orden pendiente más reciente, comprobante solo en pendientes, rechazo con motivo y actor, carrera perdida, vencimiento, parámetros exactos del `rpc`, traducción de **cada** código de error de la función (más errores desconocidos, función inexistente, `details` no JSON y caída de conexión), entrada inválida que no llega al repositorio, avisos para el admin, y anulación (todos los estados, sin perfil, idempotente, estado cambiado, perfil compartido).
- Comprobé que las pruebas detectan defectos: quitar la validación del manifiesto, adjuntar comprobante a cualquier estado o quitar la idempotencia de anular hace fallar la suite.
- `python -m compileall` e `import main` / `import database.orders`: OK. Sin dependencia nueva (`postgrest.APIError` ya viene con `supabase==2.7.4`).
- La función SQL a la que llama se probó a fondo en el Lote 2 (PostgreSQL local). La lectura de `APIError.message/details` se verificó en el código de `postgrest 0.16.11`.

## No verificado
- Nada contra Supabase/PostgREST real: que el `rpc` devuelva el `jsonb` tal cual como diccionario, y que PostgREST entregue `message` = código y `details` = JSON de nuestras excepciones `P0001` (leído en la librería, no ejecutado).
- Que la service role tenga permiso sobre `checkout_orders` y sobre la función (la migración los concede; sin ejecutar).
- Las pruebas usan una base falsa: el filtro `lte` sobre `expires_at` y el `UPDATE` condicional dependen de PostgREST.

## Preguntas abiertas
1. **Reactivar una `cancelled`** (sigue abierta; ver arriba): ¿desde el `end_date` viejo o desde la aprobación?
2. ¿Conecto `void_subscription` y el borrado «solo sin periodos» en el Lote 5 junto a la pantalla de anomalías, como indica la orden? (recomendado)

## Siguiente
Lote 4 (cliente en el bot): crea una orden en vez de N suscripciones, con el flag `CHECKOUT_V2_ENABLED` (por defecto `false`; la orden autoriza crear solo esa variable), y reserva de perfil de 30 min solo para ítems `nueva`. Antes: ejecutar la migración del Lote 2 y decidir la pregunta 1.
