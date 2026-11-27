# O02 Lote 1 — Servicios base (fechas, checkout, candado)

Rama `claude/keen-mendel-vn6j7b` · 🟡 · construido el 27/11/2026 · **en la rama, sin fusionar** (viaja en el PR #2).
Orden: `docs/arquitecto/ordenes/o02-pagos-estados-checkout.md` (Lote 1). Veredicto de arranque: el Orquestador pidió arrancar el Lote 1 el 27/11/2026, con las decisiones del Lote 0 ya aprobadas.

## Qué entrega
Tres módulos nuevos de **lógica pura** (sin base de datos, Redis real ni Telegram), **sin ningún llamador en producción**: ningún flujo existente los usa todavía, así que el comportamiento del bot y del panel no cambia.
- `utils/dates.py`: único helper de fechas. Meses de calendario con `calendar` (31/01 + 1 mes = 28/02, 29/02 si bisiesto; `anchor_day` opcional para volver al día 31), fin de periodo a las 23:59:59 de Caracas, UTC al guardar (`to_db`), Caracas al calcular y mostrar (`caracas_date`, que reemplaza el recorte `end_date[:10]`). `parse_db_datetime` acepta `Z`, `+00`, `+00:00` y fracciones; rechaza fechas sin zona en las conversiones (son ambiguas).
- `services/checkout_service.py`: `Decimal` y `ROUND_HALF_UP` (total Bs = total USD × tasa una vez; los ítems suman exacto el total, la diferencia va al último); tasa a 4 decimales y **sin tasa de respaldo** (`RateUnavailable`, N-C); predicado único `is_overdue` (`active` o `expired` con `end_date` < ahora), `days_overdue`, `overdue_periods`, `periods_to_be_current`, `renewal_coverage`; constructores de ítems `renovacion` (apunta a una `subscription_id` concreta), `nueva` y `cambio`; `build_manifest` / `validate_manifest` (JSON, rechaza una misma suscripción dos veces, detecta totales o subtotales manipulados); máquina de estados de la orden con actor obligatorio, motivo obligatorio al rechazar, anular o aprobar una caducada, aprobada inmutable y `AlreadyApplied` ante una segunda aprobación; TTL de 24 h.
- `services/locks.py`: candado Redis `SET NX EX` con token y liberación atómica (Lua) solo por su dueño; **falla cerrada** si Redis no responde (devuelve `None`; el llamador no debe proceder). Es defensa adicional: la idempotencia de la aprobación la dará la función SQL del Lote 3.

## Verificación (ejecutada)
- `python -m unittest discover -s tests -t .`: **169 pruebas OK** (115 de antes + 54 nuevas: `test_dates` 12, `test_locks` 7, `test_checkout_service` 35 — incluyen un caso con 300 repartos aleatorios de Bs, fin de mes y bisiesto, medianoche UTC/Caracas, el ejemplo de Ray de la Fase 8, dos servicios de la misma plataforma y manipulación de manifiestos).
- Comprobé que las pruebas detectan defectos: cambiar `ROUND_HALF_UP` por `ROUND_HALF_EVEN` o fijar el último día del mes en 28 hace fallar la suite.
- `python -m compileall` e importación de `main` y de los tres módulos (variables ficticias): OK. Sin dependencia nueva (`pytz` y `redis` ya estaban en `requirements.txt`).
- **No verificado** (lo prueba el Orquestador o llega en lotes siguientes): el candado contra Redis (Upstash) real — las pruebas usan un Redis falso y `client.eval` con el Lua nunca corrió contra Redis de verdad; la integración con la base (Lote 2/3).

## Qué NO se hizo (y por qué)
- **No se sustituyeron los tres cálculos de fecha actuales** (`timedelta(30)`, desde hoy, desde `end_date`) ni el recorte `[:10]` en `subscription.py`, `admin.py`, `renovar.py` y `router.py`. Cambiarlos hoy modificaría las fechas de corte que ve el cliente (de 30 días a meses de calendario) en un flujo con clientes reales y sin flag. Se reemplazan al encender `CHECKOUT_V2_ENABLED` (Lotes 3 y 4). Si prefieres reemplazarlos ya, es una orden aparte con tu veredicto.
- Sin migración, sin variable de entorno, sin cambio de esquema.

## Hallazgos y preguntas para el Orquestador
1. **La Fase 8 promete algo que no se cumple sin ancla.** Dice que 31/01 + 1 mes → 28/02 y «el mes siguiente vuelve al 31/03». Con `relativedelta` (y con `add_months` sin ancla) el siguiente es 28/03: el día **deriva**. Para volver al 31 hace falta recordar el día original (`anchor_day`). Opciones: (a) aceptar la deriva (simple, el cliente paga el mismo día del mes que le quedó); (b) guardar el día de aniversario en la suscripción o en `subscription_periods` (requiere una columna → Lote 2). El Lote 1 soporta ambas; **recomiendo (a)** salvo que quieras (b) antes de la migración.
2. **Reactivar una `cancelled`.** `build_renewal_item` rechaza `cancelled`, `pending_payment`, `expired_payment` y `voided`. La orden dice que `cancelled` sale por «reactivación por orden aprobada», pero no define desde qué fecha arranca (¿el `end_date` viejo, con meses de deuda, o la fecha de aprobación?). Hay que decidirlo antes del Lote 3 (con N-A: el perfil).
3. **Deuda del cambio de plataforma.** `build_change_item` recibe la deuda ya calculada. Propongo que sea `precio de la plataforma que se abandona × periods_to_be_current`; falta tu confirmación antes del Lote 4.
4. `is_overdue` y `days_overdue` cuentan días calendario de Caracas (D+1 empieza a las 00:00 del día siguiente al vencimiento). Es lo que usará el Lote 6; avísame si prefieres contar por 24 h desde la hora exacta.

## Siguiente
Lote 2 (migración SQL, la ejecuta el Orquestador antes de desplegar) y Lote 3 (motor), que dependen de tus respuestas 1 y 2 y del resultado del script SQL del Lote 0.
