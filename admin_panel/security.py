"""Seguridad del panel de administración (O01, Lote 0).

- CSRF ligado a la sesión (formularios con campo oculto y ``fetch`` con cabecera).
- Cabeceras de seguridad y ``Cache-Control: no-store`` bajo ``/panel``.
- Límite de intentos de login con bloqueo progresivo (Redis, con respaldo en memoria).
- IP del cliente detrás del proxy de Railway.

No guarda ni registra contraseñas ni tokens.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import secrets
import time
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sv_admin_session"
LOGIN_CSRF_COOKIE = "sv_login_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# ── Política de intentos de login ────────────────────────────────────────────
LOGIN_WINDOW_S = 15 * 60          # ventana para contar fallos por IP
LOGIN_MAX_FAILS = 5               # fallos dentro de la ventana antes de bloquear
LOGIN_BASE_LOCK_S = 15 * 60       # primer bloqueo; se duplica en cada reincidencia
LOGIN_MAX_LOCK_S = 24 * 60 * 60   # tope del bloqueo
# La memoria de reincidencias debe durar más que el bloqueo máximo más una ventana de fallos;
# si no, un atacante constante volvería al bloqueo corto tras cada tope.
LOGIN_STRIKE_TTL_S = LOGIN_MAX_LOCK_S + 2 * LOGIN_WINDOW_S
GLOBAL_WINDOW_S = 10 * 60
GLOBAL_SLOWDOWN_FAILS = 20        # fallos globales que activan la demora
GLOBAL_SLOWDOWN_S = 2.0           # demora por intento cuando hay ataque distribuido


# ═════════════════════════════════════════════════════════════════════════════
# Utilidades
# ═════════════════════════════════════════════════════════════════════════════

def _secret_key() -> bytes:
    from admin_panel.auth import get_secret_key
    return get_secret_key().encode("utf-8")


def cookie_secure() -> bool:
    """La cookie lleva ``Secure`` cuando el panel se sirve por HTTPS (según APP_URL)."""
    try:
        from config import settings
        return str(settings.APP_URL).lower().startswith("https://")
    except Exception:
        return False


def client_ip(request: Request) -> str:
    """IP del cliente. Railway añade la IP real al final de ``X-Forwarded-For``;
    se usa la última entrada porque las anteriores las puede fijar el cliente."""
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-1][:64]
    return (request.client.host if request.client else "desconocida")[:64]


# ═════════════════════════════════════════════════════════════════════════════
# CSRF
# ═════════════════════════════════════════════════════════════════════════════

class CSRFError(Exception):
    """El token CSRF falta o no coincide."""


def csrf_token_for(session_cookie: str) -> str:
    """Token determinista ligado al valor de la cookie de sesión."""
    return hmac.new(_secret_key(), b"csrf:" + session_cookie.encode("utf-8"), hashlib.sha256).hexdigest()


def request_csrf_token(request: Request) -> str:
    return csrf_token_for(request.cookies.get(SESSION_COOKIE, ""))


async def csrf_protect(request: Request) -> None:
    """Dependencia del router: expone el token a las plantillas y exige que los
    métodos que modifican datos lo traigan (cabecera ``X-CSRF-Token`` o campo
    ``csrf_token``). El login tiene su propio doble envío con cookie."""
    request.state.csrf_token = request_csrf_token(request)
    if request.method in SAFE_METHODS:
        return
    if request.url.path.rstrip("/") == "/panel/login":
        return
    token = request.headers.get("x-csrf-token")
    if not token:
        try:
            form = await request.form()
            token = form.get("csrf_token")
        except Exception:
            token = None
    if not token or not hmac.compare_digest(str(token), request.state.csrf_token):
        logger.warning("CSRF rechazado: %s %s", request.method, request.url.path)
        raise CSRFError()


def new_login_csrf() -> str:
    return secrets.token_urlsafe(32)


def login_csrf_ok(request: Request, form_token: Optional[str]) -> bool:
    cookie = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    return bool(cookie and form_token) and hmac.compare_digest(cookie, str(form_token))


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return (
        request.url.path.startswith("/panel/api/")
        or bool(request.headers.get("x-csrf-token"))
        or "application/json" in accept
    )


async def csrf_error_handler(request: Request, exc: CSRFError):
    msg = "La sesión de seguridad venció. Recarga la página e intenta de nuevo."
    if _wants_json(request):
        return JSONResponse({"ok": False, "success": False, "error": msg, "message": msg}, status_code=403)
    page = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Sesión de seguridad vencida</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;color:#1e293b'>"
        f"<h1 style='font-size:1.25rem'>{html.escape(msg)}</h1>"
        "<p>No se hizo ningún cambio.</p>"
        "<p><a href='/panel/dashboard' style='display:inline-block;padding:.6rem 1rem;background:#1e293b;"
        "color:#fff;border-radius:.5rem;text-decoration:none'>Volver al panel</a></p>"
        "</body></html>"
    )
    return HTMLResponse(page, status_code=403)


# ═════════════════════════════════════════════════════════════════════════════
# Cabeceras de seguridad
# ═════════════════════════════════════════════════════════════════════════════

# Report-Only: no bloquea nada todavía (el panel usa Tailwind y Alpine desde CDN
# y estilos en línea); sirve para medir antes de endurecer en O03.
PANEL_CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def security_headers(path: str, secure: bool) -> dict[str, str]:
    h = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }
    if secure:
        h["Strict-Transport-Security"] = "max-age=31536000"
    if path.startswith("/panel"):
        h["Cache-Control"] = "no-store"
        h["Pragma"] = "no-cache"
        h["Content-Security-Policy-Report-Only"] = PANEL_CSP_REPORT_ONLY
    return h


# ═════════════════════════════════════════════════════════════════════════════
# Límite de intentos de login
# ═════════════════════════════════════════════════════════════════════════════

class _MemoryStore:
    """Almacén en memoria con expiración (respaldo si Redis no responde)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._d: dict[str, tuple[int, float]] = {}
        self._clock = clock

    def _live(self, key: str) -> Optional[tuple[int, float]]:
        item = self._d.get(key)
        if item and item[1] > self._clock():
            return item
        self._d.pop(key, None)
        return None

    def incr(self, key: str, ttl: int) -> int:
        item = self._live(key)
        if item:
            self._d[key] = (item[0] + 1, item[1])
            return item[0] + 1
        self._d[key] = (1, self._clock() + ttl)
        return 1

    def ttl(self, key: str) -> int:
        item = self._live(key)
        return max(0, int(item[1] - self._clock())) if item else 0

    def set(self, key: str, ttl: int) -> None:
        self._d[key] = (1, self._clock() + ttl)

    def delete(self, key: str) -> None:
        self._d.pop(key, None)

    def touch(self, key: str, ttl: int) -> None:
        item = self._live(key)
        if item:
            self._d[key] = (item[0], self._clock() + ttl)

    def get(self, key: str) -> int:
        item = self._live(key)
        return item[0] if item else 0


