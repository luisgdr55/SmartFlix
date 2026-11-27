# O02 Lote 0 — Mapa de estados verificado del código actual

Rama `claude/keen-mendel-vn6j7b` · 27/11/2026 · **solo lectura, sin cambios de código**.
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md`.

**Alcance y límites.** Todo lo de este documento sale de leer el código del repo (rutas `archivo:línea` sobre la rama indicada). No se ejecutó nada contra Supabase, Redis ni Railway; no se vio ningún dato real. Cada hallazgo lleva una marca:
- **[C]** confirmado por lectura del código (el comportamiento se deduce de lo escrito).
- **[H]** hipótesis: depende de datos reales, de tiempos de ejecución o de cómo Supabase devuelve fechas; lo resuelve el script `docs/subfases/o02-lote0-consultas-anomalias.sql`.
- **[NV]** no verificado: no se pudo comprobar leyendo.

`supabase_schema.sql` está **desactualizado** respecto al código (no trae `profiles.reserved_for`, `reserved_until`, `is_extra_member`, `extra_email`, `extra_password`). El esquema real en producción es **[NV]**; el script SQL lo verifica primero.

---

## 1. Resumen: los hallazgos que más pesan

1. **El corte D+7 no puede dispararse en la práctica [C/H].** `auto_expire` (cada 15 min) pasa a `expired` toda suscripción `active` vencida; los avisos de gracia y el corte filtran `status='active'` (§3a). Una suscripción vencida deja de ser visible para el job de las 9:00 casi siempre, así que `debt_reminder_count` no llega a 6. Además, el helper `has_pending_renewal_for_platform`, que `CLAUDE.md` presenta como la guarda anti-corte, **no lo llama ningún job** [C] (§3a). La orden acierta en el diagnóstico y es incluso más grave: tampoco hay guarda.
2. **Renovar reutiliza un `profile_id` que ya pudo ser reasignado [C].** Al aprobar una renovación de una suscripción `cancelled` (cortada por el scheduler o por el admin), el código reusa el `profile_id` guardado sin comprobar el estado del perfil y sin llamar `assign_profile` (§3c). Si el perfil se liberó y se entregó a otro cliente, **dos clientes comparten el mismo perfil**. No está en la orden.
3. **La aprobación y el rechazo no validan el estado actual [C].** Rechazar borra la suscripción aunque ya esté `active`, sin liberar el perfil; aprobar dos veces una suscripción nueva asigna un segundo perfil y deja el primero ocupado para siempre (§3d). Coincide con la orden.
4. **Hallazgo nuevo — las renovaciones nunca reinician `reminder_sent`, `expiry_notified` ni `debt_reminder_count` [C].** `confirm_renewal_subscription` solo actualiza estado, perfil, referencia, fecha de confirmación y `end_date` (`database/subscriptions.py:131-137`); ningún otro punto del código los pone en `False/0` salvo al insertar (grep completo, §6). Tras la primera renovación de una fila, no vuelve a salir el aviso D-3 ni el D+0, y el contador de deuda arranca ya consumido. **Afecta directamente el Lote 3 (la función atómica debe reiniciarlos) y el Lote 6.**
5. **`expired` significa cuatro cosas distintas [C]**: vencida con perfil aún ocupado (`auto_expire`), corte suave con perfil ocupado y PIN rotado (`router.py:1226`), liberada con perfil libre (`router.py:1298`, `jobs.py:94`). No se puede saber por el estado si el perfil está libre. De ahí la decisión 2 (informe de decisiones).

---

## 2. Inventario por valor de `subscriptions.status`

Valores hallados en el código: `pending_payment`, `active`, `expired`, `expired_payment`, `cancelled`. No hay ningún otro (`voided` aún no existe). El campo es `VARCHAR(50)` sin `CHECK` (`supabase_schema.sql:107`), así que el panel puede escribir cualquier texto (ver `active`/`edit` abajo).

### `pending_payment` (defecto en `supabase_schema.sql:107` y `database/models.py:91`)
**Quién lo escribe**
- `create_subscription` — `database/subscriptions.py:34`. Llamado desde: compra individual `bot/handlers/subscription.py:240`; carrito de compra `subscription.py:593`; carrito de renovación `subscription.py:945`; express `bot/handlers/express.py:164`.
- Panel: el formulario de edición lo ofrece como opción (`admin_panel/templates/user_detail.html:261`) y `subscription_edit` guarda el `status` recibido sin validar (`admin_panel/router.py:1153-1154`).

**Quién lo lee**
- `get_pending_subscriptions` (`subscriptions.py:331-345`) → pantallas de pendientes: bot `admin.py:338`, `ai_admin.py:330`; panel `router.py:266` (dashboard), `:1482` (pagos), `:2167`.
- `get_user_pending_subscription` (`:178-194`) → `subscription.py:323`, `payment.py:41` (restaura el estado del comprobante).
- `get_user_active_subscriptions` (`:82-97`, incluye pendientes) y `ai_admin.py:720`.
- `get_user_attention_subscriptions` (`:144-175`, lista `pending`) → `start.py:144` y `subscription.py:50`.
- `delete_pending_subscriptions_for_platform` (`:288-307`) — lee y **borra**.
- `has_pending_renewal_for_platform` (`:197-214`) — **sin ningún llamador** [C].
- `analytics.py:65` (contador de pagos pendientes).
- `cancel_expired_pending_subscriptions` (`:629-646`) → los pasa a `expired_payment`.

**Quién lo borra:** rechazo de pago (Telegram `admin.py:1031`, panel `router.py:1687`) y, tras aprobar una renovación, el pendiente duplicado (`admin.py:915`, `router.py:1591`).

### `active`
**Escritura**
- `create_active_subscription` (`subscriptions.py:69`) — afiliación manual `afiliar.py:510`.
- `confirm_subscription` (`:241`) — aprobación de compra nueva: Telegram `admin.py:963`, panel `router.py:1630`.
- `confirm_renewal_subscription` (`:132`) — renovación: Telegram `admin.py:912`, panel `router.py:1587`, `/renovar` `renovar.py:330`.
- Panel: alta manual `router.py:947`; importación `router.py:2064`; edición `router.py:1153-1154` (cualquier valor).

**Lectura (filtran `status='active'`)** — es el estado más leído. Los críticos para esta orden:
D-3 `get_expiring_subscriptions` (`:310-328`); D+0 `get_expired_subscriptions_to_notify` (`:348-364`); express `get_expired_express_subscriptions` (`:367-383`); gracia (`:424-442`) y corte (`:445-462`); `get_active_subscriptions_by_user` (`:493-507`); `auto_expire_overdue_subscriptions` (`:602-626`); `get_user_attention_subscriptions` (`expiring_soon`, `:166-171`); panel `router.py:301,304,309,603,1498,2125`; `analytics.py:62,71,82`; `monthly_report.py:88`; `database/hogar.py:145`; `bot/handlers/hogar.py:885`.

### `expired`
**Escritura**
- `auto_expire_overdue_subscriptions` (`subscriptions.py:622`), llamado por el job cada 15 min (`scheduler/jobs.py:406-414`, alta en `:563-570`) **y por dos GET del panel** (`router.py:858` ficha de cliente, `:1065` listado de suscripciones). El panel escribe en GET (lo cita la orden, Lote 6) [C].
- `expire_subscription` (`:257`) — solo `job_express_release` (`jobs.py:94`), que además libera el perfil.
- Corte suave del panel: `router.py:1226` (rota PIN, **no** libera el perfil).
- «Cortar y liberar» del panel: `router.py:1298` (libera perfil y rota PIN, pero escribe `expired`, no `cancelled`).
- Panel edición (`router.py:1153-1154`).

**Lectura**
- `get_user_platform_active_subscription` (`:100-119`, `active|expired|cancelled`).
- `get_user_attention_subscriptions` (lista `expired`, `:162-165`) → dispara el carrito de renovación (`subscription.py:50-55`, `start.py:144`).
- `get_renewable_subscriptions_by_user` (`:510-525`) → `/renovar`.
- `get_expired_subscriptions` (`:528-599`).
- Ingresos: `analytics.py:68,140`, `monthly_report.py:48,120,132` (cuentan `active` y `expired`), `router.py:296,314`.
- `main.py:110-112` (`cancel_and_buy`): lee `expired` y las pasa a `cancelled` **sin liberar el perfil** (§3e).
- Plantillas: `dashboard.html:352`, `subscriptions.html:22,100`, `user_detail.html:212,233,259`.

### `expired_payment`
**Escritura:** una sola — `cancel_expired_pending_subscriptions` (`subscriptions.py:644`), por `job_pending_payment_cleanup` (`jobs.py:215-224`).
- Intervalo y corte: el job corre **cada 4 horas** (`jobs.py:526-533`) y corta pendientes de más de **4 horas** (`subscriptions.py:633`), pero el docstring, el título del job y el comentario dicen «45 minutos» (`jobs.py:213,216`, `subscriptions.py:630`). El código manda: **4 h** [C]. La documentación está desfasada.

**Lectura:** `get_user_attention_subscriptions` lo cuenta como `pending` (`:161`) → el bot dice al cliente que tiene un pedido pendiente que ya no existe como tal; `get_expired_subscriptions` (`:558`).
**Qué NO lo lee** [C]: `get_user_platform_active_subscription` (`:111`), `get_renewable_subscriptions_by_user` (`:518`), `get_pending_subscriptions` (`:338`), ingresos (`analytics`), cancelación, ni ninguna plantilla (cae en el `else` genérico que imprime el texto, `user_detail.html:214`).
**Salida administrativa:** ninguna. En `user_detail.html:233` solo `expired|cancelled` muestran el botón de borrar; `expired_payment` no muestra ni corte ni borrado. El formulario de edición (`:257-262`) no tiene `<option>` para `expired_payment`, así que ninguna opción queda `selected` y el navegador envía la **primera**, `active`: guardar el formulario sin tocar el estado **activa** la suscripción sin pago [C por lectura del HTML + comportamiento estándar de `<select>`]. Confirma lo que afirma la orden.

### `cancelled`
**Escritura**
- `cancel_subscription` (`subscriptions.py:268`): corte automático D+7 (`jobs.py:334`); admin Telegram `admin.py:1308` (con liberar perfil y rotar PIN, `:1296-1306`); IA del admin `ai_admin.py:859` (libera perfil **sin rotar PIN** y sin limpiar `reserved_*`, `ai_admin.py:848-856`).
- `main.py:110-112` (`cancel_and_buy`).

**Lectura:** `get_user_platform_active_subscription` (`:111`, incluye `cancelled`), `get_user_attention_subscriptions` (lo excluye, `:157`), `get_expired_subscriptions` (lo excluye, `:545`), plantillas `dashboard.html:353`, `subscriptions.html:23,101`, `user_detail.html:213,233,260`.
**Salida:** la única «reactivación» es implícita: una renovación aprobada sobre una fila `cancelled` la pasa a `active` (`confirm_renewal_subscription`, `:132`).

### Estados de otras tablas (no mezclar)
`profiles.status`: `available`, `occupied`, `reserved` (según `profiles.py:22,76,91,186,202`). `accounts.status`: `active|suspended|expired`. `express_queue.status`, `users.status` y `support/verification` tienen sus propios valores y no intervienen en este mapa.

---

## 3. Hechos que afirma la orden — evidencia

### (a) Consultas con `status='active'` frente a `auto_expire` — **CONFIRMADO [C]**; efecto en producción **[H]**

| Consulta / job | Filtro | Línea |
|---|---|---|
| D-3 aviso (`job_expiry_reminders`, 10:00 diario) | `active`, `reminder_sent=False`, `end_date` entre ahora y +3 días | `subscriptions.py:310-328` |
| D+0 aviso (`job_expiry_notifications`, **cada hora**) | `active`, `expiry_notified=False`, `end_date <= ahora` | `:348-364` |
| Express release (`job_express_release`, **cada 5 min**) | `active`, `plan_type='express'`, `end_date <= ahora` | `:367-383` |
| Gracia D+1..D+6 (`job_debt_reminders_and_cuts`, 9:00 diario) | `active`, `plan_type='monthly'`, `end_date < ahora`, `debt_reminder_count < 6` | `:424-442` |
| Corte D+7 (mismo job) | `active`, `monthly`, `end_date < ahora`, `debt_reminder_count >= 6` | `:445-462` |
| `auto_expire` (cada 15 min) | pasa `active` con `end_date < ahora` a `expired` | `:602-626`, `jobs.py:563-570` |

Razonamiento (sobre el código; no se vio ejecución):
- **Gracia/corte.** Una suscripción vence a la hora `T`. En ≤15 min `auto_expire` la deja en `expired`, fuera de las dos consultas de gracia y corte. El job de las 9:00 solo la ve si su ejecución cae en esa ventana de ≤15 min (≈1% de las horas posibles de vencimiento) [H]. Un `debt_reminder_count` necesita 6 ejecuciones diarias consecutivas para llegar a 6: prácticamente imposible. **El corte D+7 casi nunca ocurre y los recordatorios casi nunca salen** — consistente con lo que dice la orden («no coincidía con ninguna de las dos reglas»).
- **D+0.** El job horario solo ve la suscripción si corre entre `T` y la siguiente pasada de `auto_expire` (≤15 min de 60): cerca del 25% de los vencimientos recibe el aviso D+0; el resto, no [H].
- **Express.** Es la misma carrera con el release de 5 min contra `auto_expire` de 15 min: si `auto_expire` gana, la express queda `expired` con el perfil `occupied`, sin PIN rotado y sin aviso. Estimación gruesa, suponiendo fases independientes, ≈15-20% de las express [H]. **Hallazgo nuevo, no está en la orden**; lo cubre la consulta 3 del script (express `expired` con perfil ocupado).
- **Guarda anti-corte inexistente [C].** `CLAUDE.md` dice que el corte «no corta si hay un pago pendiente». `has_pending_renewal_for_platform` existe (`subscriptions.py:197`) pero un `grep` no halla ningún llamador; `job_debt_reminders_and_cuts` (`jobs.py:275-386`) no consulta pagos pendientes. La guarda **nunca estuvo activa**. Habrá que decirlo al actualizar `CLAUDE.md` en el Lote 8.
- Otros lectores de `active` con vencimiento: `router.py:1498` (lista de «vencidas» del dashboard) y `analytics.py:71` solo ven las no flipeadas; el panel de ingresos cuenta `active|expired` (consistente). La lista de «vencidas» del dashboard de pagos queda vacía en cuanto `auto_expire` actúa — de ahí la razón de que el panel invoque `auto_expire` en GET.
- El job de gracia que se **ve** funcionar (`send_debt_reminder`, `notification_service.py:363`) trae el texto «se libera el perfil si no renueva» (`:381-387`): con la opción B del Orquestador (sin corte automático) esos textos son **falsos** y hay que reescribirlos en el Lote 6. No está en la orden.

### (b) `delete_pending_subscriptions_for_platform` con dos servicios de la misma plataforma — **CONFIRMADO [C]**
- La función borra **todas** las `pending_payment` del par usuario+plataforma (`subscriptions.py:288-307`).
- Carrito de renovación: el carrito se indexa por `sub_id` (`subscription.py:75,797`), así que dos servicios de Netflix son dos entradas. El bucle de confirmación (`subscription.py:935-962`) hace, por cada elemento seleccionado: `delete_pending…(usuario, plataforma)` y luego `create_subscription`. En la **segunda** iteración de la misma plataforma, el borrado elimina el pendiente recién creado en la primera. Resultado: `sub_ids` contiene un id que ya no existe; el cliente paga el total de dos servicios y el admin solo puede aprobar uno; el botón del segundo responde «Suscripción no encontrada» (`admin.py:869`).
- Carrito de compra nueva (`subscription.py:593`) **no** llama a la función de borrado: allí dos ítems de la misma plataforma sí crean dos pendientes. Compra individual: `subscription.py:236-237` sí borra. Express (`express.py:164`) no borra.
- Efecto lateral: tras el borrado el perfil reservado (`reserve_profile`, `subscription.py:958-964`) queda reservado 30 min sin dueño y lo libera el job de reservas (`jobs.py:392-400`).
- Detalle extra: en el carrito de **renovación** el código reserva un perfil libre por cada ítem (`subscription.py:958-964`), aunque una renovación reutiliza el perfil que ya tiene. Reduce el stock 30 min sin necesidad; la orden lo ataja en el Lote 4 («las renovaciones no reservan perfiles»). La reserva no se vincula a la suscripción: la aprobación vuelve a pedir `get_available_profiles` (`admin.py:952`) y puede elegir otro perfil [C].

### (c) Cómo elige `get_user_platform_active_subscription` la suscripción al aprobar una renovación — **CONFIRMADO [C]**
- Consulta: `user_id` + `platform_id`, `status IN (active, expired, cancelled)`, `ORDER BY end_date DESC`, `LIMIT 1` (`subscriptions.py:106-116`). Se usa al aprobar en Telegram (`admin.py:884`) y panel (`router.py:1568`), y para pintar «es renovación» en la lista de pagos (`router.py:1512`).
- **No usa el servicio que el cliente quería renovar.** Elige la de **fecha de fin más lejana**. Con dos Netflix (una vencida y otra vigente) renueva la vigente (la que tiene el `end_date` mayor) y deja la vencida en deuda; con una fila `cancelled` antigua con `end_date` posterior a otra, resucita la cancelada.
- El pendiente que se está aprobando no se confunde con ella porque `pending_payment` no está en el filtro; pero tras aprobar **una compra nueva**, esa fila ya está `active` y una **segunda** aprobación la encuentra como `existing_sub` (mismo id) y cae en la rama de compra nueva (ver (d)).
- Cálculo de la nueva fecha (tres versiones distintas, como dice la orden): Telegram toma el `end_date` actual **siempre** como base, incluso vencido (`admin.py:899-910`); panel usa el `end_date` solo si es **futuro**, si no parte de hoy (`router.py:1572-1585`); `/renovar` usa `_calc_new_end_date` (`renovar.py:97-107`); el carrito crea la fila con `now + 30d` (`subscription.py:937-939`). Todas recortan con `[:10]` sobre un timestamp que Supabase devuelve en UTC [H]: un fin de periodo guardado como 23:59:59 Caracas se ve como el **día siguiente** en UTC, así que cada renovación podría sumar un día de deriva. Lo mide la consulta 7 del script.
- La rama de Telegram (`admin.py:884-916`) reutiliza `existing_sub.profile_id` sin comprobar que el perfil siga siendo suyo: `cancel_subscription` y el corte automático no ponen `profile_id` en `NULL` (solo cambian el estado, `subscriptions.py:268`, `jobs.py:334-352`). Si el perfil se liberó y se reasignó, la renovación lo vuelve a vincular **sin `assign_profile`** y sin reservarlo: dos clientes con el mismo perfil, o un perfil `available` con dueño [C] — el comentario del código (`admin.py:885`) dice «Perfil puede haber sido liberado por el scheduler», pero el scheduler no desvincula. El panel exige `existing_sub.get("profile_id")` y tiene el mismo problema (`router.py:1569`).

### (d) Aprobar un pedido que ya no está `pending_payment` — **CONFIRMADO [C]**
- Ni Telegram (`admin.py:839-1003`) ni el panel (`router.py:1539-1650`) comprueban `sub.status`. Los botones de Telegram persisten en el chat, así que se puede pulsar «Aprobar» sobre una orden vieja.
- Casos según el estado actual de la fila `sub_id`:
  - **`active` (ya aprobada, doble clic):** `existing_sub` es ella misma → rama «compra nueva»: toma otro perfil disponible (`get_available_profiles`, `admin.py:952`), vuelve a ejecutar `confirm_subscription` sustituyendo `profile_id` y `assign_profile` marca ocupado el nuevo. **El primer perfil queda `occupied` sin suscripción** (perfil huérfano). El cliente recibe un segundo mensaje con otro perfil. `increment_user_purchases` suma una segunda compra.
  - **`expired_payment` (por TTL de 4 h):** se aprueba igual. El `end_date` de una compra nueva se fijó al crear el pedido (`now+30d`, `subscription.py:232`) y `confirm_subscription` **no lo actualiza** (`subscriptions.py:240-246`); el mensaje al cliente muestra `ahora + 30 d` (`admin.py:975-976`) mientras la base guarda `creación + 30 d` — **la fecha que ve el cliente y la guardada difieren** [C]. Con aprobación tardía el cliente pierde días.
  - **Borrada:** `get_subscription_by_id` devuelve `None` → «Suscripción no encontrada» (`admin.py:869`). Es la «protección» actual contra el doble clic **en renovaciones** (porque la rama de renovación borra el pendiente); no cubre compras nuevas.
- Dos aprobaciones simultáneas (panel y Telegram a la vez) no tienen candado ni transacción [C].
- Rechazar tampoco comprueba estado: `handle_admin_reject_payment` (`admin.py:1006-1031`) y `payment_reject` (`router.py:1685-1687`) llaman `delete_subscription` aunque la fila esté `active`: **borra una suscripción activa, deja el perfil `occupied` y avisa al cliente de «comprobante no aprobado»** [C]. `payment_reject` ni siquiera verifica que exista (`router.py:1686-1687` borra antes de comprobar).
- La orden dice que la aprobación «no es idempotente»: confirmado y agravado (perfil huérfano).

### (e) Qué liberan y qué NO liberan borrar o cancelar — **CONFIRMADO [C]**

| Acción | Dónde | ¿Libera perfil? | ¿Rota PIN? | Observaciones |
|---|---|---|---|---|
| `delete_subscription` (genérico) | `subscriptions.py:275-285` | **No** | No | Desvincula `household_incidents` y borra la fila. Usado por rechazo, panel `router.py:1352-1358`, y tras aprobar renovación. |
| Borrar suscripción en el panel | `router.py:1344-1364` | **No** | No | La ruta no verifica estado (el botón solo aparece en `expired|cancelled`, `user_detail.html:233`, pero la ruta acepta cualquiera). |
| Borrar cliente | `database/users.py:229-238` (`delete_user`); panel `router.py:1036-1046`; también `start.py:246`, `afiliar.py:523` | **No** | No | Borra las suscripciones y el usuario; los perfiles quedan `occupied` sin dueño. El esquema declara `profile_id … ON DELETE SET NULL` (`supabase_schema.sql:99`), no libera la fila del perfil. |
| `cancel_subscription` (función) | `subscriptions.py:264-272` | **No** | No | Solo cambia el estado. No pone `profile_id` en `NULL`. |
| Corte automático D+7 | `jobs.py:333-352` | Sí (`available`, `last_released`, limpia reservas) | Sí (4 dígitos al azar) | Hoy no se dispara por la carrera de (a). |
| Cancelar desde Telegram admin | `admin.py:1283-1320` | Sí (`status`, `pin`, `last_released`) | Sí | No limpia `reserved_for/until`. |
| Cancelar por la IA del admin | `ai_admin.py:841-859` | Sí | **No** | Cliente conserva el PIN antiguo → vulnerable. No limpia reservas. |
| «Cortar y liberar» (panel) | `router.py:1261-1300` | Sí | Sí | Escribe `expired`, no `cancelled`. Rota PIN antes de comprobar que la actualización de la suscripción tuvo éxito. |
| Corte suave (panel) | `router.py:1181-1232` | **No** (mantiene `occupied`) | Sí | Estado `expired`: el cliente sigue viendo la fila «vencida» y el perfil sigue ocupado. |
| `cancel_and_buy` | `main.py:104-113` | **No** | No | Cancela **todas** las `expired` del cliente de golpe. Cada una conserva un perfil `occupied` (si no se liberó antes) → fuga de perfiles [C]. Es el caso más probable de fuga, dado que el cliente lo dispara solo. |
| Express vencida | `jobs.py:79-113` | Sí | Sí | Solo si gana la carrera frente a `auto_expire` (a). |
| Edición manual de perfil en suscripción (panel) | `router.py:1161-1168` | Sí (vieja `available`) | No | Marca `available` sin comprobar que no esté ligado a otra suscripción activa; marca el nuevo `occupied` sin comprobar que estuviera libre. |
| Rechazo de pago | `admin.py:1031`, `router.py:1687` | No aplica (pendiente sin perfil) | — | Salvo que la fila estuviera `active` (ver (d)). |

Otras observaciones:
- `release_profile` (`profiles.py:86-97`) no rota PIN ni limpia `reserved_*`; los jobs y las rutas del panel/bot hacen el `update` directo cada uno a su manera (hay **seis** formas distintas de liberar un perfil). La orden lo recoge en el Lote 3 («se libera el perfil y se rota el PIN según la regla vigente»).
- `profile_set_status` (`router.py:758-770`) permite pasar un perfil a `available`, `occupied` o `maintenance` sin comprobar si tiene una suscripción activa asociada [C]; solo valida el texto del estado.

---

## 4. ¿La orden contradice algo del código?

- **Corte «con guarda»**: la orden (y `CLAUDE.md`) hablan de una guarda anti-corte con pago pendiente. La guarda **no está conectada** (§3a). No contradice el diseño, pero corrige el diagnóstico: no «se pierde por la carrera», es que no existe.
- **«Un job cada 15 min pasa las vencidas a `expired`»**: correcto, y además lo hacen dos GET del panel.
- **«El bot cuenta `expired_payment` como deuda»**: correcto (`subscriptions.py:161`), y además le dice al cliente que tiene un pedido pendiente.
- **«La aprobación elige la suscripción por usuario+plataforma con `limit(1)`»**: correcto; el criterio de elección es el `end_date` más lejano.
- **Tabla de estados de la orden**: `cancelled` sale por «reactivación por orden aprobada». Hoy ya ocurre así, por la vía implícita de `confirm_renewal_subscription`. Conviene que el motor la haga explícita **y compruebe/reasigne el perfil** (hallazgo 2).
- **`pending_payment` dice «ningún código nuevo los escribe» (legado)**: es cierto para el Lote 4 con el flag encendido, pero con `CHECKOUT_V2_ENABLED=false` seguirán escribiéndose; la tabla debe decir «legado, solo con el flag apagado».
- **TTL**: la orden propone 24 h para la orden nueva; el job actual usa 4 h para el pendiente, y su documentación dice 45 min. Ver decisión 4.

---

## 5. Hallazgos nuevos (no están en la orden)

| # | Hallazgo | Evidencia | Marca |
|---|---|---|---|
| N1 | Las renovaciones no reinician `reminder_sent`, `expiry_notified`, `debt_reminder_count` | `subscriptions.py:131-137`; grep §6 | C |
| N2 | La renovación de una `cancelled` reutiliza un `profile_id` posiblemente ya reasignado y no llama `assign_profile` | `admin.py:884-896`, `router.py:1569-1570`, `jobs.py:334-352` | C |
| N3 | Las express también pierden la carrera contra `auto_expire` (perfil que no se libera) | `subscriptions.py:367-383` vs `:602-626` | C / H (frecuencia) |
| N4 | `cancel_and_buy` cancela `expired` sin liberar perfil | `main.py:104-113` | C |
| N5 | Rechazar sobre una fila `active` la borra sin liberar perfil | `admin.py:1006-1031`, `router.py:1685-1687` | C |
| N6 | `end_date` de compra nueva no se actualiza al aprobar; el cliente ve una fecha y la base guarda otra | `subscriptions.py:240-246`, `admin.py:974-976` | C |
| N7 | La IA del admin cancela sin rotar PIN | `ai_admin.py:841-859` | C |
| N8 | Los recordatorios D+1..D+6 dicen al cliente «se liberará tu perfil si no renuevas» y «Día x/6»; con la opción B es falso | `notification_service.py:381-387` (aviso al admin) y `:411-417` (cliente); `bot/messages.py:209-215` (`DEBT_REMINDER`) y `:217-221` (`HARD_CUT_NOTIFICATION`) | C |
| N9 | El comentario/título del job de limpieza dice 45 min; el código usa 4 h | `jobs.py:213-216,526-533`, `subscriptions.py:630-633` | C |
| N10 | Carrito de compra (no el de renovación) no borra pendientes; el de renovación sí, y se pisa con dos servicios de la misma plataforma | `subscription.py:593` vs `:943` | C |
| N11 | `release_profile` no rota PIN ni limpia reservas; hay seis implementaciones paralelas | tabla de (e) | C |
| N12 | `subscription_edit` del panel acepta cualquier `status` y cambia perfiles sin validar disponibilidad | `router.py:1153-1168` | C |
| N13 | No hay `CHECK` ni catálogo del campo `status` | `supabase_schema.sql:107` | C |
| N14 | El esquema del repo no tiene columnas que el código usa (`profiles.reserved_*`, extras) → el esquema real es desconocido | `supabase_schema.sql:76-85` vs `profiles.py:186-188`, `router.py:2203` | C / NV (esquema real) |

---

## 6. Evidencia: búsquedas realizadas

- Escritores de `status` de suscripciones: `grep -n 'status' database/subscriptions.py`, `router.py`, `jobs.py`, `main.py`, `ai_admin.py`, `admin.py`.
- `reminder_sent|expiry_notified|debt_reminder_count` (excluyendo `streamvip/`, tests): solo se **escriben** `False/0` al insertar (`subscriptions.py:35-36,73-74`, `router.py:950-951,2067-2068`) y se ponen en `True`/`+1` en los marcadores. **Ninguna ruta de renovación los reinicia.**
- Llamadores de `has_pending_renewal_for_platform`: ninguno fuera de su definición.
- `streamvip/`: copia con otra estructura (no se leyó; ver el informe de decisiones, decisión 6).

## 7. Qué quedó sin verificar

- Datos reales: cuántas filas hay en cada estado, cuántas duplicadas, cuántas perfiles huérfanos → **script SQL** (`o02-lote0-consultas-anomalias.sql`).
- El esquema real (`information_schema`) → primer bloque del script.
- Cómo devuelve Supabase las fechas (UTC `+00:00`) y por tanto la magnitud de la deriva → consulta 7.
- El texto de `EXPIRY_NOTIFICATION` y los demás mensajes de `bot/messages.py` no citados arriba.
- Cualquier comportamiento de Telegram, Redis o Railway.
