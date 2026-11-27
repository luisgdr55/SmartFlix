# O02 Lote 0 — Informe de decisión para el Orquestador

Rama `claude/keen-mendel-vn6j7b` · 27/11/2026 · Para: Luis (Orquestador).
**Yo recomiendo; tú decides.** Nada de esto se ha construido. Evidencia en `docs/subfases/o02-lote0-mapa-de-estados.md` (los `[C]`, `[H]`, `[NV]` y las referencias `archivo:línea` salen de allí).

## Cómo leerlo
Cada decisión trae: **opciones → recomendación → costo → riesgo → retrocompatibilidad con los flujos activos → qué pasa si eliges la otra.** Al final, una tabla resumen y las decisiones nuevas que descubrí.

> ⚠️ **Advertencia previa que afecta el orden de trabajo (léela aunque no leas el resto).**
> Hoy el corte automático D+7 **no se dispara casi nunca** solo porque `auto_expire` (cada 15 min) saca las suscripciones de `status='active'` antes de que el job de las 9:00 las vea [C/H]. **Si alguien «arregla» eso apagando `auto_expire` sin retirar a la vez la parte de corte del job `debt_reminders_and_cuts`, el corte D+7 volvería a funcionar de golpe** y empezaría a cortar clientes reales, contra tu decisión B. Ambas cosas van juntas en el Lote 6 y no se deben separar. Esto es la razón de que **no** recomiende tocar `auto_expire` antes de ese lote.

---

## Decisión 1 (residual) — Resumen diario de mora al admin (Lote 6)

La parte A/B del corte ya está resuelta (B). Falta confirmar el resumen diario propuesto.

| Opción | Qué es |
|---|---|
| **1-a** | Sin resumen. La mora solo se ve en el panel. |
| **1-b (recomendada)** | Un mensaje diario al admin (9:00 Caracas, el mismo horario del job de deuda) con los clientes con mora ≥ 7 días: nombre/servicio, días de mora, deuda en USD y un botón por cliente para abrir su ficha. Tope de 15 filas por mensaje y «ver todos en el panel». Si no hay nadie, no se envía nada. |
| **1-c** | Igual que 1-b pero solo semanal, o solo cuando alguien cruza el día 7. |

**Recomendación: 1-b.** Sin corte automático, la mora deja de tener un «fin»: si el admin no mira el panel, un cliente puede pasar meses con acceso sin pagar. El resumen sustituye a la memoria del admin. Un mensaje diario al admin no se acerca al límite de 30 msg/s.
- **Costo:** pequeño (una consulta con el predicado único `is_overdue` + un mensaje con botones; el job ya existe y se reutiliza).
- **Riesgo:** bajo. Posible mensaje largo (>4096 caracteres de Telegram) → tope y paginación. Posible ruido si hay mucha mora histórica: por eso conviene mostrar solo ≥ 7 días y ordenar por deuda.
- **Retrocompatibilidad:** es un aviso nuevo, solo al admin; no cambia nada para clientes.
- **Si eliges 1-a:** nada se rompe, pero el corte pasa a depender de abrir el panel. **Si 1-c:** menos ruido, más lag; razonable si la mora es muy poca.
- Pregunta anexa: ¿el resumen va a **todos** los admins (`send_to_admins`) o solo a ti? Recomiendo a todos.

---

## Decisión 2 — Qué hacer con `expired` heredado

**Hechos.** `expired` hoy mezcla cuatro situaciones [C]: vencida por `auto_expire` (perfil aún ocupado), corte suave (perfil ocupado y PIN rotado), «cortar y liberar» del panel (perfil libre) y express liberada (perfil libre). Por el estado no puedes saber si el perfil sigue siendo del cliente. Además las lecturas (renovar, ingresos, aviso al cliente) tratan `expired` como «vencida, renovable».

