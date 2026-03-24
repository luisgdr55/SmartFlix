"""Session management for StreamVip Admin Panel using itsdangerous."""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner, SignatureExpired, BadSignature

logger = logging.getLogger(__name__)

COOKIE_NAME = "sv_admin_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24 hours in seconds
SESSION_VALUE = "authenticated"

_SECRET_KEY: Optional[str] = None


def _get_secret_key() -> str:
    global _SECRET_KEY
    if _SECRET_KEY is None:
        try:
            from config import settings
            key = getattr(settings, "SECRET_KEY", None) or os.environ.get("SECRET_KEY", "")
        except Exception:
            key = ""
        if not key:
            key = os.environ.get("SECRET_KEY", "streamvip_admin_secret_key_2025")
        _SECRET_KEY = key
    return _SECRET_KEY


def _get_signer() -> TimestampSigner:
    return TimestampSigner(_get_secret_key())


def _get_admin_password() -> str:
    try:
        from config import settings
        pwd = getattr(settings, "ADMIN_PANEL_PASSWORD", None)
        if pwd:
            return pwd
    except Exception:
        pass
    return os.environ.get("ADMIN_PANEL_PASSWORD", "streamvip2025")


def create_session(response: Response) -> None:
    """Signs a session token and sets it as a cookie."""
    signer = _get_signer()
    token = signer.sign(SESSION_VALUE).decode("utf-8")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def clear_session(response: Response) -> None:
    """Clears the admin session cookie."""
    response.delete_cookie(key=COOKIE_NAME)


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
    """Verify the submitted admin password."""
    return password == _get_admin_password()


async def require_auth(request: Request) -> Optional[bool]:
    """FastAPI dependency — redirects to /panel/login if not authenticated."""
    if not verify_session(request):
        raise _AuthRedirectException()
    return True


class _AuthRedirectException(Exception):
    """Internal exception used to trigger auth redirect."""
    pass
