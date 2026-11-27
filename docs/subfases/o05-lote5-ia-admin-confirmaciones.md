# O05 Lote 5 — IA del admin: confirmaciones

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **sin fusionar**: espera veredicto del Orquestador.
Orden: `docs/arquitecto/ordenes/o05-agente-conversacional.md` (Lote 5).

## Problema
En `bot/handlers/ai_admin.py`, cualquier texto del admin con «tasa», «cambio», «binance» o «dólar» y un número mayor que 1 cambiaba la tasa **al instante y sin confirmar** (también por la ruta de la IA, con el número que ella devolviera). Un texto como «¿cuánto cambió la tasa en 2 días?» la dejaba en Bs 2 y cambiaba el precio en bolívares que ven todos los clientes.

## Qué cambia
- Solo una **orden explícita** propone el cambio: verbo de cambio («actualiza», «cambia», «pon», «fija», «establece», «ajusta»…) y **un único número**. Preguntas, comentarios y «tasa 1050» sin verbo solo muestran la tasa y explican cómo pedir el cambio. Una orden sin número claro, o con varios, pide aclaración.
- **El número sale siempre del texto del admin**, nunca de la IA (la intención `rate_update` ya no lleva el valor).
- Lectura de números: `1050`, `105.5`, `105,5`, `1.050`, `1.050,50`, `1,050.50` (un separador seguido de exactamente 3 dígitos es de miles).
- **Plausibilidad**: finito, > 0, ≤ Bs 100.000 y dentro de ±50 % de la tasa vigente; si no, se rechaza con el motivo y una salida (revisar el número o usar `/tasa`).
- **Confirmación con botones** `[✅ Confirmar] [❌ Cancelar]` que muestran tasa actual, nueva y variación. El borrador vive en `context.user_data` (memoria; se pierde en un despliegue, y el admin solo debe repetir la orden): **un solo uso**, vence a los 5 minutos y se identifica con un código aleatorio de 8 caracteres. Un segundo toque, un botón viejo o uno vencido no aplican nada y ofrecen volver al menú. Al confirmar se **revalida** contra la tasa vigente. Solo administradores pueden pulsar los botones.
- Todo mensaje de este flujo trae un botón de salida («🏠 Menú admin»).
- Las demás acciones con efecto (cancelar suscripción, bloquear, desbloquear) ya pedían confirmación con botón; no se tocaron.

## Verificación (ejecutada aquí)
- `python -m unittest discover -s tests -t .` → **86 pruebas OK**, 19 de ellas de este lote (`tests/test_ai_admin_rate.py`): formatos de número, qué es una orden, plausibilidad, tarjeta sin efecto, confirmación una sola vez, cancelar, código ajeno, borrador vencido, revalidación, fallo de la actualización, no administrador, pregunta que no cambia la tasa, y clasificación de la IA sin orden explícita.
- **Pruebas de mutación**: quitar el «un solo uso», ampliar la banda de plausibilidad o aplicar la tasa sin confirmar hace fallar 2, 3 y 7 pruebas respectivamente.
- **No verificado**: el flujo real en Telegram y la tasa real en Supabase/Redis (lo prueba el Orquestador).

## Revisión independiente
Sin defectos en el flujo: el número sale del texto (nunca de la IA), el borrador es de un solo uso y se consume antes de cualquier `await`, la banda se revalida al confirmar y el callback exige admin. Corregido por sus observaciones: la confirmación responde siempre aunque falle una consulta, un fallo de auditoría no oculta que la tasa cambió, y los avisos ya no recomiendan `/tasa` (sin validación) sino el panel web. Observación aceptada: en «actualiza los 3 pagos con tasa 1050» se interpreta 1050; es inocuo porque exige confirmar.

## Para probar en Telegram (Orquestador)
1. «¿cuánto cambió la tasa en 2 días?» → solo muestra la tasa.
2. «actualiza la tasa a 1050» → tarjeta con variación; **Cancelar** → la tasa no cambia.
3. Repetir y **Confirmar** → se actualiza; volver a pulsar el mismo botón → «venció o ya se usó».
4. «actualiza la tasa a 5» → rechazada por no plausible.

## Fuera de alcance, solo reportado
`/tasa` y `update_rate` no validan (0, negativos, `nan`, infinito): `docs/DEUDA.md` S-009.