| Opción | Qué es |
|---|---|
| **2-a (recomendada)** | **Leerlo como mora, sin migrar**. `is_overdue` acepta `active` o `expired` con `end_date` < ahora. Nadie escribe `expired` nuevo (el Lote 6 deja de crearlo). Los registros viejos se normalizan solo cuando se renuevan (el motor los pasa a `active`) o se anulan. |
| **2-b** | Migrar todos los `expired` a `active`. |
| **2-c** | Migración por categorías con datos del script: `expired` con perfil libre → `cancelled`; `expired` con perfil ocupado → quedan como mora. Se haría en el Lote 7, fila por fila, con tu autorización. |

**Recomendación: 2-a ahora, y evaluar 2-c en el Lote 7 con los números del script.**
- **Por qué no 2-b:** reactivaría en bloque suscripciones cuyo perfil se **liberó** (perfil libre o ya entregado a otro). Les devolvería acceso lógico a perfiles que no son suyos y los volvería a mostrar como «activas» en los ingresos y en el stock. Es una escritura masiva sobre producción sin ganancia y con el riesgo del hallazgo N2.
- **Costo:** 2-a no cuesta nada de migración; solo disciplina en las consultas (un único predicado). 2-c cuesta un script con `--dry-run` y una revisión humana.
- **Riesgo:** 2-a deja basura heredada visible (suscripciones `expired` con perfil libre que aparecerán como «mora» con botón Renovar). Es aceptable: al renovar, el motor comprueba/reasigna el perfil (decisión nueva N-A). Para el admin, el listado de mora distinguirá «con perfil» de «sin perfil».
- **Retrocompatibilidad:** total. Los flujos actuales ya leen `expired` como renovable.
- **Si eliges 2-b:** corres el riesgo anterior y pierdes la distinción de quién estaba cortado a propósito (corte suave) frente a vencido a secas; además cambian ingresos y avisos de un día para otro.
- **Dato que lo decide:** consultas 3, 4 y 6 del script (cuántos `expired` tienen perfil ocupado y cuántos libre).

---

## Decisión 3 — Aprobación atómica: función SQL vs secuencial con compensación

| Opción | Qué es |
|---|---|
| **3-a (recomendada)** | Función `approve_checkout_order(order_id, actor)` en Postgres: en **una transacción** bloquea la orden (`FOR UPDATE`), solo procede si está `pending`, aplica todos los ítems (renovar periodos, crear/activar suscripción, asignar perfil, insertar `subscription_periods`) y devuelve el resultado; una segunda llamada devuelve lo ya aplicado. Python la llama con `sb.rpc(...)`. |
| **3-b** | Python aplica los efectos uno a uno y, si algo falla en medio, ejecuta «compensaciones» (deshacer). |

**Recomendación: 3-a.**
- **Por qué:** un pago cubre N servicios; hoy cualquier fallo en el medio deja datos a medias (ya vimos perfiles huérfanos por doble aprobación y renovaciones que borran el pendiente después de renovar: `admin.py:912-915`). En 3-b, «deshacer» también puede fallar (¿y si el bot se reinicia a mitad? Railway reinicia al desplegar), y no hay forma de tener bloqueo entre panel y Telegram sin una transacción. En 3-a el reinicio del proceso o un doble clic dan «todo o nada» y la idempotencia es de la base, no del código.
- **Costo:** mayor que 3-b al inicio: hay que escribir plpgsql, probarlo en Supabase (no hay CI; lo ejecutas tú en el editor SQL) y mantener parte de la lógica fuera de Python. Mitigación: la función solo **aplica** (la aritmética de fechas y totales se calcula en Python con `Decimal` y viaja ya resuelta en el manifiesto); el SQL hace transiciones y escrituras, no decide precios. La suite `unittest` con repositorio falso cubre la lógica de Python; la función SQL la prueba el Orquestador.
- **Riesgo de 3-a:** error de lógica dentro de la función en producción; se limita con el flag `CHECKOUT_V2_ENABLED=false` por defecto, la migración antes del despliegue y una prueba con tu cuenta. `supabase==2.7.4` (pinned, `requirements.txt`) expone `rpc`; **la firma exacta se verifica en el Lote 2/3** antes de usarla (no la he ejecutado). Riesgo de 3-b: el mismo tipo de fallo ocurre cada vez que ocurre un reinicio/error de red.
- **Retrocompatibilidad:** con el flag apagado nada cambia; la función no se llama. Con el flag encendido solo toca las órdenes nuevas; las suscripciones existentes no se migran.
- **Si eliges 3-b:** más rápido de escribir y de revisar en Python, pero no existe garantía de atomicidad con panel + Telegram + `/renovar` aprobando a la vez; tendrías que apoyarte en el candado Redis (`services/locks.py`), que además puede fallar si Redis cae. No lo recomiendo para dinero.
- **Aviso:** la función debe incluir cosas que hoy **no** se hacen y que descubrí: reiniciar `reminder_sent`, `expiry_notified` y `debt_reminder_count` en cada renovación (N1), y comprobar/reasignar el perfil al reactivar una suscripción `cancelled` (N2).

