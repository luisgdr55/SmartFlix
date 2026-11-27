# O01 — Seguridad y autenticación del panel · 🔴 · rama `s22-seguridad-auth`

**Estado:** **Lote 0 construido** en la rama `claude/keen-mendel-vn6j7b`, pendiente de revisión del PR y de las precondiciones (detalle en `docs/subfases/o01-lote0-seguridad-panel.md`); Lote 1 sin iniciar. Borrador original del Orquestador (27/11/2026) · **Depende de:** decisión sobre S-001 (rotar claves) · **Precede a:** O02, O03, O04
**Evidencia:** informe privado del Arquitecto (no versionado: describe fallos aún sin corregir en un repo público).

## Objetivo
Pasar de «una clave fija en una variable de entorno y una cookie constante» a una autenticación de plataforma de gestión: usuarios y roles, contraseñas con hash, sesiones revocables, MFA para el propietario, CSRF, límite de intentos y auditoría de toda acción. Cerrar, en un lote previo y sin cambio de esquema, los huecos de seguridad del panel.

## Impacto (antes de cambiar)
- Toca: `admin_panel/auth.py`, `admin_panel/router.py`, plantillas, `config.py`, `main.py` (middlewares y docs de FastAPI). No toca handlers del bot ni scheduler.
- Datos de clientes: el panel muestra credenciales de cuentas de streaming; cambia cómo se muestran (bajo demanda y auditado).
- Retrocompatibilidad: al desplegar, las sesiones actuales se invalidan (un re-login). El flujo del bot no cambia.
- Esquema: **Lote 1 requiere migración en Supabase antes de desplegar** (disparador de escalamiento).

## Alcance

### Lote 0 — Endurecimiento sin cambio de esquema (desplegable solo)
1. Secretos: eliminar valores por defecto de `SECRET_KEY`, `ADMIN_PANEL_PASSWORD` y `WEBHOOK_SECRET`; el arranque falla con mensaje claro si falta alguno.
   **Precondición:** confirmar (solo nombres, nunca valores) que las tres existen en Railway; si falta alguna, la crea el Orquestador antes de fusionar.
2. Comparaciones en tiempo constante (`hmac.compare_digest`) en el login y en el secreto del webhook.
3. Escape contextual de todo dato controlado por el cliente o por el admin en atributos HTML y manejadores inline de las plantillas (datos por `data-*` o bloque JSON; lógica en JS). Revisar **todas** las plantillas, no solo las reportadas.
4. Operaciones que mutan por GET pasan a POST: cambio de tasa por Binance y cierre de sesión.
5. Token CSRF ligado a la sesión en todo POST y en los `fetch` del panel.
6. Cabeceras: CSP (primero `Report-Only`), `frame-ancestors`/`X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS, `Cache-Control: no-store` bajo `/panel`; cookie con `Secure`.
7. Desactivar en producción `/docs`, `/redoc` y `/openapi.json`.
8. Límite de intentos de login por IP y global (Redis) con bloqueo progresivo y mensaje que ofrece salida (esperar / contactar al propietario).
9. Errores al usuario sin detalle interno: mensaje humano + ID de error; el detalle va al log. Validar `page`, importes y filtros (rechazo con aviso, no lista vacía silenciosa).
10. Credenciales de cuentas de streaming: dejan de incrustarse en listas; se revelan bajo demanda (POST), con registro de auditoría.

### Lote 1 — Identidad (migración)
- Tablas `admin_users`, `admin_sessions`, `admin_audit_log` (ver «Datos»).
- Login por correo + contraseña; política de longitud mínima 10 y rechazo de contraseñas comunes.
- Sesión opaca: ID aleatorio en cookie `HttpOnly; Secure; SameSite=Lax`; en base solo su hash. Expiración por inactividad (8 h) y absoluta (7 d); rotación del ID al iniciar sesión y al cambiar privilegios; cerrar sesión **invalida** en servidor; «cerrar las demás sesiones»; lista de sesiones activas.
- Roles: `owner`, `operator`, `viewer` (matriz abajo). Permisos verificados en backend en cada ruta.
- Cambio de contraseña desde el panel (pide la actual) y gestión de usuarios por el propietario (crear, desactivar, restablecer).
- MFA (TOTP) obligatorio para `owner`; opcional para los demás.
- Recuperación asistida: el propietario restablece a otros usuarios; un propietario bloqueado se recupera con procedimiento documentado en `OPERACION.md` (acceso a Supabase), no por correo.
- Arranque (bootstrap): mientras `admin_users` esté vacío, la contraseña heredada solo permite crear el primer propietario y forzar su contraseña personal; después queda inerte.
- Auditoría: cada POST del panel registra actor, acción, entidad, resumen antes/después, IP y hora; pantalla «Actividad» con filtros. Tabla de solo inserción.

| Capacidad | owner | operator | viewer |
|---|---|---|---|
| Ver dashboard, clientes, suscripciones | ✅ | ✅ | ✅ |
| Ver credenciales de cuentas | ✅ | ✅ (auditado) | ❌ |
| Aprobar/rechazar pagos, cortar, editar | ✅ | ✅ | ❌ |
| Precios, tasa, datos de pago, cuentas, plataformas | ✅ | ❌ | ❌ |
| Usuarios del panel, sesiones, actividad | ✅ | ❌ | ❌ |

## Datos (migración propuesta, la ejecuta el Orquestador)
```sql
CREATE TABLE admin_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  name VARCHAR(120),
  password_hash TEXT NOT NULL,
  role VARCHAR(20) NOT NULL CHECK (role IN ('owner','operator','viewer')),
  totp_secret_enc TEXT, mfa_enabled BOOLEAN DEFAULT FALSE,
  is_active BOOLEAN DEFAULT TRUE, failed_attempts INT DEFAULT 0, locked_until TIMESTAMPTZ,
  last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE admin_sessions (
  id_hash CHAR(64) PRIMARY KEY, admin_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
  ip INET, user_agent TEXT, created_at TIMESTAMPTZ DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ DEFAULT NOW(), expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ);
