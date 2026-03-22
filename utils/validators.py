from __future__ import annotations

import re
import html
from typing import Optional

from utils.helpers import parse_telegram_ids


def is_admin(telegram_id: int, admin_ids_str: str) -> bool:
    """Check if a telegram_id is in the admin list."""
    admin_ids = parse_telegram_ids(admin_ids_str)
    return int(telegram_id) in admin_ids


def validate_phone_ve(phone: str) -> bool:
    """
    Validate Venezuelan phone number.
    Valid formats: 04XX-XXXXXXX, 0424XXXXXXX, +584XXXXXXXXX
    """
    if not phone:
        return False
    # Remove spaces and dashes
    cleaned = re.sub(r"[\s\-]", "", phone)
    # Match Venezuelan formats
    patterns = [
        r"^(\+58|0058)?4(1[246]|2[46])\d{7}$",  # +58 or 0058 prefix
        r"^0?(4(1[246]|2[46]))\d{7}$",            # 04XX format
    ]
    for pattern in patterns:
        if re.match(pattern, cleaned):
            return True
    return False


def validate_cedula_ve(cedula: str) -> bool:
    """
    Validate Venezuelan cedula number.
    Valid formats: V-12345678, E-12345678, 12345678
    """
    if not cedula:
        return False
    # Remove spaces and dashes
    cleaned = re.sub(r"[\s\-]", "", cedula.upper())
    # Match format: optional V or E prefix + 6-8 digits
    pattern = r"^[VEve]?\d{6,8}$"
    return bool(re.match(pattern, cleaned))


def sanitize_text(text: str) -> str:
    """
    Sanitize user input text.
    - Strip leading/trailing whitespace
    - Escape HTML special characters
    - Remove control characters
    - Limit length to 1000 characters
    """
    if not text:
        return ""
    # Strip whitespace
    text = text.strip()
    # Remove control characters (except newlines and tabs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Limit length
    text = text[:1000]
    return text


def validate_payment_reference(reference: str) -> bool:
    """Validate payment reference format (digits, 8-20 chars)."""
    if not reference:
        return False
    cleaned = re.sub(r"[\s\-]", "", reference)
    return bool(re.match(r"^\d{8,20}$", cleaned))


def validate_amount_bs(amount_str: str) -> Optional[float]:
    """
    Parse and validate a Bolivar amount string.
    Returns float or None if invalid.
    """
    if not amount_str:
        return None
    # Remove Bs prefix, spaces, and dots used as thousands separators
    cleaned = re.sub(r"[Bb][Ss]\.?\s*", "", amount_str).strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        value = float(cleaned)
        if value <= 0:
            return None
        return round(value, 2)
    except (ValueError, TypeError):
        return None