---

## Decisión 4 — TTL de una orden sin pagar

**Hechos.** Hoy el pendiente caduca a las **4 horas** (`subscriptions.py:633`, job cada 4 h `jobs.py:526-533`), aunque los comentarios dicen 45 minutos [C]. La reserva de perfil dura 30 minutos (`profiles.py:179`, job de reservas cada 10 min). Al caducar, el pendiente pasa a `expired_payment`, un estado sin salida (mapa §2).

| Opción | TTL de la orden (no de la reserva) |
|---|---|
| **4-a** | 4 h (lo que hace hoy el código). |
| **4-b (recomendada)** | **24 h** (propuesta de la orden). Reserva de perfil: 30 min, solo para ítems `nueva`. |
| **4-c** | 48-72 h. |
| **4-d** | Sin caducidad automática; solo recordatorio al admin. |

**Recomendación: 4-b.**
- **Por qué:** en el modelo nuevo caducar una orden **no destruye nada** (la orden pasa a `expired`, no se borra; el admin puede aprobarla igual con motivo, según la orden). Pagar por pago móvil de noche y que el admin apruebe al día siguiente es normal; con 4 h hoy una parte de esos pedidos caerían a `expired_payment`. 24 h cubre una noche. La reserva se mantiene en 30 min para que un cliente indeciso no bloquee stock.
- **Costo:** ninguno de código relevante (constante configurable). Costo de negocio: un perfil `nueva` ya no se retiene con la orden (solo 30 min) → si el admin aprueba tarde, puede no haber perfil: la orden queda pendiente con aviso (caso extremo ya previsto).
- **Riesgo (de negocio):** la **tasa**. Con la tasa congelada al abrir la orden, en 24 h puede subir el dólar y el cliente paga Bs de ayer. **Tú decides quién asume la diferencia** (¿se aprueba con la tasa congelada, o se pide complemento si pasó X horas/Y%?). Recomiendo: rige la congelada si el pago llegó dentro del TTL; si la orden ya caducó, el admin ve el monto en la tasa del día junto al pagado.
- **Retrocompatibilidad:** con el flag apagado sigue el pendiente de 4 h actual (no se toca). Con el flag encendido la orden vale 24 h.
- **Si eliges 4-a:** menos riesgo de tasa, más pedidos caducados sin pagar y más trabajo manual del admin. **4-c** agrava la exposición cambiaria. **4-d** deja órdenes zombi y números de «pendientes» que no significan nada.
- Aparte: hoy el título/comentario del job dice «45 min» (N9); se corrige en el Lote 6.

---

## Decisión 5 — Redondeo de Bs