class _RedisStore:
    def __init__(self, client):
        self._r = client

    def incr(self, key: str, ttl: int) -> int:
        n = int(self._r.incr(key))
        if n == 1:
            self._r.expire(key, ttl)
        return n

    def ttl(self, key: str) -> int:
        t = int(self._r.ttl(key))
        return t if t > 0 else 0

    def set(self, key: str, ttl: int) -> None:
        self._r.set(key, 1, ex=ttl)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def touch(self, key: str, ttl: int) -> None:
        self._r.expire(key, ttl)

    def get(self, key: str) -> int:
        v = self._r.get(key)
        return int(v) if v else 0


class _FallbackStore:
    """Usa Redis y, si falla, la memoria del proceso (nunca deja entrar sin límite)."""

    def __init__(self, primary, fallback: _MemoryStore):
        self._p, self._f = primary, fallback

    def _call(self, name: str, *args):
        if self._p is not None:
            try:
                return getattr(self._p, name)(*args)
            except Exception as e:  # Redis caído o lento
                logger.warning("Límite de login: Redis no disponible (%s); uso memoria", type(e).__name__)
        return getattr(self._f, name)(*args)

    def incr(self, key, ttl): return self._call("incr", key, ttl)
    def ttl(self, key): return self._call("ttl", key)
    def set(self, key, ttl): return self._call("set", key, ttl)
    def delete(self, key): return self._call("delete", key)
    def touch(self, key, ttl): return self._call("touch", key, ttl)
    def get(self, key): return self._call("get", key)


class LoginThrottle:
    """Bloqueo progresivo por IP: 5 fallos en 15 min → 15 min, luego 30, 60… hasta 24 h."""

    def __init__(self, store):
        self.s = store

    def seconds_locked(self, ip: str) -> int:
        return self.s.ttl(f"panel:login:lock:{ip}")

    def register_failure(self, ip: str) -> int:
        """Anota un fallo. Devuelve los segundos de bloqueo recién impuestos (0 si no hay)."""
        self.s.incr("panel:login:gfail", GLOBAL_WINDOW_S)
        n = self.s.incr(f"panel:login:fail:{ip}", LOGIN_WINDOW_S)
        if n < LOGIN_MAX_FAILS:
            return 0
        strikes = self.s.incr(f"panel:login:strikes:{ip}", LOGIN_STRIKE_TTL_S)
        self.s.touch(f"panel:login:strikes:{ip}", LOGIN_STRIKE_TTL_S)  # ventana deslizante
        lock = min(LOGIN_BASE_LOCK_S * (2 ** (strikes - 1)), LOGIN_MAX_LOCK_S)
        self.s.set(f"panel:login:lock:{ip}", lock)
        self.s.delete(f"panel:login:fail:{ip}")
        logger.warning("Login del panel bloqueado %ss para una IP (reincidencia %s)", lock, strikes)
        return lock

    def register_success(self, ip: str) -> None:
        self.s.delete(f"panel:login:fail:{ip}")

    def global_delay(self) -> float:
        """Demora por intento cuando hay muchos fallos globales (ataque distribuido)."""
        return GLOBAL_SLOWDOWN_S if self.s.get("panel:login:gfail") >= GLOBAL_SLOWDOWN_FAILS else 0.0


_throttle: Optional[LoginThrottle] = None


def get_throttle() -> LoginThrottle:
    global _throttle
    if _throttle is None:
        primary = None
        try:
            import redis
            from config import settings
            primary = _RedisStore(redis.from_url(
                settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2, decode_responses=True))
        except Exception as e:
            logger.warning("Límite de login sin Redis (%s); uso memoria", type(e).__name__)
        _throttle = LoginThrottle(_FallbackStore(primary, _MemoryStore()))
    return _throttle


async def apply_global_delay(throttle: LoginThrottle) -> None:
    d = throttle.global_delay()
    if d:
        await asyncio.sleep(d)


# ═════════════════════════════════════════════════════════════════════════════
# Montaje en la aplicación
# ═════════════════════════════════════════════════════════════════════════════

def register_security(app: FastAPI) -> None:
    """Añade las cabeceras de seguridad y el manejador del error CSRF."""

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        secure = cookie_secure()
        for k, v in security_headers(request.url.path, secure).items():
            response.headers.setdefault(k, v)
        return response

    app.add_exception_handler(CSRFError, csrf_error_handler)
