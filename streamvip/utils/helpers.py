from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytz

VENEZUELA_TZ = pytz.timezone("America/Caracas")


def short_id(uuid_str: str) -> str:
    """Return first 8 characters of a UUID string."""
    if not uuid_str:
        return ""
    return str(uuid_str).replace("-", "")[:8].upper()


def format_date_vzla(dt: Optional[datetime]) -> str:
    """Format datetime for Venezuela locale as DD/MM/YYYY."""
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    dt_vzla = dt.astimezone(VENEZUELA_TZ)
    return dt_vzla.strftime("%d/%m/%Y")


def format_datetime_vzla(dt: Optional[datetime]) -> str:
    """Format datetime for Venezuela locale as DD/MM/YYYY HH:MM."""
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    dt_vzla = dt.astimezone(VENEZUELA_TZ)
    return dt_vzla.strftime("%d/%m/%Y %H:%M")


def days_remaining(end_date: Optional[datetime]) -> int:
    """Return number of days until expiry. Negative if already expired."""
    if end_date is None:
        return 0
    now = venezuela_now()
    if end_date.tzinfo is None:
        end_date = pytz.utc.localize(end_date)
    end_date_vzla = end_date.astimezone(VENEZUELA_TZ)
    delta = end_date_vzla - now
    return delta.days


def venezuela_now() -> datetime:
    """Return current time in Venezuela timezone (UTC-4)."""
    return datetime.now(VENEZUELA_TZ)


def mask_email(email: str) -> str:
    """Mask email address for display. E.g. us****@gmail.com"""
    if not email or "@" not in email:
        return "****"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        visible = max(2, len(local) // 3)
        masked_local = local[:visible] + "*" * (len(local) - visible)
    return f"{masked_local}@{domain}"


def format_price_usd(amount: float) -> str:
    """Format USD price with 2 decimal places."""
    return f"${amount:.2f}"


def format_price_bs(amount: float) -> str:
    """Format Bs price with 2 decimal places."""
    return f"Bs {amount:,.2f}"


def truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def parse_telegram_ids(ids_str: str) -> list[int]:
    """Parse comma-separated telegram IDs string to list of ints."""
    if not ids_str:
        return []
    result = []
    for part in ids_str.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result
