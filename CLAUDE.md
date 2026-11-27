# CLAUDE.md — SmartFlixVE

Bot de Telegram para alquiler de perfiles de streaming en Venezuela (Netflix, Disney+, Paramount+, HBO Max, Prime Video). **En producción con clientes reales.**
Stack: Python 3.11+, FastAPI + Uvicorn, python-telegram-bot (webhook), Supabase (PostgreSQL), Redis (Upstash), APScheduler (America/Caracas), Gemini Vision (OCR de pagos), Railway.
Mapa: `bot/handlers/` (lógica del bot), `admin_panel/` (panel web FastAPI), `database/` (acceso a Supabase), `services/` (Gemini, Redis, notificaciones), `scheduler/jobs.py` (tareas programadas), `main.py`.

## Entorno y roles (desde el 15-nov-2026)

SmartFlixVE se trabaja en la nube (Claude con el repo de GitHub `luisgdr55/SmartFlix`). Ya no hay Arquitecto en Claude Web ni copiar y pegar «Instrucciones para Claude Code»: **un solo agente cumple los dos papeles**,
con la skill ATLAS, y **el Orquestador (Luis) decide**. El texto anterior está en `docs/arquitecto/instrucciones-proyecto-claude-web-HISTORICO.md`.

- **Modo Arquitecto:** analiza el problema, diseña la solución y escribe la orden (objetivo, archivos que toca, qué NO incluye) en `docs/arquitecto/ordenes/<orden>.md`; la hace aprobar antes de construir.
  Mantiene la coherencia con la lógica de negocio existente. No decide en silencio lo que es del Orquestador (alcance, lo que ve el cliente, dinero, seguridad).
- **Modo Desarrollador:** ejecuta la orden con las reglas de este archivo. **No decide arquitectura ni improvisa**: ante un disparador, DETENTE y pregunta.
- **Regla de negocio fija:** el admin es el único que aprueba pagos y gestiona clientes.

## Orden de lectura (siempre, antes de actuar)

1. Este archivo.
2. `docs/ESTADO.md`: §0 (en curso) y §1 (última orden).
3. `features.md` **antes de implementar algo nuevo** (puede existir ya o estar planificado). Después, la orden y solo lo que cite. Pendientes: `docs/RECORDATORIOS.md`; deuda: `docs/DEUDA.md`; histórico: `docs/archivo/estado_actual-historico.md` (se busca, no se lee entero).

## Flujo de trabajo

1. **Antes de cambiar:** lee el código relevante y escribe el impacto: qué handlers, jobs o tablas toca, si afecta datos de clientes y si es retrocompatible con los flujos activos.
2. **Plan** (archivos y por qué); lo que detectes fuera de alcance se **reporta, no se corrige**.
3. **El diff es la revisión:** va en el Pull Request; el Orquestador lo aprueba ahí. Nada llega a `master` sin su veredicto.
4. **Cierre de cada orden:** actualiza `docs/ESTADO.md` (§0, §1 en máx. 8 líneas, fila de §3) y `features.md` si cambió el estado de una feature; el detalle va en `docs/subfases/<orden>.md`. Un commit por grupo de cambios relacionados.
- Veredictos del Orquestador: **APROBADO** (fusión por PR) · **APROBADO CON AJUSTES** (aplicas, re-verificas, fusionas y cierras sin pedir otro veredicto) · **MODIFICAR** · **DETENTE**.

### Disparadores de escalamiento (DETENTE y pregunta)

- Dependencia nueva o cambio de versión mayor.
- Cambio de esquema en Supabase o cualquier script que lea o escriba la base de producción. Las features que agregan columnas **requieren migración en Supabase antes de desplegar el código**.
- Algo contradice la orden, una regla de este archivo o la lógica de negocio documentada.
- Algo fuera del alcance escrito, o un hallazgo de seguridad (se reporta; no se corrige en silencio).

## Git, PR y despliegue

- **`master` = producción.** Railway despliega en cada fusión a `master` y **no hay `watchPatterns`**: hasta un commit solo de docs redespliega y reinicia el bot (el webhook se reregistra al arrancar). Fusiona en horario de baja actividad
  y verifica después el estado y los logs de Railway. **Nunca empujes directo a `master`.**
