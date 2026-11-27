# O02-H — Guardas de pagos y fuga de perfiles (hotfix previo al motor de O02) · 🔴 · rama `fix/o02h-guardas-pagos-perfiles`

**Estado:** BORRADOR para veredicto del Orquestador (27/11/2026) · **Origen:** decisión N-E aprobada del Lote 0 de O02 (`docs/subfases/o02-lote0-decisiones.md`) · **Evidencia:** `docs/subfases/o02-lote0-mapa-de-estados.md` §3c, §3d, §3e y hallazgos N3, N4, N5.
**Depende de:** nada (no requiere O01 ni la migración de O02). **No bloquea** a O02; el motor de O02 (Lotes 1 a 4) reemplazará estas guardas más adelante.

## Objetivo
Cerrar, con cambios mínimos y sin esquema nuevo, cuatro fallos que hoy pierden o duplican perfiles en producción:
1. **Aprobar** un pedido que ya no está pendiente (doble clic, botón viejo en el chat) asigna un segundo perfil y deja el primero ocupado sin dueño.
2. **Rechazar** un pedido que ya está activo lo **borra** y deja su perfil ocupado.
3. **`cancel_and_buy`** (botón del cliente) cancela sus suscripciones vencidas **sin liberar el perfil**.
4. **Express**: la carrera entre `auto_expire` (15 min) y `job_express_release` (5 min) deja ~15-20 % de las express vencidas con el perfil ocupado y el PIN sin rotar (cifra estimada, [H] en el mapa).

## Reglas de este arreglo
- **`auto_expire` y el corte D+7 NO se tocan** (se retiran juntos en el Lote 6 de O02; apagar solo `auto_expire` reactivaría el corte automático y cortaría clientes reales).
- Sin esquema nuevo, sin dependencia nueva, sin variable de entorno nueva, sin cambio en lo que ve el **cliente** salvo lo indicado en §«Lo que ve el cliente» (nada).
- Solo cambia lo que ve el **admin**: mensajes de aviso nuevos, siempre con botón de salida.
- Hallazgos fuera de alcance (N1, N2, N6, N7, N9 a N14, las otras cinco implementaciones de «liberar perfil») **se reportan, no se corrigen**.

## Análisis de impacto (CLAUDE.md, «Antes de cambiar»)
| Qué toca | Detalle |
|---|---|
| Handlers | `handle_admin_approve_payment` (`bot/handlers/admin.py:839`), `handle_admin_reject_payment` (`:1006`), `_handle_cancel_and_buy` (`main.py:104`). |
| Panel | `payment_approve` (`admin_panel/router.py:1533`) y `payment_reject` (`:1676`). |
| Jobs | `job_express_release` (`scheduler/jobs.py:67`) y su consulta `get_expired_express_subscriptions` (`database/subscriptions.py:367`). Misma frecuencia (5 min); **no se agrega ni retira ningún job**. |
| Tablas | `subscriptions` (lectura de `status`, `profile_id`; el mismo `UPDATE` que ya existe en `confirm_subscription`, ahora condicional), `profiles` (los mismos campos que ya escribe el corte: `status`, `pin`, `last_released`, `reserved_for/until`). Ningún cambio de columnas. |
| Datos de clientes | Sí, indirectamente: liberan perfiles y rotan PIN de clientes **sin servicio vigente**. Por eso se exigen las guardas de §«Seguridad del cliente vigente». |
| Retrocompatibilidad | Total para flujos felices: aprobar/rechazar un pendiente, renovar y el release de express recién vencida se comportan igual. |

## Cambios (qué sí incluye)

### H1 — Aprobar solo desde `pending_payment`
- **Telegram** (`admin.py:839`) y **panel** (`router.py:1533`): justo después de cargar la suscripción, si `status != 'pending_payment'`, **no se hace ningún cambio**. Telegram edita el mensaje con «Este pedido ya no está pendiente (estado: X)» y los botones «⏳ Pendientes» y «🔙 Atrás» (sin callejón); el panel redirige a `/panel/payments` con un error legible.
- **Compra nueva** (`confirm_subscription`, `database/subscriptions.py:231`): el `UPDATE` pasa a ser condicional (`… .eq("status", "pending_payment")`) y devuelve `False` si no actualizó ninguna fila. Así dos aprobaciones simultáneas (panel + Telegram) no asignan dos perfiles: la segunda recibe el aviso de H1. En Telegram, `assign_profile` solo se llama si `confirm_subscription` devolvió `True` (hoy se llama aunque falle; `admin.py:963-964`).
- **Renovación**: solo la guarda de estado (el borrado del pendiente ya impide el doble clic secuencial; la carrera simultánea en renovaciones la cubre el Lote 3 de O02, se declara como residual).
- `expired_payment` **queda bloqueado** para aprobar (ver Pregunta 1).

