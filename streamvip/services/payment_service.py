from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import redis

from config import settings
from database import get_supabase
from database.subscriptions import check_payment_reference_exists
from services.gemini_service import validate_payment_image
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)

PAYMENT_HASH_TTL = 86400  # 24 hours


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _compute_image_hash(image_bytes: bytes) -> str:
    """Compute SHA256 hash of image bytes for anti-fraud checks."""
    return hashlib.sha256(image_bytes).hexdigest()


async def _check_image_hash_duplicate(image_hash: str) -> bool:
    """Check if image hash was used recently (24h)."""
    try:
        r = _get_redis()
        key = f"payment_img:{image_hash}"
        return r.exists(key) > 0
    except Exception:
        return False


async def _store_image_hash(image_hash: str) -> None:
    """Store image hash in Redis to detect duplicates."""
    try:
        r = _get_redis()
        key = f"payment_img:{image_hash}"
        r.setex(key, PAYMENT_HASH_TTL, "1")
    except Exception as e:
        logger.warning(f"Could not store payment image hash: {e}")


def _parse_payment_datetime(fecha: str, hora: Optional[str]) -> Optional[datetime]:
    """Parse Venezuelan date/time from comprobante."""
    if not fecha:
        return None
    try:
        date_formats = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
        parsed_date = None
        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(fecha.strip(), fmt)
                break
            except ValueError:
                continue

        if not parsed_date:
            return None

        if hora:
            try:
                time_obj = datetime.strptime(hora.strip(), "%H:%M")
                return parsed_date.replace(hour=time_obj.hour, minute=time_obj.minute)
            except ValueError:
                pass

        return parsed_date
    except Exception:
        return None


async def validate_payment(
    image_bytes: bytes,
    expected_amount_bs: float,
    subscription_id: str,
) -> dict:
    """
    Validate a payment comprobante image.

    Returns:
        {
            "valid": True/False/None,
            "data": {...extracted data...},
            "reason": "explanation"
        }
    """
    result_data = {}

    # 1. Anti-fraud: check image hash
    image_hash = _compute_image_hash(image_bytes)
    if await _check_image_hash_duplicate(image_hash):
        return {
            "valid": False,
            "data": {},
            "reason": "duplicate_image",
            "message": "Este comprobante ya fue utilizado anteriormente.",
        }

    # 2. Use Gemini Vision to extract payment data
    try:
        extracted = await validate_payment_image(image_bytes)
    except Exception as e:
        logger.error(f"Gemini vision error: {e}")
        return {
            "valid": None,
            "data": {},
            "reason": "vision_error",
            "message": "No se pudo analizar la imagen. Por favor envía una imagen más clara.",
        }

    if not extracted.get("is_comprobante_valido"):
        return {
            "valid": False,
            "data": extracted,
            "reason": "not_comprobante",
            "message": "La imagen no parece ser un comprobante de pago válido.",
        }

    result_data = extracted

    # 3. Validate amount (within 0.50 Bs tolerance)
    try:
        extracted_amount_str = str(extracted.get("monto", "0")).replace(",", ".")
        extracted_amount = float(extracted_amount_str)
        tolerance = 0.50
        if abs(extracted_amount - expected_amount_bs) > tolerance:
            return {
                "valid": False,
                "data": extracted,
                "reason": "amount_mismatch",
                "message": (
                    f"El monto del comprobante (Bs {extracted_amount:.2f}) no coincide "
                    f"con el monto esperado (Bs {expected_amount_bs:.2f})."
                ),
            }
    except (ValueError, TypeError):
        logger.warning("Could not parse amount from payment image")
        # Allow to proceed with manual review
        pass

    # 4. Check for duplicate reference
    reference = extracted.get("referencia", "")
    if reference and await check_payment_reference_exists(reference):
        return {
            "valid": False,
            "data": extracted,
            "reason": "duplicate_reference",
            "message": f"El número de referencia {reference} ya fue registrado.",
        }

    # 5. Validate date is within 60 minutes
    fecha = extracted.get("fecha", "")
    hora = extracted.get("hora")
    if fecha:
        payment_dt = _parse_payment_datetime(fecha, hora)
        if payment_dt:
            now = venezuela_now()
            # Make naive comparison
            if payment_dt.tzinfo is None:
                now_naive = now.replace(tzinfo=None)
            else:
                now_naive = now
            time_diff = abs((now_naive - payment_dt).total_seconds())
            if time_diff > 3600:  # 60 minutes
                return {
                    "valid": False,
                    "data": extracted,
                    "reason": "payment_too_old",
                    "message": "El comprobante tiene más de 60 minutos. Por favor realiza el pago nuevamente.",
                }

    # 6. All checks passed - store image hash
    await _store_image_hash(image_hash)

    return {
        "valid": True,
        "data": extracted,
        "reason": "ok",
        "reference": reference,
        "message": "Pago verificado correctamente.",
    }


async def get_payment_config() -> Optional[dict]:
    """Get active payment configuration (bank details)."""
    try:
        sb = get_supabase()
        result = (
            sb.table("payment_config")
            .select("*")
            .eq("is_active", True)
            
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting payment config: {e}")
        return None