**Hechos.** Hoy el Bs se calcula **por ítem**: `round(price_usd * tasa, 2)` en `float` (`exchange_service.py:137-144`, `subscription.py:73`), y el total del carrito es la **suma de ítems ya redondeados**; en el resumen del carrito los ítems y el total se muestran sin decimales (`,.0f`, `subscription.py:615,976`) y en la instrucción de pago el «monto exacto» con 2 (`,.2f`): el cliente puede ver dos cifras distintas del mismo pedido. Python `round` sobre `float` es de «redondeo bancario» y arrastra errores binarios. Además `calculate_price_bs` usa **36.0** si no hay tasa, y varios sitios usan 36 como respaldo (`subscription.py:62,153,218,513,599,783,951`) [C] — ver decisión nueva N-C.

| Opción | Qué es |
|---|---|
| **5-a (recomendada)** | Total en Bs = `total_usd × tasa` **una sola vez**, `Decimal`, `ROUND_HALF_UP`, 2 decimales. Los ítems llevan su `amount_bs` repartido de forma que **sumen exactamente el total** (se redondea cada uno y el resto, de ±0,01, se carga al último). |
| **5-b** | Suma de ítems redondeados (lo de hoy), pero con `Decimal`. |
| **5-c** | Bs enteros (sin decimales). |

**Recomendación: 5-a.** El cliente paga **un** monto en un solo pago móvil; ese monto debe ser exacto y reproducible, y lo que se guarda por periodo (`subscription_periods.amount_bs`) debe sumar el total pagado para que los ingresos en Bs (criterio de aceptación de la orden) cuadren al céntimo.
- **Costo:** bajo; lo implementa `checkout_service` con `Decimal` y se prueba con `unittest`.
- **Riesgo:** casi nulo. Alerta menor: el `rate_used` del esquema del repo es `DECIMAL(10,2)` (`supabase_schema.sql:106`), o sea guarda la tasa con 2 decimales; la orden crea el campo nuevo en `NUMERIC(12,4)`. El esquema real es **[NV]**; el script lo muestra.
- **Retrocompatibilidad:** las suscripciones existentes conservan su `price_bs`. Con el flag apagado el carrito actual sigue sumando por ítem.
- **Si eliges 5-b:** pagos con diferencias de centavos entre lo que dice la pantalla y la suma de periodos; reconciliar el monto con el comprobante (señal OCR de monto) fallaría por 0,01 en carritos grandes. **5-c** simplifica el pago pero el redondeo a entero con tasas de 3 cifras cambia el precio hasta ~0,5 Bs por servicio; eso es una decisión de precio (lo que ve el cliente), tuya.

---

## Decisión 6 — ¿Se conserva `streamvip/`?

**Hechos.** Carpeta en la raíz con copia antigua de `admin_panel`, `bot`, `config.py`, `database`, `main.py`, `scheduler`, `services` (rebranding de la sesión 9, `docs/RECORDATORIOS.md` R00-7, `docs/DEUDA.md` S-006). Lo que **sí** verifiqué: el `Procfile` y `railway.toml` arrancan `uvicorn main:app` de la **raíz**, no de `streamvip/`, y ningún `import` fuera de la carpeta la referencia [C]. Lo que **no** verifiqué [NV]: qué «Root Directory» tiene el servicio en Railway (no tengo ni debo usar Railway en esta tarea) y si algún proceso la usa.

| Opción | Qué es |
|---|---|
| **6-a (recomendada)** | No tocarla en O02. Abrir una **orden de limpieza aparte**: primero confirmar con el estado de Railway (solo lectura) que el servicio no apunta ahí; si no, eliminarla en un PR propio. |
| **6-b** | Retirarla dentro de O02. |
| **6-c** | Conservarla indefinidamente. |

