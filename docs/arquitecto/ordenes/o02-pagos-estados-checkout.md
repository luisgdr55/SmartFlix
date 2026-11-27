# O02 — Pagos, máquina de estados y orden de pago · 🔴 · rama `s23-pagos-estados`

**Estado:** BORRADOR para veredicto del Orquestador (27/11/2026) · **Depende de:** O01 Lote 0 (auditoría y CSRF) · **Implementa:** Fase 8 de `features.md` (lotes 1 a 6) ampliada con historial de pagos y saneamiento
**Evidencia:** informe privado del Arquitecto (con datos de clientes; no versionado).

## Objetivo
Que **un pago cubra N servicios sin crear suscripciones nuevas**, que cada suscripción tenga una máquina de estados con salida administrativa, y que exista un historial real de pagos y periodos. Eliminar de raíz los registros huérfanos, los duplicados y la dependencia de estados que ninguna pantalla puede resolver.

## Problema (resumen)
- La renovación **crea una suscripción nueva** por cada servicio; un cliente con varios servicios acaba con pendientes duplicados.
- La limpieza de pendientes se hace por usuario + plataforma, así que dos servicios de la misma plataforma se pisan; la aprobación elige la suscripción por usuario + plataforma con `limit(1)` y puede renovar la equivocada.
- Los pedidos sin pagar pasan a `expired_payment`, un estado que las consultas de renovación ignoran, que el bot cuenta como deuda y cuya pantalla de edición activaría la suscripción sin pago.
- La aprobación no comprueba el estado actual ni es idempotente; borrar suscripciones o clientes no libera el perfil.
- Las fechas se calculan de tres formas distintas (`timedelta(30)`, desde hoy, desde `end_date`) y se recortan como texto en UTC.
- No hay historial: las renovaciones sobrescriben `end_date`; los ingresos en Bs se calculan con la tasa de hoy, no la del pago.
- Las consultas de aviso de deuda, gracia y corte filtran `status='active'` mientras un job cada 15 min pasa las vencidas a `expired`: los avisos y cortes dependen de una carrera.

## Decisión del Orquestador (27/11/2026): opción B — sin corte automático
`CLAUDE.md` y `docs/ESTADO.md` decían «corte automático D+7 con guarda de pago pendiente»; `features.md` Fase 8 (decisiones 1 a 3, sesión 17) decía «sin corte automático». El Orquestador eligió **B, la de la Fase 8**:
- **No hay corte automático.** El servicio de un cliente con deuda sigue activo; cortar es una acción **manual** del admin (corte suave o cortar y liberar, ya existentes en el panel).
- **Recordatorios de deuda D+1 a D+6** (se corrige para que realmente salgan). Después de D+6 **no** se envían más; la mora queda visible en el panel.
- El comportamiento previo en producción no coincidía con ninguna de las dos reglas (por el cruce entre `auto_expire` y las consultas con `status='active'`): el Lote 6 lo corrige.
- **Efecto en las reglas del repo:** la guarda «el corte automático D+7 no corta si hay un pago pendiente» de `CLAUDE.md` queda sin objeto; se reemplaza por «el corte manual advierte al admin si el cliente tiene una orden pendiente». La actualización de `CLAUDE.md` y `docs/ESTADO.md` se hace en el Lote 8, no antes.

## Máquina de estados propuesta
**Suscripción** (`subscriptions.status`):
| Estado | Significado | Entra | Sale |
|---|---|---|---|
| `active` | Con acceso. Si `end_date` < hoy es **mora** (derivado, no un estado) | aprobación de orden | corte manual → `cancelled` |
| `cancelled` | Sin acceso, perfil liberado | corte manual o automático (según veredicto) | reactivación por orden aprobada |
| `voided` | Anulada por el admin con motivo (nunca borrada si tuvo pagos) | admin | terminal |
| `pending_payment`, `expired`, `expired_payment` | **Legado**: ningún código nuevo los escribe; las consultas los siguen aceptando (`expired` se lee como mora) | — | saneamiento |

**Orden de pago** (`checkout_orders.status`): `pending → approved | rejected | expired | cancelled | voided`.
Guardas: aprobar solo desde `pending`; una orden aprobada es inmutable; rechazar exige motivo; expirar por TTL (decisión 4).
Toda transición registra actor y hora; el admin puede anular una orden aprobada por error con motivo (revierte efectos mediante un asiento contrario, no borrando).

## Alcance

