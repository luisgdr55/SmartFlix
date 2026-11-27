# O01 Lote 0 — Endurecimiento del panel (sin cambio de esquema)

Rama `claude/keen-mendel-vn6j7b` · 🔴 · construido el 27/11/2026 · **sin fusionar**: espera veredicto del Orquestador.
Orden: `docs/arquitecto/ordenes/o01-seguridad-autenticacion-panel.md` (Lote 0, puntos 1 a 10).

## Qué cambia, por punto de la orden
| # | Cambio | Archivos |
|---|---|---|
| 1 | `WEBHOOK_SECRET`, `SECRET_KEY` y `ADMIN_PANEL_PASSWORD` sin valor por defecto. Arranque falla si faltan, están vacías o coinciden con los valores antiguos públicos (se comparan por huella SHA-256; el código ya no los contiene). Los errores de arranque no imprimen valores (`hide_input_in_errors`). | `config.py`, `admin_panel/auth.py` |
| 2 | `hmac.compare_digest` en el login del panel y en el secreto del webhook. | `admin_panel/auth.py`, `main.py` |
| 3 | Datos de clientes y del admin salen de los atributos y manejadores inline: pasan por `data-*` (autoescapados) y el JS los lee con `$el.dataset`. Revisadas **todas** las plantillas con un analizador de HTML. Reescritos los modales de credenciales de `/subscriptions` (además estaba fuera de su `x-data`, no funcionaba) y de la ficha de cliente. Filtros de URL con `|urlencode`. | `templates/subscriptions.html`, `user_detail.html`, `users.html`, `accounts.html`, `profiles.html`, `payments.html` |
| 4 | Cerrar sesión y cambio de tasa por Binance pasan de GET a **POST** (el GET responde 405). | `router.py`, `base.html`, `dashboard.html`, `settings.html` |
| 5 | CSRF: token ligado a la cookie de sesión (HMAC con `SECRET_KEY`), exigido como dependencia de **todo** el router a los métodos que modifican; campo oculto en los 30 formularios POST y cabecera `X-CSRF-Token` añadida automáticamente a todo `fetch` no GET del mismo origen. Login con doble envío mediante cookie propia. Si falla: página con salida («Volver al panel») o JSON, sin cambios aplicados. | `admin_panel/security.py` (nuevo), `router.py`, plantillas |
| 6 | Cabeceras: `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS (si `APP_URL` es https), y bajo `/panel`: `Cache-Control: no-store` y CSP en modo **Report-Only** (no bloquea nada; se endurece en O03). Cookie de sesión con `Secure` cuando `APP_URL` es https. | `admin_panel/security.py`, `auth.py`, `main.py` |
| 7 | `/docs`, `/redoc`, `/openapi.json` solo existen con `DEBUG=true`. | `main.py` |
| 8 | Límite de intentos de login por IP: 5 intentos en 15 min y se bloquea 15 min, duplicando en cada reincidencia hasta 24 h (la memoria de reincidencias se renueva y dura más que el bloqueo máximo). **Cada intento se cuenta antes de verificar la clave**, así una ráfaga concurrente no puede probar más de 5. Demora de 2 s por intento con ≥ 20 fallos globales en 10 min. Redis (en un hilo, sin congelar el bucle de eventos; contadores siempre con TTL) con respaldo en memoria. IP del cliente según `TRUSTED_PROXY_HOPS` (por defecto 1 = Railway). Mensaje con tiempo de espera y salida; cabecera `Retry-After`. | `admin_panel/security.py`, `router.py`, `config.py` |
| 9 | Errores sin detalle interno: 37 puntos (`?error=...`, JSON y contexto) ahora muestran un mensaje humano con un código corto; el detalle va al log. Validación de importes (texto, negativos, NaN, infinito, topes), tasas (> 0), Telegram ID, `page` (aviso en vez de lista vacía) y búsqueda (se quitan los caracteres que rompen el filtro `or_`). Un fallo de base en las listas ya no se disfraza de lista vacía. | `router.py` |
| 10 | Credenciales de cuentas de streaming ya **no** van en el HTML de listas ni fichas: se piden con `POST /panel/subscriptions/{id}/credentials` al pulsar «Mostrar», con `Cache-Control: no-store` y registro de auditoría (`admin_log`, `admin_telegram_id=0` = panel, hasta O01 Lote 1). Lo mismo para el **PIN** y la contraseña del cupo adicional de los perfiles (`POST /panel/profiles/{id}/credentials`). Los formularios de edición no muestran contraseñas ni PIN: **vacío = conservar, «-» = borrar**. Las consultas de lista dejaron de traer `password` y `pin`. | `router.py`, `subscriptions.html`, `user_detail.html`, `account_form.html`, `profiles.html`, `profile_form.html` |

## Verificación (ejecutada aquí, el 27/11/2026)
- `python -m compileall` sobre el repo (sin `streamvip` ni el entorno virtual): OK. `import main` con variables ficticias: OK.
- `python -m unittest discover -s tests -t .` → **86 pruebas OK** (`tests/test_panel_security.py` para este lote y `tests/test_ai_admin_rate.py` para O05 Lote 5). Solo `unittest`, sin dependencias nuevas; base, Redis y Telegram falsos.
- Las pruebas de XSS y de credenciales **fallan con las plantillas antiguas**: renderizadas desde `HEAD` con el mismo texto hostil se inyectan `x-init`, `onerror` y un atributo, y la contraseña de streaming va en el HTML.
- Comprobación en Chromium real (Playwright) con datos hostiles: los modales de credenciales, el botón «Mostrar» del PIN y el botón de editar de la ficha de cliente funcionan; las credenciales no están en el HTML inicial, las piden con la cabecera CSRF y no hay errores de consola ni código ejecutado. Esa comprobación **encontró un error propio** que las pruebas unitarias no ven (`this.$el` dentro de un método de Alpine es el botón, no la raíz: pedía `/profiles/undefined/credentials`); quedó corregido y con una prueba estática que lo vigila.
- Pruebas de mutación: reintroducir cada defecto (CSRF con `str`, intento contado tras verificar, PIN en blanco que borra, `$el` fuera de `init()`) hace fallar sus pruebas.

## Revisión independiente (subagente sin el contexto de la implementación, 27/11/2026)
Corregido en este mismo lote:
- **Alto**: un token CSRF con caracteres no ASCII daba HTTP 500 (`compare_digest` con `str`); ahora se compara en bytes y se rechaza con 403/400.
- **Medio**: ráfaga concurrente con la demora global activa podía probar N claves; ahora el intento se cuenta antes de verificar.
- **Medio**: Redis síncrono congelaba el bucle de eventos (y el webhook) si Upstash tardaba; ahora corre en un hilo, y los contadores nacen con TTL (`SET NX EX` + `INCR`).
- **Medio**: PIN y contraseña del cupo seguían en el HTML (lista y formulario de perfiles) y las consultas de lista traían `password`; ahora bajo demanda y sin traerlos.
- **Medio**: validación de arranque: la huella ignora mayúsculas y espacios; `WEBHOOK_SECRET` debe tener el formato que Telegram acepta; aviso en el log (sin valor) si un secreto tiene menos de 16 caracteres. **No se rechaza por longitud** para no tumbar el servicio si hoy Railway tiene una clave corta.
- **Bajo**: `fetch` ya no envía el token a URLs `//host`; los botones del dashboard no interpolan ids en JavaScript; la confirmación de la tasa responde siempre (aunque falle una consulta) y un fallo de auditoría no oculta que la tasa cambió; el aviso de la tasa ya no manda al admin a `/tasa` (sin validación) sino al panel; si la auditoría no se registra queda un error explícito en el log.