### H2 — Rechazar solo desde `pending_payment`
- **Telegram** (`admin.py:1006`) y **panel** (`router.py:1676`): cargar la suscripción **antes** de borrar (el panel hoy borra primero, `router.py:1686-1687`); si no existe o `status != 'pending_payment'`, no se borra nada y se avisa con salida (mismos criterios que H1). El texto al cliente de «comprobante no aprobado» **solo** se envía si efectivamente se rechazó.

### H3 — `cancel_and_buy` libera el perfil de lo que cancela
- Nuevo helper en `database/profiles.py`: `release_profile_for_cut(profile_id, exclude_sub_id)`; devuelve `{"old_pin", "new_pin", "profile_name", "account_email"}` si liberó, o `None` si **no** debió liberar (ver §Seguridad). Libera con la regla vigente del corte: `status='available'`, PIN nuevo de 4 dígitos, `last_released`, limpia `reserved_for/until`.
- `_handle_cancel_and_buy` (`main.py:104-113`): en lugar del `UPDATE` masivo, lee las `expired` del cliente (`id, profile_id, platform`), por cada una llama al helper y luego la pasa a `cancelled`; **por cada perfil liberado envía al admin el mismo mensaje que el corte** (PIN anterior, PIN nuevo, cuenta; para que cambie el PIN en la plataforma). Si el helper devuelve `None`, la suscripción se cancela igual (no se bloquea al cliente) y se avisa al admin de «perfil no liberado: lo usa otra suscripción activa».
- El mensaje y la navegación al cliente **no cambian**.

### H4 — Express: `job_express_release` también procesa las que `auto_expire` ganó
- `get_expired_express_subscriptions` acepta `status IN ('active','expired')`, `plan_type='express'`, `end_date <= ahora`, **y** para las `expired` solo las de **las últimas 24 h** (`end_date >= ahora − 24 h`). Se agrega `status` del perfil a lo que devuelve.
- En `job_express_release`, para filas `expired`: se procesan **solo si** el perfil sigue `occupied` **y** `release_profile_for_cut` no encuentra otra suscripción `active` con ese perfil. Para filas `active` el flujo no cambia. Tras liberar: aviso al admin, aviso al cliente (`send_express_expired`) y cola, como hoy.
- Las express `expired` con perfil ocupado de **más de 24 h** (fuga histórica) **no** se tocan aquí: se sanean en el Lote 7 de O02 con autorización, con el recuento de `express.vencida_perfil_ocupado` del script SQL del Lote 0. Motivo: evitar que el primer despliegue libere de golpe perfiles viejos y escriba a clientes meses después.

### Seguridad del cliente vigente (obligatorio en H3 y H4)
`release_profile_for_cut` **no libera ni rota PIN** si existe otra suscripción `active` (distinta de `exclude_sub_id`) cuyo `profile_id` sea ese perfil, porque el perfil ya pudo haberse entregado a otro cliente (las suscripciones vencidas conservan el `profile_id`, mapa §3c). En ese caso devuelve `None` y el llamador avisa al admin. Una express liberada por el propio job queda con el perfil `available`, así que **no se vuelve a procesar** (idempotente por el filtro de perfil `occupied`).

### Pruebas y documentación
- Lógica pura en `services/payment_guards.py` (nuevo, sin dependencias): `can_decide_payment(status)`, `profile_is_safe_to_release(profile_status, other_active_count)`, `express_release_candidates(rows, now)`. Suite `unittest` en `tests/test_payment_guards.py` con repositorio falso (`tests/support.py`).
- Cierre: `docs/subfases/o02h-guardas-pagos-y-perfiles.md`, `docs/ESTADO.md` (§0, §1 en ≤ 8 líneas, fila de §3) y marcar N3, N4, N5 en el mapa del Lote 0 como corregidos.

## NO incluye
`auto_expire`, corte D+7 y cualquier cambio de scheduler · reiniciar avisos al renovar (N1) · reasignar el perfil al renovar una `cancelled` (N2) · fecha de inicio al aprobar (N6) · PIN en la cancelación de la IA (N7) · textos de mora (N-D) · tasa de respaldo (N-C) · `expired_payment` y su salida administrativa · unificar las seis formas de liberar un perfil · saneamiento de datos existentes · cualquier migración, dependencia o variable nueva · `streamvip/` · UI del panel (plantillas).

## Lo que ve el cliente
Nada cambia. El mensaje de «comprobante no aprobado» solo deja de enviarse cuando el rechazo no procede (antes se enviaba aunque se borrara una suscripción activa). `cancel_and_buy` y el aviso de express vencida se envían como hoy.