### Lote 0 — Decisiones y diagnóstico de solo lectura
Veredicto sobre las decisiones de abajo (la del corte D+7 ya está tomada: opción B). Consulta de solo lectura (con autorización expresa del Orquestador) que lista anomalías: duplicados por usuario+plataforma, `expired_payment`, suscripciones vencidas con perfil ocupado, perfiles ocupados sin suscripción activa. Entrega un listado, no corrige nada.

### Lote 1 — Servicios base (sin esquema)
- `services/checkout_service.py`: cálculo de periodos y mora, armado del manifiesto, totales con `Decimal`, validaciones. Agnóstico al almacenamiento de sesión.
- `utils/dates.py`: **un solo** helper de fechas: sumar meses calendario (con la biblioteca estándar `calendar`, sin dependencia nueva), fin de día en `America/Caracas`, conversión a UTC al guardar y a Caracas al mostrar. Reemplaza los tres cálculos actuales y el recorte `[:10]`.
- `services/locks.py`: candado en Redis (`SET NX EX`) para jobs y para aprobar.

### Lote 2 — Migración (la ejecuta el Orquestador antes de desplegar)
```sql
CREATE TABLE checkout_orders (            -- según Fase 8
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  manifest JSONB NOT NULL, total_usd NUMERIC(10,2) NOT NULL, total_bs NUMERIC(14,2),
  rate_used NUMERIC(12,4), payment_reference VARCHAR(255), receipt_text TEXT,
  ocr_flags JSONB, expires_at TIMESTAMPTZ, decided_by TEXT, decision_reason TEXT,
  version INT NOT NULL DEFAULT 1, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE UNIQUE INDEX ON checkout_orders(payment_reference) WHERE status='approved' AND payment_reference IS NOT NULL;
CREATE TABLE subscription_periods (       -- historial: nunca se actualiza, solo se inserta
  id BIGSERIAL PRIMARY KEY, subscription_id UUID NOT NULL REFERENCES subscriptions(id),
  order_id UUID REFERENCES checkout_orders(id), period_start TIMESTAMPTZ NOT NULL, period_end TIMESTAMPTZ NOT NULL,
  amount_usd NUMERIC(10,2) NOT NULL, rate_used NUMERIC(12,4), amount_bs NUMERIC(14,2),
  kind VARCHAR(12) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS void_reason TEXT, ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ;
-- índices: subscriptions(user_id, platform_id, status), subscriptions(status, end_date), checkout_orders(user_id, status)
-- función atómica de aprobación: approve_checkout_order(order_id uuid, actor text) → aplica todos los efectos del manifiesto
--   en UNA transacción, solo si status='pending' (idempotente: una segunda llamada devuelve el resultado ya aplicado).
```
La función SQL se escribe y revisa en el lote; supabase-py la invoca por `rpc` (se verifica la API exacta de la versión instalada antes de usarla).

### Lote 3 — Motor de aprobación
`checkout_service.approve(order_id, actor)` llama a la función atómica. Tipos de ítem del manifiesto (Fase 8): `renovacion` (N periodos, desde `end_date`), `nueva`, `cambio` (solo sobre suscripción vencida). **Cada ítem apunta a una `subscription_id` concreta; nunca se resuelve por usuario+plataforma.** Aprobar desde Telegram, panel y `/renovar` usa este único motor. Al cancelar/anular/borrar una suscripción se libera el perfil y se rota el PIN según la regla vigente.

### Lote 4 — Cliente en el bot
- El carrito de renovación y el de compra crean **una orden** (no suscripciones). Un solo monto total, un solo comprobante.
- Perfil reservado por 30 min **solo** para ítems `nueva`; las renovaciones no reservan perfiles.
- El comprobante se asocia a la orden pendiente del cliente: si hay orden pendiente, el enrutador de fotos la atiende **antes** que el flujo de Hogar; se aceptan imágenes enviadas como archivo/PDF; se implementa `payment:resend`.
- «Mis servicios» muestra también las vencidas con botón Renovar; ningún aviso sin botón.
- OCR: se agregan señales (destinatario y cédula contra `payment_config`, monto contra el total, referencia repetida). **Solo marcan; aprueba el admin.**

### Lote 5 — Admin
Aprobación por orden en panel y Telegram (con vista de los ítems y de las señales OCR); aprobación parcial por ítem; anular con motivo; estado visible con etiquetas humanas. Pantalla «Revisión de anomalías» (consultas del Lote 0) con acciones auditadas: fusionar, anular, liberar perfil. Borrado solo para suscripciones sin periodos.