Queda abierto, para decisión o para otra orden:
- **IP del cliente tras el proxy (alto, sin verificar en producción)**: si la entrada de `X-Forwarded-For` que se toma no es la del cliente, todos comparten el contador y alguien puede bloquear el login de todos hasta 24 h; **no hay vía de desbloqueo** salvo esperar. Mitigación incluida: `TRUSTED_PROXY_HOPS`. Pendiente del Orquestador: comprobar la IP en los logs de Railway y decidir si se añade un comando de desbloqueo por Telegram.
- La auditoría **no bloquea** la entrega de credenciales si falla su registro (decisión de disponibilidad, pendiente de confirmar); deja un error en el log.
- `_public_error` da «código xxxx» también en errores benignos (clave duplicada); mejora de UX para O03.
- Cierre de sesión sin revocación en servidor y CSP solo Report-Only con `unsafe-inline`/`unsafe-eval`: O01 Lote 1 y O03.
- Fuera de alcance: `cmd_tasa` sin validar (S-009); `ai_admin.py` muestra al admin el texto de un error de IMAP, que puede incluir host o usuario; `streamvip/` conserva sus valores por defecto antiguos (S-006).

## NO verificado (lo prueba el Orquestador)
- Nada contra Supabase, Redis (Upstash) ni Telegram reales; la inserción en `admin_log` con `admin_telegram_id = 0` y el comportamiento del cliente Redis síncrono bajo carga.
- Que la **IP detectada tras el proxy de Railway** sea la del cliente (se toma la última entrada de `X-Forwarded-For`). Si no lo fuera, todos compartirían el contador y 5 fallos de cualquiera bloquearían el login 15 min.
- La CSP en modo Report-Only no se ha observado en el navegador de producción.

