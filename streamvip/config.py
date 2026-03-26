from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


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
    WEBHOOK_SECRET: str = "streamvip_secret"
    DEBUG: bool = False
    SECRET_KEY: str = "streamvip_admin_secret_key_2025"
    ADMIN_PANEL_PASSWORD: str = "streamvip2025"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