### Lote 6 — Scheduler (opción B)
- Un único predicado `is_overdue(sub)` (estado `active` o `expired` heredado, con `end_date` < ahora) usado por **todos** los jobs y por el panel. `auto_expire` deja de cambiar estados; el panel deja de escribir en GET.
- Se **retira el job de corte automático D+7**. Se mantienen: aviso D-3, aviso D+0 y recordatorios de deuda D+1 a D+6 (con `debt_reminder_count` incrementado de forma atómica); tras D+6 no hay más envíos.
- **Resumen diario al admin** (propuesta, a confirmar en el veredicto): lista de clientes con mora ≥ 7 días y su deuda, con botón para abrir su ficha, para que el corte manual no dependa de la memoria.
- Corte manual (panel y Telegram): si el cliente tiene una orden `pending`, se advierte y se pide confirmar; el corte libera el perfil y rota el PIN según la regla vigente.
- Cada job con `coalesce=True`, `max_instances=1`, `misfire_grace_time` explícito y candado Redis; reclamo condicional antes de enviar (`UPDATE … WHERE reminder_sent=false RETURNING`); difusión ≤ 30 mensajes/s con manejo de `RetryAfter`.

### Lote 7 — Saneamiento de datos existentes
Solo tras el listado del Lote 0 y con autorización expresa para cada ejecución contra producción: script con modo `--dry-run` por defecto que propone (no aplica) fusiones y anulaciones; el Orquestador aprueba fila por fila.

### Lote 8 — Documentación de cierre
Actualizar `CLAUDE.md` (guarda del corte), `docs/ESTADO.md` (§0, §1, tabla de módulos: «Corte automático D+7» pasa a «Corte manual; recordatorios D+1 a D+6»), `docs/FLUJOS.md` y `features.md` (Fase 8).

## NO incluye
Pasarela automática con verificación bancaria · cambio de plataforma a mitad de periodo · referidos/lealtad/planes familiares · agente de IA (orden futura) · rediseño visual (O03) · PWA (O04) · autenticación (O01).

## Retrocompatibilidad y despliegue
- Variable no secreta `CHECKOUT_V2_ENABLED` (por esta orden se autoriza **crear solo esa**; por defecto `false`). Con `false` el bot sigue con los carritos actuales; con `true` usa órdenes. Los carritos en Redis caducan a los 30 min.
- Orden: migración → despliegue con flag apagado → prueba del Orquestador con su cuenta → encender el flag en horario de baja actividad → vigilar logs.
- Suscripciones existentes no se migran; la deriva de fechas se corrige en la próxima renovación (Fase 8).
- Fusionar en baja actividad (Railway redeploya y reinicia el bot).

## Casos extremos que las pruebas deben cubrir
Dos servicios de la misma plataforma pagados juntos y por separado · doble clic en aprobar · aprobar una orden ya rechazada · comprobante con monto distinto al total · tasa que cambia entre abrir la orden y aprobarla (rige la congelada) · fin de mes (31/01 + 1 mes) · cambio de hora/UTC cerca de medianoche · orden expirada con comprobante tardío (el admin puede aprobarla con motivo) · perfil no disponible al aprobar `nueva` (la orden queda pendiente con aviso, no se pierde) · reinicio del proceso a mitad de aprobación (la función atómica garantiza todo o nada).

## Criterios de aceptación
- Dado un cliente con tres servicios, cuando paga una vez el total, entonces existe una orden, una aprobación y tres periodos nuevos; no se crea ninguna suscripción.
- Dado un cliente con dos servicios de la misma plataforma, cuando renueva solo uno, entonces solo cambia el `end_date` de esa suscripción.
- Dada una orden aprobada, cuando se aprueba de nuevo, entonces no hay efectos adicionales.
- Dada cualquier suscripción en cualquier estado, entonces el admin tiene una acción auditada para destrabarla.
- Dado el reporte de ingresos del mes, entonces el total en Bs es la suma de los `amount_bs` guardados, no el total en USD × tasa de hoy.

- Dado un cliente con 8 días de mora, cuando corre el scheduler, entonces no se corta, no recibe más recordatorios, y aparece en el resumen del admin.
- Dado un corte manual de un cliente con una orden pendiente, entonces el admin recibe una advertencia antes de confirmar.