- Una rama por orden: `s19-nombre`, `fix/…`, `docs/…`, `rNN-…` para rescates. Toda rama se empuja a `origin` en cuanto tiene commits.
- La fusión es un **Pull Request** (merge `--no-ff`) y **solo con el veredicto del Orquestador**. Si `gh pr create` falla (GraphQL no disponible), usa `gh api repos/luisgdr55/SmartFlix/pulls`.
- Commits descriptivos en español (qué y por qué): `feat(modulo): …` / `fix(…)` / `docs: …`. Nunca `git add .` a ciegas.
- **El repositorio es PÚBLICO** (ver `docs/RECORDATORIOS.md` R00-1): todo lo que se commitea lo ve cualquiera. Cero claves, tokens, URLs con credenciales, datos de clientes (nombres, teléfonos, credenciales de cuentas de streaming) en código, pruebas, docs, commits o PR.
  Fixtures y ejemplos solo con datos ficticios.

## Reglas de desarrollo

- No inventes librerías, APIs, métodos ni versiones: verifica y declara versiones exactas. Sin dependencia nueva sin aprobación.
- Todo error se maneja con su ruta de recuperación; el cliente nunca queda en un callejón sin salida (todo mensaje de aviso ofrece una acción). Evita `except Exception` amplios que conviertan condiciones benignas de Telegram en errores visibles (sesión 18).
- Rate limiting en toda difusión (máx. 30 mensajes/segundo, límite de Telegram). Jobs nuevos: se agregan en `setup_scheduler()`. Zona horaria `America/Caracas` en el scheduler; fechas almacenadas en UTC.
- Guardas de negocio que no se rompen: **no hay corte automático** (decisión O02, opción B): la mora solo genera recordatorios D+1 a D+6 y un resumen diario al admin; el corte es siempre manual y advierte si el cliente tiene un pago pendiente (orden `pending` o pago legado). **Nunca reactivar un job que corte o libere perfiles solo; quitar `auto_expire` sin quitar el corte lo revivía.** La mora se deriva de `end_date` (`is_overdue`), no es un estado; las fechas de negocio son de Caracas (`utils/dates.py`, nunca `end_date[:10]`); los avisos se reclaman de forma atómica antes de enviarse y todo job lleva candado Redis. El flag `CHECKOUT_V2_ENABLED` (por defecto `false`) activa las órdenes de pago; no se enciende sin migración ejecutada y prueba del Orquestador. Además: el carrito vive en Redis (`cart:{tid}`, `renewal_cart:{tid}`, TTL 30 min); el comprobante se valida por OCR pero **el admin aprueba**.
- Infraestructura: `REDIS_URL` con esquema `rediss://` (Upstash con TLS); Supabase con service role key (verifica RLS si un update falla en silencio); el webhook de Telegram se reregistra al arrancar (`lifespan` en `main.py`).
- **Verificación:** no hay pruebas automatizadas ni CI (R00-5). Antes de reportar, ejecuta lo que sí se puede (`python -m compileall`, importar `main`, el script `test_renewal_cart.py` si aplica) y dilo; **nunca declares verificaciones que no ejecutaste**.
  Lo que dependa de Telegram, Supabase o Redis reales lo prueba el Orquestador. Contra producción no se ejecuta nada sin su autorización expresa.

## Secretos y producción

- **Nunca** pegues en código, docs, commits, PR ni chat valores de variables de entorno (token del bot, `SUPABASE_KEY`, `GEMINI_API_KEY`, `GMAIL_*`, `IMAP_*`, `SECRET_KEY`, `WEBHOOK_SECRET`, contraseña del panel). Las lecturas de Railway por MCP son de estado, logs y **nombres** de variables, no de valores.
- No cambies variables, servicios, TCP Proxy ni dominios de Railway sin orden escrita. Contra la base de producción no se ejecuta nada sin autorización expresa del Orquestador para esa acción.

## Antes de reportar que terminaste

`git diff --stat` sin archivos ajenos a la orden; sin `.pyc` ni `.env` en el diff; docs de cierre actualizadas; rama empujada y PR abierto sin fusionar.

## Si trabajas en la PC de Luis (Windows)

- **PROHIBIDO `taskkill /IM node.exe`** (u otro comando por nombre de imagen). Un proceso Python se termina por **puerto o PID** (`netstat -ano` + `taskkill /PID`).
- La PC no es la fuente de verdad: las ramas se empujan a `origin`.