**Recomendación: 6-a, y no dejarla «para siempre».** Mantenerla no afecta a O02 (no se edita), pero cada auditoría tiene que ignorarla, y duplica código con la lógica vieja (incluidos los mismos bugs de estado) en un **repositorio público** (R00-1): un valor por defecto antiguo o un nombre de proyecto antiguo queda expuesto (O01 L0 ya lo anotó: «conserva sus valores por defecto antiguos»).
- **Costo:** limpiar = un PR de borrado + 1 lectura de Railway. Mantener = deuda permanente.
- **Riesgo de borrar sin verificar:** si Railway estuviera apuntando a `streamvip/` (poco probable según `Procfile`, pero **no verificado**), borrarla tumbaría producción. De ahí «primero confirmar».
- **Retrocompatibilidad:** O02 no la lee ni la importa; no la afecta.
- **Si eliges 6-b:** mezclas una limpieza de riesgo propio con una orden 🔴 de pagos; yo lo evitaría. **6-c:** coste cero ahora, pero confusión en cada revisión.

---

## Decisiones nuevas que descubrí (no están en la orden)

### N-A — Reactivar una suscripción `cancelled`/`expired`: qué pasa con el perfil
Hoy la renovación reutiliza el `profile_id` guardado sin comprobar su estado ni llamar `assign_profile` [C], y `cancel_subscription` no lo desvincula. Puede haber **dos clientes con el mismo perfil** (mapa §3c).
- Opciones: **(i)** el motor comprueba el perfil: libre y sin dueño → se reasigna y se marca ocupado; ocupado por otra suscripción → se asigna otro del mismo tipo (y se avisa al admin con el PIN y la cuenta, como en los cortes); sin stock → la orden queda **pendiente con aviso**, no se pierde; **(ii)** igual pero siempre pide confirmación al admin.
- **Recomiendo (i)**, con aviso al admin cuando se cambió el perfil. Es lo que ya describe la orden para `nueva` sin perfil.
- **Impacto en el cliente:** si el perfil cambia, el cliente recibe credenciales nuevas (lo que ve el cliente = tuyo). **Retrocompatibilidad:** solo el motor nuevo; el flujo actual conserva el bug hasta el Lote 3.
- Si no se decide: el bug sigue (con flag apagado, ya existe).

### N-B — Reinicio de avisos al renovar
`confirm_renewal_subscription` no reinicia `reminder_sent`, `expiry_notified` ni `debt_reminder_count` [C]. Tras la primera renovación de una fila no vuelve a salir D-3 ni D+0, y el contador de deuda sigue consumido.
- **Recomiendo** que el motor los reinicie en cada renovación (dentro de la función SQL), y que el contador solo sume desde la mora actual.
- **Saneamiento de filas existentes** (reiniciar los contadores de suscripciones ya renovadas): es un `UPDATE` masivo a producción → **solo en el Lote 7 con tu autorización**. La consulta 6 del script cuenta cuántas filas están afectadas.
- Si no se decide: la regla B (recordatorios D+1..D+6) funcionaría para la primera mora de cada fila y fallaría en las siguientes.

### N-C — Tasa de respaldo «36»
`calculate_price_bs` y varios carritos usan **36.0** si no hay tasa en Redis/BD (`exchange_service.py:141-142`, `subscription.py:62,153,218,513,599,783,951`) [C]. Con la tasa real mucho mayor, un fallo de Redis + base mostraría precios en Bs absurdamente bajos al cliente.
- Opciones: **(i)** el checkout nuevo se **bloquea** con un mensaje y acción («reintenta / contacta soporte») si no hay tasa válida; **(ii)** se mantiene el respaldo.
- **Recomiendo (i)** solo en el flujo nuevo. Es una decisión sobre lo que ve el cliente y el dinero, por eso es tuya. El flujo viejo no se toca en O02.

### N-D — Mensajes de mora con la opción B
`DEBT_REMINDER` dice «se liberará tu perfil si no renuevas» y «Día x/6»; `HARD_CUT_NOTIFICATION` dice «tras 6 días… tu perfil ha sido liberado» (`bot/messages.py:209-221`, `notification_service.py:411-417`) [C]. Con la opción B (sin corte automático) eso **sería falso**.
- **Recomiendo** reescribirlos en el Lote 6: «tu servicio sigue activo, pero tienes N días de mora, renueva para evitar el corte», siempre con botón Renovar. **El texto exacto lo apruebas tú** (es lo que ve el cliente); te lo presentaré antes de construir.

