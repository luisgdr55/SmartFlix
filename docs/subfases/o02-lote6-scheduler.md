# O02 Lote 6 — Scheduler sin corte automático (opción B) y cierre de la parte A del Lote 5

Rama `claude/keen-mendel-vn6j7b`. Sin fusionar, sin tocar producción. Nada de lo nuevo cambia al cliente mientras `CHECKOUT_V2_ENABLED=false`, **salvo** lo marcado «efecto inmediato».

## Parte A — recomendaciones del Lote 5 aplicadas
- **Aprobación parcial** dividiendo la orden: función SQL `split_and_approve_checkout_order(UUID,TEXT,INT[],TEXT,TEXT)`; la orden hija (`parent_order_id`) queda pendiente con el resto. Motor Python `approve_partial` y botón en el panel. **Telegram no tiene interfaz parcial** (se decide en el panel).
- **Anular una orden aprobada** con asiento contrario: `void_checkout_order(UUID,TEXT,TEXT)`; periodos tipo `'reversa'`, `subscriptions.void_reason/voided_at`. Panel sí; Telegram no.
- `/renovar` por el motor queda **fuera** (orden aparte, como se acordó).
- **El SQL de la migración cambió** (funciones nuevas, `parent_order_id`, `kind='reversa'`): el Orquestador debe releer `docs/migraciones/o02-lote2-checkout.sql` antes de ejecutarla. Sigue sin ejecutarse.

## Parte B — scheduler (decisión B: sin corte automático)
- **No hay corte automático D+7.** Se eliminó `job_auto_expire` y la parte de corte de `job_debt_reminders_and_cuts` (las dos juntas: quitar solo `auto_expire` habría revivido el corte). Se borraron `get_subscriptions_past_grace_period`, `get_subscriptions_in_grace_period`, `increment_debt_reminder`, `auto_expire_overdue_subscriptions` y `HARD_CUT_NOTIFICATION`.
- Avisos: D-3, D+0 (solo vencidas en las últimas 48 h, así los `expired` viejos no reciben ráfaga) y D+1..D+6 (uno por día, mensual, nunca después del día 6).
- **Reclamo atómico antes de enviar** (`claim_flag`, `claim_debt_reminder`: UPDATE condicional) y devolución del reclamo si el envío falla; así dos instancias o dos pasadas no duplican avisos.
- Cada job con **candado Redis** (`_locked_job`, falla cerrado: si Redis cae, el job no corre), `coalesce=True`, `max_instances=1`, `misfire_grace_time` explícito. 11 jobs registrados (sin `auto_expire`; nuevo `expire_orders` cada 30 min, solo con el flag encendido).
- **Resumen diario de mora al admin** (`services/mora.py`): clientes con ≥7 días, deuda en USD, tope 15 líneas, botones al detalle del cliente; dice que el servicio sigue activo.
- `send_to_user` reintenta ante `RetryAfter` (3 intentos); otros errores no reintentan.
- **Corte manual con advertencia**: si el cliente tiene un pago pendiente (orden o pago legado), Telegram, IA del admin y panel piden confirmación explícita (`:force` / página `confirm-cut`).
- Mora **derivada** de la fecha en panel (filtro «En mora»), «Mis servicios», analítica (activos = no vencidos) y reporte mensual (no renovados = active|expired). Los GET del panel ya no escriben.
- La renovación legada ahora reinicia `reminder_sent`, `expiry_notified` y `debt_reminder_count`.
- Efecto inmediato (aunque el flag siga apagado): fin del corte automático, textos nuevos de aviso, resumen de mora, conteo de activos en analítica.

## Textos para revisión del Orquestador (lo que ve el cliente)
`DEBT_REMINDER` reescrito (mora en días, sin amenaza de liberación, siempre con botón de renovar; el día 6 dice «último recordatorio»), `EXPIRY_NOTIFICATION` suavizado. Son propuestas; se ajustan a tu veredicto.

## Verificado y no verificado
- Verificado: 343 pruebas `unittest` OK (27 nuevas en `tests/test_lote6_scheduler.py`), `compileall`, `build_telegram_app()` con flag apagado y encendido, pruebas de panel con TestClient. Mutaciones comprobadas: quitar el UPDATE condicional del reclamo o la ventana de 48 h hace fallar pruebas.
- Las pruebas encontraron un defecto propio antes de entregar: la advertencia de corte en Telegram usaba `InlineKeyboardMarkup` sin importarla (habría fallado en producción). Corregido.
- **No verificado** (lo prueba el Orquestador): Telegram, Supabase, Redis, Railway y navegador reales; migración no ejecutada.

## Hallazgos fuera de alcance (reportados, no corregidos)
- `services.notification_service.send_to_admins` no existe aunque el panel lo importa: los avisos al admin desde el panel fallan en silencio.
- Contadores `debt_reminder_count` obsoletos en filas viejas → Lote 7.
- Rama remota `fix/o02h-guardas-pagos-perfiles` sigue sin borrar (el proxy lo rechazó).

## Preguntas
1. ¿Apruebas los textos de aviso y el resumen diario (hora actual del job)?
2. ¿Aprobación parcial/anulación también en Telegram, o basta el panel?
3. Orden para ejecutar la migración y probar de punta a punta antes de encender el flag.