## Precondiciones ANTES de fusionar (bloqueantes)
1. **Variables en Railway** (solo nombres, nunca valores): `SECRET_KEY`, `ADMIN_PANEL_PASSWORD` y `WEBHOOK_SECRET` deben existir, no estar vacías y **no ser los valores antiguos por defecto**. Si no, el servicio entra en bucle de reinicio (`restartPolicyMaxRetries = 3`) y el bot queda caído. Plan de vuelta atrás: revertir la fusión.
2. **S-001**: rotar claves y decidir sobre el repo público. La contraseña temporal usada durante la auditoría debe sustituirse por una definitiva.
3. Las sesiones existentes siguen siendo válidas mientras no se cambie `SECRET_KEY`; se recomienda rotarla a la vez para invalidar cualquier cookie emitida durante la auditoría.
4. Confirmar que `DEBUG` no es `true` en producción.
5. Confirmar que la IP que ve el límite de login es la del cliente (ver «Queda abierto»); si Railway añade más de un proxy, definir `TRUSTED_PROXY_HOPS` (variable opcional, no secreta, por defecto 1).
6. Fusionar en horario de baja actividad (Railway reinicia el bot) y revisar los logs del arranque.

## Cambios de comportamiento que verá el admin
«Cerrar sesión» y «Obtener tasa de Binance» siguen donde estaban (ahora son POST). Las credenciales se muestran tras pulsar «🔓 Mostrar credenciales»; el PIN de un perfil, tras «Mostrar». En editar cuenta o perfil, la contraseña y el PIN no se muestran: **vacío = conservar y «-» = borrar**. Tras 5 contraseñas erróneas, el login se bloquea 15 minutos.

## Decisiones menores
- Telegram ID, importes y tasas se validan con helpers propios que devuelven un mensaje seguro (`InputError`), sin dependencias nuevas.
- Auditoría provisional en `admin_log` con `admin_telegram_id = 0`: evita un cambio de esquema; O01 Lote 1 la reemplaza por `admin_audit_log`.
- Token CSRF derivado de la cookie de sesión (sin estado en servidor): suficiente mientras la sesión sea una cookie firmada; en O01 Lote 1 pasa a ligarse al ID de sesión.

## Fuera de alcance, solo reportado
- `/tasa` (Telegram) y `update_rate` aceptan 0, negativos, `nan` e infinito: falta un guardián central (ver `docs/DEUDA.md` S-009).
- `/subscriptions` y la ficha de cliente siguen escribiendo en la base dentro de un GET (barrido de vencimientos): lo corrigen O02/O03.
- El logout no invalida el token en servidor y la sesión sigue siendo un valor constante firmado: O01 Lote 1.
