# O02 Lote 5 — Admin: aprobación por orden, anomalías y borrado seguro

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **en la rama, sin fusionar** (viaja en el PR #2).
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md` (Lote 5). Arranque aprobado por el Orquestador el 27/11/2026.

> Todo lo que toca órdenes sigue detrás de `CHECKOUT_V2_ENABLED=false` (por defecto): con el flag apagado el admin ve lo de siempre. Lo que **sí** está activo sin flag: las pantallas «Anomalías» y «Órdenes de pago» del panel (solo lectura salvo sus acciones, que exigen motivo y quedan auditadas), el borrado seguro, las etiquetas humanas y el arreglo del botón «Pendientes».

## Cómo se aplicaron las recomendaciones del Lote 4
- **Excepción de Hogar: aplicada.** Si el cliente está justo esperando que le pidan la foto del soporte de Hogar (`waiting_photo`), su foto es de Hogar aunque tenga una orden pendiente; en cualquier otro caso, la orden tiene prioridad como pide la orden.
- **Selector de periodos: NO construido**, como recomendé: es una orden aparte (cambia lo que paga el cliente).
- **Imagen repetida:** no hubo recomendación mía que aplicar; sigue como estaba (se marca, no se bloquea).

## Qué entrega
1. **Aprobar y rechazar por orden en Telegram** (`bot/handlers/admin_orders.py`): ver la orden (ítems, señales, estado), aprobar, o rechazar eligiendo un motivo con botón (el motivo se le explica al cliente). Una orden caducada se aprueba con el botón explícito «comprobante tardío» (motivo registrado). Todo mensaje de error ofrece salida (reintentar, ver la orden, pendientes). «Pendientes» lista primero las órdenes con el flag encendido.
2. **Lo mismo en el panel** (`/panel/orders`, detalle con formularios de aprobar y rechazar): ítems, total, señales del comprobante y estado con etiqueta humana. **Un solo motor** para ambos (`services/order_admin.py`): aprobar llama a `checkout_service.approve`, avisa al cliente por ítem (confirmación + datos de acceso, con todo escapado), suma su compra y devuelve al admin un resumen con lo que debe atender (PIN a cambiar en un cambio de plataforma, perfil que cambió, perfil que no se liberó). Si el cliente no tiene Telegram o falla el envío, el resumen lo dice. Una segunda aprobación no avisa de nuevo ni se audita dos veces.
3. **Revisión de anomalías** (`/panel/anomalies`, `services/anomalies.py`): las consultas del Lote 0 en Python (perfil compartido, activa con perfil libre, duplicados, mora con perfil ocupado, perfiles ocupados sin suscripción, pedidos caducados sin pago y pendientes antiguos). Acciones con **motivo obligatorio y auditoría**: **anular** (con `void_subscription`, libera el perfil con guardas y avisa por Telegram los PIN anterior/nuevo), **liberar perfil** (solo si ninguna suscripción activa lo usa) y **fusionar duplicados** (se conserva una tal cual y se anula la otra; solo del mismo cliente y plataforma; **no suma ni mueve fechas**). Si no se pudieron leer los datos, la pantalla lo dice (no confunde «sin anomalías» con «error»).
4. **Borrado seguro:** una suscripción con periodos pagados no se borra (se anula desde Anomalías); borrar un cliente con historial también se bloquea. Antes de ejecutar la migración (sin tabla de periodos) el borrado funciona como siempre.
5. **Etiquetas humanas:** `expired_payment` → «Pedido caducado sin pago», `voided` → «Anulada», etc.; la mora se muestra derivada («En mora (N días)»). Aplicadas en las páginas existentes del panel, en las nuevas y en los mensajes de Telegram.
6. El indicador de pagos pendientes del panel suma las órdenes pendientes (con el flag).

## Dos arreglos de errores existentes que encontré
- **El botón «⏳ Pendientes» del panel de admin no hacía nada**: `cmd_pendientes` leía `update.message`, que es `None` cuando llega desde un botón. Esto también afectaba al botón «Pendientes» del aviso que agregué en O02-H. Ahora usa `effective_message`.
- **Borrar un cliente decía «eliminado correctamente» aunque `delete_user` fallara** (devolvía `False` y el panel lo ignoraba). Ahora informa el error.

## NO construido (necesita decisión tuya; recomendación incluida)
1. **Aprobación parcial por ítem.** Es dinero: ¿qué pasa con el resto cuando el cliente pagó de menos o solo uno de los servicios está bien? Opciones: (a) **dividir la orden**: lo aprobado queda aprobado y lo demás pasa a una orden nueva pendiente con su propio monto; (b) todo o nada: se rechaza y el cliente rehace el pedido (lo que hay hoy); (c) aprobar un subconjunto y dejar saldo pendiente. **Recomiendo (a)**, que exige una función SQL nueva (`split_checkout_order`) y ajustar la migración (aún sin ejecutar, es el momento más barato). Mientras no se decida, rige (b).
2. **Anular una orden aprobada por error con asiento contrario.** Es lo más delicado: revertir una renovación (devolver el `end_date`), una compra nueva (anular la suscripción) o un cambio de plataforma (el perfil ya se movió: no es automáticamente reversible). Requiere ampliar la migración (un tipo `reversa` en `subscription_periods`, importes negativos o un campo de reverso) y ajustar el reporte de ingresos. **Recomiendo** una función SQL `void_checkout_order` que revierta renovaciones y nuevas, y deje los cambios de plataforma como corrección manual guiada. Hasta entonces una aprobada se corrige anulando la suscripción desde Anomalías (con motivo y auditoría).
3. **`/renovar` del admin conectado al motor** (paridad con el cliente): sigue usando su camino manual. Es una herramienta que hoy funciona sin depender de las órdenes; **recomiendo** conectarla en una orden aparte, después de probar el flujo del cliente.
4. **Job que vence las órdenes a las 24 h** y el resumen de mora: Lote 6 (`expire_stale_orders` ya existe, sin programar). Mientras tanto una orden sin pagar queda pendiente hasta que el admin la rechace; el panel la lista con su antigüedad.

## Verificación (ejecutada)
- `python -m unittest discover -s tests -t .`: **300 pruebas OK** (246 de antes + 54 nuevas: 20 de servicios, 31 de interfaz del admin y 3 con el panel real).
- Cubren: etiquetas y mora derivada; cada categoría de anomalías y que los datos sanos no den hallazgos; mensajes al cliente por ítem (con nombres hostiles escapados); aprobar/rechazar con y sin Telegram del cliente, segunda aprobación sin efectos, errores sin filtrar detalles internos; **cada código de error de aprobación da un mensaje y una salida**; acceso solo de admins; rutas del panel (auditoría, motivo obligatorio, validaciones de fusión, liberar perfil solo sin uso); borrado seguro con y sin historial y con la tabla aún inexistente; renderizado de las 3 plantillas nuevas (sin inyección, **todo formulario POST lleva token CSRF**, etiquetas humanas); y, con el panel real, que las páginas exigen login y que los POST sin token CSRF dan 403 y con token funcionan.
- Comprobé que las pruebas detectan defectos: quitar la guarda del borrado, reavisar al cliente en una segunda aprobación, quitar la excepción de Hogar, volver a `update.message` en «Pendientes» o quitar un token CSRF hacen fallar la suite.
- `python -m compileall` y `build_telegram_app()` (flag en `false` y `true`): 63 handlers, el de órdenes registrado antes del general `admin:`, y las 8 rutas nuevas del panel.

## No verificado (lo prueba el Orquestador con Telegram, Supabase y un navegador)
- Todo contra Supabase real: las consultas de `list_orders` (`in_` + orden), la lectura de anomalías con tus volúmenes (límite 5.000 filas por tabla) y `count_subscription_periods`.
- El aspecto de las tres páginas nuevas en un navegador (probé el HTML y el CSRF, no el diseño) y los mensajes en un teléfono.
- La entrega real de los mensajes de acceso (`send_to_user`) y del aviso de PIN a los admins.
- Los datos de acceso de perfiles de «cupo adicional» (`is_extra_member`): igual que el flujo anterior, se entregan las credenciales de la cuenta; si esos perfiles tienen credenciales propias, hay que confirmarlo.

## Qué prueba el Orquestador
Con la migración ejecutada y el flag encendido en horario de baja actividad: (1) una orden con un servicio nuevo y una renovación → «Pendientes» en Telegram → aprobar → el cliente recibe confirmación y acceso por cada ítem y el admin el resumen; (2) rechazar con motivo → el cliente ve el motivo y «Iniciar nuevo pedido»; (3) la misma orden en el panel; (4) «Anomalías» con tus datos reales (compáralas con el resultado del script del Lote 0); (5) intentar borrar una suscripción renovada por una orden → se bloquea con el mensaje; (6) la pantalla de órdenes con una orden caducada.

## Prerrequisitos para encender el flag
Migración del Lote 2 ejecutada y la prueba anterior. El Lote 6 (vencimiento a las 24 h, resumen de mora) es recomendable pero no bloquea.
