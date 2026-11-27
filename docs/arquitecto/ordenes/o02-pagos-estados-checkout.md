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

## Conflicto documental que requiere veredicto (decisión previa, Lote 0)
`CLAUDE.md` y `docs/ESTADO.md` dicen: corte automático D+7 **con** guarda de pago pendiente. `features.md` Fase 8 (decisiones 1 a 3, sesión 17) dice: **sin corte automático**, corte manual por el admin, recordatorios hasta D+6.
El comportamiento real en producción no coincide con ninguna de las dos (ver último punto del problema).
- **Opción A:** mantener corte D+7 con guarda (arreglando los filtros). **Opción B (recomendada):** Fase 8: la deuda es visible y el admin corta a mano; recordatorios D+1 a D+6; después la mora solo se ve en el panel.
Hasta que el Orquestador elija, no se toca el scheduler (Lote 6).

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
Veredicto sobre A/B y sobre las decisiones de abajo. Consulta de solo lectura (con autorización expresa del Orquestador) que lista anomalías: duplicados por usuario+plataforma, `expired_payment`, suscripciones vencidas con perfil ocupado, perfiles ocupados sin suscripción activa. Entrega un listado, no corrige nada.

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

### Lote 6 — Scheduler (según veredicto A/B)
Un único predicado `is_overdue(sub)` (active/expired con `end_date` < ahora) usado por todos los jobs; `auto_expire` deja de cambiar estados; el panel deja de escribir en GET. Cada job con `coalesce=True`, `max_instances=1`, `misfire_grace_time` explícito y candado Redis; reclamo condicional antes de enviar (`UPDATE … WHERE reminder_sent=false RETURNING`); rate limit de difusión ≤ 30/s con manejo de `RetryAfter`.

### Lote 7 — Saneamiento de datos existentes
Solo tras el listado del Lote 0 y con autorización expresa para cada ejecución contra producción: script con modo `--dry-run` por defecto que propone (no aplica) fusiones y anulaciones; el Orquestador aprueba fila por fila.

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

## Pruebas obligatorias
Suite `unittest` de lógica pura con repositorio falso: aritmética de fechas y zona horaria, totales con `Decimal`, transiciones válidas e inválidas, idempotencia, el escenario de dos servicios por plataforma, detector de anomalías. Lo que dependa de Supabase/Telegram real lo prueba el Orquestador; el cierre lo declara.

## Decisiones que el Orquestador debe tomar
1. **A o B** sobre el corte D+7 (arriba).
2. **`expired` en datos existentes:** tratarlo como mora (recomendado) o migrar a `active`.
3. **Aprobación atómica por función SQL (recomendada)** o secuencial con compensación.
4. TTL de una orden sin pagar (propuesta: 24 h; la reserva de perfil sigue en 30 min).
5. Redondeo del Bs: total = total USD × tasa una sola vez, redondeo a 2 decimales (propuesta).
6. ¿Se conserva `streamvip/` mientras tanto? (S-006; no se toca en esta orden).

## Archivos esperados
`services/checkout_service.py` (nuevo) · `services/locks.py` (nuevo) · `utils/dates.py` (nuevo) · `database/orders.py` (nuevo) · `database/subscriptions.py` · `database/profiles.py` · `bot/handlers/subscription.py` · `bot/handlers/renovar.py` · `bot/handlers/admin.py` · `bot/handlers/my_services.py` · `main.py` (enrutador de fotos) · `scheduler/jobs.py` · `admin_panel/router.py` y plantillas de pagos y cliente · `supabase_schema.sql` · `tests/` · `docs/FLUJOS.md` (máquinas de estado) · `docs/ESTADO.md` · `features.md` (Fase 8).

## Plan por lotes
| Lote | Riesgo | Requiere |
|---|---|---|
| 0 decisiones y diagnóstico | 🟡 | autorización de lectura a producción |
| 1 servicios base | 🟡 | — |
| 2 migración | 🔴 | Orquestador ejecuta SQL |
| 3 motor | 🔴 | Lote 2 |
| 4 bot | 🔴 | Lotes 1 a 3, flag apagado |
| 5 admin | 🔴 | Lote 3 |
| 6 scheduler | 🔴 | veredicto A/B |
| 7 saneamiento | 🔴 | autorización por ejecución |