CREATE INDEX ON admin_sessions(admin_id) WHERE revoked_at IS NULL;
CREATE TABLE admin_audit_log (
  id BIGSERIAL PRIMARY KEY, admin_id UUID, action VARCHAR(80) NOT NULL, entity VARCHAR(40), entity_id TEXT,
  detail JSONB, ip INET, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE INDEX ON admin_audit_log(created_at DESC);
```
Reutiliza `admin_log` existente solo para el bot; el panel escribe en `admin_audit_log`.

## NO incluye
Login de clientes (O04) · permisos del bot de Telegram · rotación de claves y decisión repo público (son del Orquestador, S-001/S-002) · cambio de dominio o WAF · recuperación por correo (fase posterior) · cambios de lógica de pagos (O02).

## Casos de falla y salidas
- Variable de secreto ausente al arrancar → el servicio no inicia y el log dice cuál falta (precondición evita caída en producción).
- Propietario olvida contraseña o pierde el TOTP → procedimiento de recuperación documentado y probado en un entorno de prueba.
- Usuario bloqueado por intentos → mensaje con tiempo restante y a quién acudir; el propietario puede desbloquear.
- Doble envío del login o de «cambiar contraseña» → botón deshabilitado en curso y siempre rehabilitado.
- Sesión vencida a mitad de una acción → vuelve al login y regresa a la misma página sin perder lo escrito.

## Criterios de aceptación
- Dado un nombre de cliente con comillas y etiquetas, cuando el admin abre cualquier lista, entonces nada se ejecuta y el texto se ve literal.
- Dado que falta `SECRET_KEY`, cuando arranca el servicio, entonces falla con mensaje y no usa un valor por defecto.
- Dado 5 intentos fallidos, cuando intenta el sexto, entonces recibe bloqueo temporal con instrucción de salida.
- Dado un cierre de sesión, cuando se reutiliza la cookie anterior, entonces es rechazada.
- Dado un `operator`, cuando intenta cambiar precios o crear usuarios, entonces recibe 403 (verificado por backend, no solo por menú).
- Dado un `viewer`, cuando abre una ficha de cliente, entonces no ve credenciales.
- Dada cualquier acción de escritura, entonces existe su fila en `admin_audit_log`.
- Dado un GET a las rutas que antes mutaban, entonces responde 405.

## Pruebas obligatorias (stdlib `unittest`, sin dependencia nueva)
Hash y verificación · expiración/revocación/rotación de sesión · CSRF (falta, token ajeno) · límite de intentos con Redis falso · matriz de permisos por rol · render de plantillas con datos hostiles · cabeceras con `TestClient`. Las que dependan de Supabase real las prueba el Orquestador; el cierre lo declara.

## Decisiones que el Orquestador debe tomar
1. **¿Habrá más de una persona con acceso al panel?** (la regla fija dice «el admin es el único que aprueba pagos»; los roles `operator`/`viewer` la amplían a propósito).
2. **Hash de contraseñas:** `argon2-cffi` (recomendado por ATLAS; **dependencia nueva**, requiere tu aprobación y versión exacta) o `hashlib.scrypt` de la biblioteca estándar (sin dependencia; aceptable).
3. **TOTP:** implementación propia con la biblioteca estándar (RFC 6238) o `pyotp` (dependencia nueva).
4. **CI:** añadir un workflow de GitHub Actions que corra `compileall` y las pruebas (S-005).
5. **S-001:** rotar claves y decidir repo público/privado **antes** de fusionar el Lote 0.

## Archivos esperados
`config.py` · `main.py` · `admin_panel/auth.py` · `admin_panel/security.py` (nuevo: CSRF, cabeceras, límites) · `admin_panel/router.py` · `admin_panel/templates/*` · `database/admin_users.py` (nuevo) · `supabase_schema.sql` · `tests/` (nuevo) · `docs/OPERACION.md` (recuperación) · `docs/ESTADO.md`.

## Plan por lotes
| Lote | Contenido | Riesgo | Despliegue |
|---|---|---|---|
| 0 | Endurecimiento (1 a 10) | 🔴 | Tras confirmar variables y S-001 |
| 1a | Migración + login, sesiones, roles | 🔴 | Tras migración |
| 1b | MFA, gestión de usuarios, actividad | 🔴 | |
| 1c | Retiro de la contraseña heredada | 🔴 | Tras confirmar primer propietario |

Paquete de Auditoría con revisor independiente para cada lote 🔴.