### N-E — ¿Hotfix antes del motor de pagos?
Hay fallos en producción **hoy** que se corrigen con guardas pequeñas y que el motor nuevo tardará en cubrir (Lotes 1-4 son varias órdenes): aprobar/rechazar sin comprobar estado (rechazar una `active` la borra y deja el perfil ocupado — N5; doble aprobación deja un perfil huérfano), `cancel_and_buy` que cancela sin liberar (N4) y la carrera de `auto_expire` con las express (N3).
- **Opción 1:** esperar a O02 completa (con flag, meses). **Opción 2 (recomiendo):** una mini-orden aparte «O02-H» con solo tres guardas: aprobar/rechazar únicamente si `status='pending_payment'`; `cancel_and_buy` libera el perfil; `get_expired_express_subscriptions` acepta `active` o `expired`. **No** incluye tocar `auto_expire` ni el corte (ver advertencia inicial).
- **Costo:** bajo; **riesgo:** bajo-medio, está en los flujos de dinero y de perfiles → pide tu veredicto, PR y fusión en baja actividad (cada fusión reinicia el bot).
- Si no se decide: la fuga de perfiles sigue acumulándose; la consulta 3/4 del script te dice cuánto.

### N-F — Inicio del periodo al aprobar una compra nueva
Hoy `end_date` se fija al **crear** el pedido (`now + 30 d`) y la aprobación no lo actualiza (`subscriptions.py:240-246`), mientras el mensaje al cliente dice `aprobación + 30 d` (N6). El cliente pierde los días que tarde el admin.
- **Recomiendo** que el periodo empiece **en la aprobación** (fin de día en Caracas, un mes calendario después), como dice la Fase 8. Cambia lo que el cliente ve en su fecha de corte → lo decides tú; lo propongo como parte del Lote 3.

---

## Resumen y orden de decisión sugerido

| # | Decisión | Mi recomendación | Urgencia |
|---|---|---|---|
| 1 | Resumen diario de mora | Sí, 9:00, ≥7 días, tope 15, a todos los admins | Antes del Lote 6 |
| 2 | `expired` heredado | Leerlo como mora (2-a); evaluar 2-c en Lote 7 | Antes del Lote 1 |
| 3 | Aprobación atómica | Función SQL (3-a) | Antes del Lote 2 |
| 4 | TTL de la orden | 24 h; reserva 30 min solo `nueva` | Antes del Lote 2 |
| 5 | Redondeo de Bs | Una vez, `Decimal`, `ROUND_HALF_UP`, ítems que suman el total | Antes del Lote 1 |
| 6 | `streamvip/` | Orden de limpieza aparte, tras verificar Railway | No bloquea O02 |
| N-A | Perfil al reactivar | Comprobar/reasignar; sin stock → orden pendiente con aviso | Antes del Lote 3 |
| N-B | Reiniciar avisos al renovar | Sí; saneamiento solo en Lote 7 | Antes del Lote 3 |
| N-C | Tasa de respaldo 36 | Bloquear el checkout nuevo sin tasa | Antes del Lote 4 |
| N-D | Textos de mora | Reescribir (los apruebas tú) | Antes del Lote 6 |
| N-E | Mini-orden de guardas hoy | Sí (O02-H), sin tocar `auto_expire` | Cuando tengas el resultado del script |
| N-F | Inicio del periodo = aprobación | Sí | Antes del Lote 3 |

Sin tu veredicto no avanzo al Lote 1. Lo que dependa de datos reales (2 y N-E) mejora con el resultado del script `o02-lote0-consultas-anomalias.sql`.
