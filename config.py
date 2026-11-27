from __future__ import annotations

import hashlib

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
    return hashlib.sha256(value.encode("utf-8")).hexdigest() in _PUBLIC_DEFAULT_DIGESTS


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

    @field_validator(*_REQUIRED_SECRETS)
    @classmethod
    def _secret_must_be_own(cls, v: str) -> str:
        """Sin valor por defecto: vacío o público = el servicio no arranca."""
        if not v or not v.strip():
            raise ValueError("no puede estar vacío")
        if is_public_default(v):
            raise ValueError("es un valor por defecto público; define uno propio")
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True
        hide_input_in_errors = True  # que el error de arranque no imprima valores


settings = Settings()
