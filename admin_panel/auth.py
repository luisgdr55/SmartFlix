"""Session management for SmartFlixVE Admin Panel using itsdangerous."""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner, SignatureExpired, BadSignature

logger = logging.getLogger(__name__)

COOKIE_NAME = "sv_admin_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24 hours in seconds
SESSION_VALUE = "authenticated"

def get_secret_key() -> str:
    """Clave para firmar la cookie y derivar el token CSRF.

    No hay valor por defecto: ``config.Settings`` exige ``SECRET_KEY`` al arrancar.
    """
    from config import settings
    key = settings.SECRET_KEY
    if not key:
        raise RuntimeError("SECRET_KEY no está definida")
    return key


def _get_signer() -> TimestampSigner:
    return TimestampSigner(get_secret_key())


def _get_admin_password() -> str:
    """Contraseña del panel. Sin valor por defecto: ``config.Settings`` la exige."""
    from config import settings
    pwd = settings.ADMIN_PANEL_PASSWORD
    if not pwd:
        raise RuntimeError("ADMIN_PANEL_PASSWORD no está definida")
    return pwd


def _cookie_flags() -> dict:
    from admin_panel.security import cookie_secure
    return {"httponly": True, "samesite": "lax", "secure": cookie_secure()}


def create_session(response: Response) -> None:
    """Signs a session token and sets it as a cookie."""
    signer = _get_signer()
    token = signer.sign(SESSION_VALUE).decode("utf-8")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        **_cookie_flags(),
    )


def clear_session(response: Response) -> None:
    """Clears the admin session cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/", **_cookie_flags())


def verify_session(request: Request) -> bool:
    """Returns True if the session cookie is valid and not expired."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        signer = _get_signer()
        signer.unsign(token, max_age=COOKIE_MAX_AGE)
        return True
    except SignatureExpired:
        logger.debug("Admin session expired")
        return False
    except BadSignature:
        logger.debug("Admin session bad signature")
        return False
    except Exception as e:
        logger.error(f"Error verifying session: {e}")
        return False


def verify_password(password: str) -> bool:
    """Verify the submitted admin password (comparación en tiempo constante)."""
    return hmac.compare_digest(password.encode("utf-8"), _get_admin_password().encode("utf-8"))


async def require_auth(request: Request) -> Optional[bool]:
    """FastAPI dependency — redirects to /panel/login if not authenticated."""
    if not verify_session(request):
        raise _AuthRedirectException()
    return True


class _AuthRedirectException(Exception):
    """Internal exception used to trigger auth redirect."""
    pass
