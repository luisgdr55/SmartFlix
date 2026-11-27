from __future__ import annotations

import hashlib
import re

from pydantic import field_validator
from pydantic_settings import BaseSettings

# Huellas SHA-256 de los valores por defecto que traían versiones anteriores de este
# archivo. Esos valores son públicos (historial de git), así que se rechazan sin
# volver a escribirlos aquí.
_PUBLIC_DEFAULT_DIGESTS = frozenset({
    "55516397a616612d7d1a10a5e2c752d13f87e5709844bae2f3252336043af5e5",
    "7f44ae5bfe8089f66e08da11c1a526a46e014007ec9df7df8073dffba13f704d",
    "bcb9cf80ae00f69642dfdc22484e48fb581f449029050cbf6c7a9c47b7b99639",
})
_REQUIRED_SECRETS = ("WEBHOOK_SECRET", "SECRET_KEY", "ADMIN_PANEL_PASSWORD")


def is_public_default(value: str) -> bool:
    """Compara la huella del valor tal cual y normalizado (sin espacios ni mayúsculas)."""
    for candidate in (value, value.strip(), value.strip().lower()):
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest() in _PUBLIC_DEFAULT_DIGESTS:
            return True
    return False


# Telegram solo admite estos caracteres (1 a 256) en el `secret_token` del webhook.
_WEBHOOK_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")
MIN_RECOMMENDED_SECRET_LEN = 16


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    ADMIN_TELEGRAM_IDS: str = ""
    SUPABASE_URL: str
    SUPABASE_KEY: str
    REDIS_URL: str
    GEMINI_API_KEY: str
    TMDB_API_KEY: str = ""
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    GMAIL_REDIRECT_URI: str = ""
    # Central inbox for verification code forwarding (IMAP)
    IMAP_EMAIL: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_HOST: str = "imap.gmail.com"
    IMAP_PORT: int = 993
    APP_URL: str
    WEBHOOK_SECRET: str
    DEBUG: bool = False
    SECRET_KEY: str
    ADMIN_PANEL_PASSWORD: str
    # Proxies de confianza delante de la app (Railway = 1): decide qué entrada de
    # X-Forwarded-For es la IP real del cliente para el límite de intentos de login.
    TRUSTED_PROXY_HOPS: int = 1

    @field_validator(*_REQUIRED_SECRETS)
    @classmethod
    def _secret_must_be_own(cls, v: str) -> str:
        """Sin valor por defecto: vacío o público = el servicio no arranca."""
        if not v or not v.strip():
            raise ValueError("no puede estar vacío")
        if is_public_default(v):
            raise ValueError("es un valor por defecto público; define uno propio")
        return v

    @field_validator("WEBHOOK_SECRET")
    @classmethod
    def _webhook_secret_format(cls, v: str) -> str:
        if not _WEBHOOK_SECRET_RE.fullmatch(v):
            raise ValueError("solo admite letras, números, «_» y «-» (1 a 256 caracteres): Telegram rechaza otros")
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True
        hide_input_in_errors = True  # que el error de arranque no imprima valores


settings = Settings()