## Despliegue y reversa
- Sin migración, sin flag, sin variable nueva. Railway reinicia el bot en la fusión (sin `watchPatterns`): **fusionar en horario de baja actividad** y revisar estado y logs de Railway después.
- Verificar tras desplegar (lo hace el Orquestador): un pedido de prueba aprobado dos veces (la segunda debe avisar sin cambiar nada), un rechazo sobre un pedido ya aprobado (no debe borrarlo), `cancel_and_buy` con una cuenta de prueba, y que `job_express_release` siga corriendo (log «Running job: express_release»).
- Reversa: revertir el PR (sin cambios de datos que deshacer; los perfiles liberados por H3/H4 ya fueron liberados con la regla normal del corte).

## Casos extremos que las pruebas deben cubrir
Aprobar dos veces la misma compra nueva · aprobar una renovación ya aprobada (pendiente borrado) · rechazar una `active` · rechazar una inexistente en el panel · aprobar una `expired_payment` · `cancel_and_buy` con perfil usado luego por otra suscripción activa · `cancel_and_buy` sin perfil asignado · express `expired` con perfil `available` (no se reprocesa) · express `expired` de hace 25 h (no se procesa) · express `active` recién vencida (flujo de siempre) · perfil `expired` express cuyo perfil lo usa otra suscripción `active` (no se libera; aviso al admin) · fallo del `UPDATE` condicional.

## Criterios de aceptación
- Dada una compra nueva ya `active`, cuando el admin pulsa «Aprobar» de nuevo, entonces no se asigna otro perfil ni se envían más mensajes al cliente.
- Dada una suscripción `active`, cuando el admin pulsa «Rechazar» sobre su aviso viejo, entonces no se borra ni se avisa al cliente; el admin ve el estado y una salida.
- Dado un cliente con una suscripción vencida y su perfil ocupado, cuando pulsa «cancelar y comprar», entonces el perfil queda libre con PIN nuevo y el admin recibe el aviso con ambos PIN.
- Dada una express vencida hace < 24 h que `auto_expire` marcó `expired`, cuando corre el job, entonces su perfil se libera una sola vez.
- Dado un perfil que ya usa otra suscripción activa, cuando se cancela o expira una suscripción antigua que lo apuntaba, entonces el perfil **no** se libera y el admin recibe un aviso.
- `git diff --stat` solo con los archivos listados; `python -m compileall` y `import main` limpios; la suite `unittest` en verde (lo que dependa de Telegram/Supabase lo prueba el Orquestador y el cierre lo declara).

## Archivos esperados
`bot/handlers/admin.py` · `admin_panel/router.py` (solo `payment_approve` y `payment_reject`) · `main.py` (solo `_handle_cancel_and_buy`) · `database/subscriptions.py` (`confirm_subscription`, `get_expired_express_subscriptions`) · `database/profiles.py` (helper nuevo) · `scheduler/jobs.py` (solo `job_express_release`) · `services/payment_guards.py` (nuevo) · `tests/test_payment_guards.py` (nuevo) · `docs/ESTADO.md` · `docs/subfases/o02h-guardas-pagos-y-perfiles.md` · mapa del Lote 0.

## Plan
Un solo lote, un commit por grupo (H1+H2, H3, H4, pruebas, docs). 🔴 por tocar aprobación de pagos y perfiles de clientes reales.

## Preguntas abiertas para el Orquestador
1. **`expired_payment`.** Hoy, por accidente, un botón viejo de Telegram permite aprobar un pedido que ya caducó (el cliente pagó tarde). Con H1 eso queda bloqueado y el pedido no tiene salida hasta el motor de O02. ¿Aceptas bloquearlo (recomendado, consistente con el veredicto N-E), o prefieres permitir aprobar `expired_payment` solo en la ruta de compra nueva y con un paso extra de confirmación? Para decidir sirve el conteo `exp_pago.con_comprobante` del script SQL.
2. **Ventana de 24 h de H4.** ¿Correcto o prefieres otra (por ejemplo 6 h o 48 h)? Lo que quede fuera se sanea en el Lote 7.
3. **Aviso al admin en `cancel_and_buy`.** ¿El aviso con PIN anterior/nuevo va a todos los admins (`send_to_admins`) como los cortes? Recomiendo que sí.

## Corrección a mi informe anterior
En el informe de decisiones (N-E) dije «`get_expired_express_subscriptions` acepta `active` o `expired`». Eso solo es seguro con las guardas de esta orden: sin la ventana de 24 h y sin el filtro de «perfil ocupado y sin otra suscripción activa», el job volvería a procesar cada 5 minutos express ya liberadas y podría rotar el PIN de un perfil que ya es de otro cliente.
