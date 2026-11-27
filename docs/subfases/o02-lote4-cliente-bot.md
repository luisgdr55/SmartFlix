# O02 Lote 4 — Cliente en el bot (órdenes de pago)

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **en la rama, sin fusionar** (viaja en el PR #2).
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md` (Lote 4). Arranque aprobado por el Orquestador el 27/11/2026.

> **Todo detrás de `CHECKOUT_V2_ENABLED`, que por defecto es `false`.** Con el flag apagado el bot sigue con los carritos de siempre; el código nuevo solo corre si alguien lo enciende. **NO encender el flag todavía** (ver «Prerrequisitos»). No se tocó ninguna variable de Railway (el valor por defecto en código ya es `false`, así que no hace falta crearla para desplegar).

## Cómo se aplicaron las recomendaciones del Lote 3
- **Conectar `void_subscription` y el borrado «solo sin periodos» en el Lote 5**, junto a la pantalla de anomalías: **aceptado** (queda registrado en la orden).
- **Reactivar una `cancelled`: sigue abierta** (no hubo recomendación mía que aplicar). El checkout nuevo no la ofrece (`build_renewal_item` y la función SQL la rechazan).

## Qué hace con el flag encendido
1. **Una orden, no suscripciones.** Carrito de compra, carrito de renovación, compra individual y express crean UNA orden (`checkout_orders`) con ítems tipados, un solo monto en Bs (tasa congelada y Bs repartidos para que sumen exacto) y un solo comprobante. No se crea ninguna suscripción pendiente.
   - Los precios salen de la plataforma en la base, no de lo que quedó en el carrito de Redis.
   - Una renovación es un ítem por suscripción (dos Netflix = dos ítems, sin pisarse) y se valida que sea del cliente aunque el carrito (Redis) venga manipulado.
   - Sin tasa válida **no hay pedido** (no existe el respaldo de 36): mensaje con «Intentar de nuevo» y «Menú».
2. **Reserva de perfil solo para ítems `nueva`** (30 min); las renovaciones no reservan.
3. **Orden pendiente previa:** sin comprobante → se cancela y se crea la nueva; con comprobante en revisión → **no se crea otra** (no se pisa un pago que espera al admin) y se ofrece reenviar o soporte.
4. **Comprobante** (`handle_order_receipt`): foto, imagen enviada como archivo, o PDF. Se asocia a la orden pendiente del cliente y se atiende **antes que el flujo de Hogar** (`route_photo`); se registró un handler de documentos (sin orden pendiente o con flag apagado no hace nada, igual que antes). Formato no admitido o >10 MB: respuesta con acción.
5. **Señales OCR** (`services/ocr_signals.py`): teléfono y cédula del receptor contra `payment_config`, monto contra el total de la orden (tolerancia Bs 0,50), referencia repetida (en órdenes y suscripciones aprobadas), imagen repetida y fecha anterior a la orden. **Solo marcan**: se guardan en `ocr_flags`, salen en el ticket del admin y nunca bloquean ni aprueban. Un dato no leído es «no leído», no una alarma. Un PDF no se lee con OCR y se avisa al admin.
6. **`payment:resend`** (antes el botón existía sin handler) y **`order:cancel:<id>`** (solo si no hay comprobante).
7. **«Mis servicios»** muestra también las vencidas con botón «Renovar» y el pedido en curso con sus acciones. Ningún aviso nuevo queda sin botón.

## Archivos
Nuevos: `bot/handlers/checkout_v2.py`, `services/checkout_flow.py`, `services/ocr_signals.py` y sus pruebas. Cambios mínimos en lo existente: un guardia de 3 líneas al inicio de `handle_order_confirmed`, `handle_cart_confirm`, `handle_renewal_cart_confirm` y `handle_express_confirmed`; un bloque en `show_my_services`; el enrutador de fotos, el handler de documentos y dos callbacks en `main.py`; `CHECKOUT_V2_ENABLED` en `config.py`; `send_document_to_admin` en `notification_service.py`; `payment_reference_used` en `database/orders.py`.

## Qué NO incluye este lote
Aprobación por orden en el admin, `/renovar` del admin conectado al motor, `void_subscription`/borrado seguro, job de vencimiento de órdenes y resumen de mora (Lotes 5 y 6); selector de varios periodos (pregunta 1); reactivar una `cancelled`.

## Único cambio visible con el flag APAGADO
El botón «🔄 Reenviar comprobante» (que ya existía en el aviso de comprobante recibido) ahora responde con «Envía de nuevo la foto de tu comprobante en este chat» y un botón de menú; antes no hacía nada (quedaba cargando). Es un arreglo de un botón muerto, no cambia ningún flujo de pago.

## Prerrequisitos para encender el flag (importante)
1. La **migración del Lote 2 ejecutada** en Supabase (aún sin confirmación).
2. El **Lote 5 (aprobación por orden en el admin)**: el ticket que recibe el admin lleva botones `admin:oapprove:<id>` / `admin:oreject:<id>` que **todavía no tienen handler**. Con el flag encendido antes del Lote 5, el cliente pagaría y el admin no podría aprobar desde el bot. Tampoco existe aún el job que vence las órdenes a las 24 h (Lote 6; `expire_stale_orders` está listo, sin programar).
3. La prueba del Orquestador con su cuenta (ver abajo).

## Verificación (ejecutada)
- `python -m unittest discover -s tests -t .`: **246 pruebas OK** (196 de antes + 50 nuevas: 10 de señales OCR, 10 de armado/textos, 30 de handlers con Telegram, base, Redis y OCR falsos).
- Cubren: un carrito de renovación con dos servicios de la misma plataforma → una orden de dos ítems, sin suscripciones, sin reservas y con el carrito limpiado solo si todo salió bien; compra con precios de la base aunque el carrito traiga otros; sin tasa (tres variantes) o sin datos de pago → no hay orden y hay salida; carrito manipulado rechazado; orden en revisión no se pisa y orden sin comprobante sí; fallo de base conserva el carrito; comprobante como foto, imagen-archivo y PDF; señales que marcan pero no bloquean (monto/teléfono/referencia/imagen repetida); fallo del OCR no bloquea; formato no admitido y archivo pesado; la orden tiene prioridad sobre Hogar y, sin orden, el flujo de siempre queda igual; cancelar (propia, con comprobante, ajena, no pendiente); reenviar; «Mis servicios» con vencidas; y con el flag apagado los 4 puntos de entrada no llaman al flujo nuevo.
- Comprobé que las pruebas detectan defectos: que las renovaciones reserven perfil, que Hogar tenga prioridad, que el guardia del flag quede siempre activo o que se pise un pago en revisión hacen fallar la suite.
- `python -m compileall` y `build_telegram_app()` con el flag en `false` y en `true`: arma los 62 handlers sin errores en ambos casos.

## No verificado (lo prueba el Orquestador con Telegram, Supabase y Redis reales)
- Descarga real de fotos, imágenes-archivo y PDF; `send_document` al admin; el filtro de documentos de `python-telegram-bot`; el OCR real de Gemini con el nuevo comparador; los textos y botones en un teléfono.
- Todo contra Supabase: la migración, el filtro de `payment_reference_used`, el orden de la orden pendiente más reciente.
- Que la clave de «estado del usuario» y el TTL de 30 min de Redis no interfieren (el enrutador no depende de ella: consulta la orden en la base).

## Prueba que debe hacer el Orquestador (cuando existan Lote 2 y Lote 5)
Con el flag encendido solo en un entorno de prueba o en horario de baja actividad: (1) renovar dos servicios de la misma plataforma → una sola orden y un solo monto; (2) enviar el comprobante como foto, como archivo y como PDF → ticket al admin con señales; (3) pedido nuevo con servicio sin stock; (4) «Mis servicios» con una suscripción vencida; (5) cancelar un pedido sin comprobante; (6) que el bot siga igual con el flag apagado.

## Decisiones tomadas al construir (y preguntas)
1. **Una renovación cubre 1 periodo** (igual que hoy). El selector de varios periodos y la mora multi-periodo (Fase 8) **no se expusieron**: cambiaría lo que paga el cliente. `renewal_items` ya acepta `periods_by_sub`. **Pregunta:** ¿se agrega el selector (o se cobra por defecto lo necesario para quedar al día, `periods_to_be_current`)? Recomiendo un selector explícito con el total en vivo, en una orden aparte.
2. **Imagen repetida:** antes se bloqueaba al cliente («este comprobante ya se envió»); ahora **solo se marca** y se reenvía al admin, como pide la orden. Esto reduce la protección automática contra reutilizar un comprobante a cambio de que decide el admin.
3. **Prioridad sobre Hogar** (literal de la orden): un cliente con una orden pendiente que envíe una foto para el soporte de Hogar la verá tratada como comprobante. Mitigación posible: tratar como Hogar si el cliente está en un flujo de Hogar activo. **Pregunta:** ¿lo agrego?
4. El plazo de pago pasa de «30 minutos» a «24 horas» para la orden (el perfil nuevo se reserva 30 min); el texto del cliente lo dice.
5. Cancelar o reemplazar una orden no libera de inmediato los perfiles reservados: los libera el job de reservas al vencer su TTL (30 min).

## Siguiente
Lote 5 (admin): aprobación por orden en Telegram y panel con vista de ítems y señales, aprobación parcial, anular con motivo, pantalla de anomalías, `void_subscription` y borrado solo sin periodos. Hasta entonces el flag debe seguir apagado.