## Pruebas obligatorias
Suite `unittest` de lógica pura con repositorio falso: aritmética de fechas y zona horaria, totales con `Decimal`, transiciones válidas e inválidas, idempotencia, el escenario de dos servicios por plataforma, detector de anomalías. Lo que dependa de Supabase/Telegram real lo prueba el Orquestador; el cierre lo declara.

## Veredicto del Orquestador sobre el Lote 0 (27/11/2026): APROBADO — se aplican las recomendaciones de `docs/subfases/o02-lote0-decisiones.md`
Detalle, costo y riesgo de cada una en ese informe; el diagnóstico en `o02-lote0-mapa-de-estados.md`.
1. Resumen diario de mora al admin: **sí** (9:00 Caracas, mora ≥ 7 días, tope 15, a todos los admins, botón a la ficha).
2. `expired` heredado: **se lee como mora, sin migrar** (`is_overdue`); migración por categorías solo en el Lote 7 y con autorización.
3. Aprobación: **función SQL atómica** (`approve_checkout_order`); debe reiniciar `reminder_sent`, `expiry_notified` y `debt_reminder_count` en cada renovación.
4. TTL de la orden: **24 h**; reserva de perfil 30 min solo para ítems `nueva`. Pendiente de definir: quién asume la diferencia de tasa si la orden caduca.
5. Redondeo de Bs: **una vez, `Decimal`, `ROUND_HALF_UP`, 2 decimales**; los ítems suman exactamente el total.
6. `streamvip/`: **no se toca en O02**; orden de limpieza aparte tras verificar Railway (solo lectura).
- N-A: al reactivar, el motor comprueba/reasigna el perfil; sin stock, la orden queda pendiente con aviso.
- N-B: reinicio de avisos al renovar (saneamiento de filas existentes solo en el Lote 7).
- N-C: el checkout nuevo se bloquea si no hay tasa válida (sin respaldo 36).
- N-D: se reescriben los textos de mora; **el texto exacto se presenta al Orquestador antes de construir** (Lote 6).
- N-E: mini-orden **O02-H** (guardas de aprobar/rechazar solo desde `pending_payment`, `cancel_and_buy` libera perfil, express acepta `active|expired`), **sin tocar `auto_expire` ni el corte** (se retiran juntos en el Lote 6).
- N-F: el periodo empieza en la aprobación (fin de día Caracas, mes calendario).
Estado: decisiones cerradas; **no se ha construido nada**. Siguiente: veredicto de arranque del Lote 1 y de la orden O02-H, y el resultado del script SQL.

## Decisiones que el Orquestador debe tomar
1. ~~A o B sobre el corte D+7~~ **Resuelta: B.** Pendiente solo confirmar el resumen diario de mora al admin (Lote 6).
2. **`expired` en datos existentes:** tratarlo como mora (recomendado) o migrar a `active`.
3. **Aprobación atómica por función SQL (recomendada)** o secuencial con compensación.
4. TTL de una orden sin pagar (propuesta: 24 h; la reserva de perfil sigue en 30 min).
5. Redondeo del Bs: total = total USD × tasa una sola vez, redondeo a 2 decimales (propuesta).
6. ¿Se conserva `streamvip/` mientras tanto? (S-006; no se toca en esta orden).

## Archivos esperados
`services/checkout_service.py` (nuevo) · `services/locks.py` (nuevo) · `utils/dates.py` (nuevo) · `database/orders.py` (nuevo) · `database/subscriptions.py` · `database/profiles.py` · `bot/handlers/subscription.py` · `bot/handlers/renovar.py` · `bot/handlers/admin.py` · `bot/handlers/my_services.py` · `main.py` (enrutador de fotos) · `scheduler/jobs.py` · `CLAUDE.md` · `admin_panel/router.py` y plantillas de pagos y cliente · `supabase_schema.sql` · `tests/` · `docs/FLUJOS.md` (máquinas de estado) · `docs/ESTADO.md` · `features.md` (Fase 8).

## Plan por lotes
| Lote | Riesgo | Requiere |
|---|---|---|
| 0 decisiones y diagnóstico | 🟡 | autorización de lectura a producción |
| 1 servicios base | 🟡 | — |
| 2 migración | 🔴 | Orquestador ejecuta SQL |
| 3 motor | 🔴 | Lote 2 |
| 4 bot | 🔴 | Lotes 1 a 3, flag apagado |
| 5 admin | 🔴 | Lote 3 |
| 6 scheduler | 🔴 | decisión B tomada |
| 7 saneamiento | 🔴 | autorización por ejecución |
| 8 documentación de cierre | 🟢 | — |
