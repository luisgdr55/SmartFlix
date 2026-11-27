# O02-H — Guardas de pagos y fuga de perfiles

Rama `claude/keen-mendel-vn6j7b` (la asignada a la sesión; la orden proponía `fix/o02h-guardas-pagos-perfiles`) · 🔴 · construido el 27/11/2026 · **sin fusionar**: espera revisión del PR y veredicto.
Orden: `docs/arquitecto/ordenes/o02h-guardas-pagos-y-perfiles.md`. Veredicto de arranque: **APROBADO** con las recomendaciones (Q1 bloquear `expired_payment`; Q2 ventana de 24 h; Q3 aviso a todos los admins).

## Qué cambia
- **H1 aprobar** (`bot/handlers/admin.py`, `admin_panel/router.py::payment_approve`): solo si `status='pending_payment'`; si no, no se cambia nada y el admin ve el estado con salida (Telegram: botones «Pendientes» y «Panel admin»; panel: error legible). `confirm_subscription` ahora hace un `UPDATE` condicional y devuelve `False` si no actualizó filas; en ese caso **no** se llama `assign_profile` (en Telegram antes se llamaba siempre).
- **H2 rechazar** (Telegram y `payment_reject`): se carga la suscripción antes de borrar; si no existe o no está pendiente, no se borra ni se avisa al cliente.
- **H3 `cancel_and_buy`** (`main.py`): por cada suscripción `expired` del cliente libera el perfil con `release_profile_for_cut` y avisa al admin (PIN anterior/nuevo y cuenta; o «perfil NO liberado» si lo usa otra suscripción activa o no se pudo leer). La suscripción se cancela siempre; al cliente no le cambia nada.
- **H4 express** (`scheduler/jobs.py::job_express_release`, `get_expired_express_subscriptions`): también procesa las `expired` de las últimas 24 h cuyo perfil sigue `occupied`; las `active` siguen el flujo de siempre. Idempotente (el perfil liberado queda `available`).
- **Seguridad del cliente vigente** (`database/profiles.py::release_profile_for_cut`): no libera ni rota PIN si el perfil no está `occupied` o lo usa otra suscripción `active`.
- Lógica pura en `services/payment_guards.py`.

## Desvíos respecto a la orden (decididos al construir)
1. `release_profile_for_cut` devuelve `None` solo si no pudo leer el perfil; si lo leyó devuelve siempre un diccionario con `released` y `reason` (`no_ocupado` / `otra_suscripcion_activa`). Así los llamadores distinguen «ya estaba libre» de «lo usa otro».
2. **Q3**: se usa `send_to_admin`, que ya envía a **todos** los admins. `send_to_admins` (con «s») **no existe** en `services/notification_service.py` (ver hallazgos).
3. H4 sin aviso al admin cuando el guard rechaza una express `expired` (el job corre cada 5 min y repetiría el aviso durante 24 h): solo `logger.info`.
4. En el job, el filtro de la consulta usa `or_` con corte en UTC más un filtro de respaldo en Python.

## Verificación (ejecutada)
- `python -m unittest discover -s tests -t .`: **115 pruebas OK** (86 de antes + 29 nuevas en `tests/test_payment_guards.py`: lógica pura, `release_profile_for_cut` y `confirm_subscription` con base falsa, aprobar/rechazar por Telegram y por panel, y `job_express_release`). Se ejecutaron en un venv fuera del repo con las versiones de `requirements.txt` (el entorno no las traía instaladas).
- `python -m compileall` y `import main` (variables ficticias): OK.
- **No verificado** (lo prueba el Orquestador con Telegram, Supabase y Railway reales): el `or_` de PostgREST en la consulta de express; que `update(...).eq(...).execute()` devuelva filas con la versión desplegada (`supabase==2.7.4`; el panel ya lo usa en `subscription_release`); `_handle_cancel_and_buy` (función anidada en `main.py`, sin prueba automática); los mensajes reales al admin.

## Qué prueba el Orquestador tras desplegar
1. Aprobar dos veces un pedido de prueba: la segunda avisa «ya no está pendiente» y no asigna otro perfil.
2. Rechazar un pedido ya aprobado: no lo borra.
3. `cancel_and_buy` con una cuenta de prueba que tenga una suscripción vencida: perfil `available`, PIN nuevo y aviso al admin con ambos PIN.
4. Logs de Railway: «Running job: express_release» cada 5 min sin errores.
5. **Efecto del primer ciclo:** al desplegar, cualquier express `expired` de las últimas 24 h con perfil ocupado se libera una vez y su cliente recibe el aviso de «express vencida». Es lo esperado.

## Despliegue
Sin migración, sin dependencia, sin variable nueva. Fusionar en horario de baja actividad (Railway reinicia el bot). Reversa: revertir el PR.

## Reportado, no corregido (fuera de alcance)
- `services.notification_service.send_to_admins` **no existe**, pero `admin_panel/router.py` lo importa al aprobar/rechazar pagos (dentro de `try/except`): los avisos al admin desde el panel fallan en silencio. Conviene una orden propia.
- En `payment_approve` queda código inalcanzable tras el `if ok:` (la rama de error original); inocuo.
- Siguen abiertos N1, N2, N6, N7, N9 a N14 y las otras cinco formas de liberar un perfil (mapa del Lote 0).
- `expired_payment` queda sin salida administrativa hasta el motor de O02 (Q1).
