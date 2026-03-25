"""Shopping cart via Redis for multi-service orders."""
from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)

CART_KEY = "cart:{}"
CART_TTL = 1800


def _get_redis():
    from services.gemini_service import _get_redis as _r
    return _r()


def get_cart(telegram_id: int) -> list[dict]:
    try:
        r = _get_redis()
        raw = r.get(CART_KEY.format(telegram_id))
        return json.loads(raw) if raw else []
    except Exception as e:
        logger.warning(f"get_cart error: {e}")
        return []


def save_cart(telegram_id: int, items: list[dict]) -> None:
    try:
        r = _get_redis()
        r.setex(CART_KEY.format(telegram_id), CART_TTL, json.dumps(items))
    except Exception as e:
        logger.warning(f"save_cart error: {e}")


def add_to_cart(telegram_id: int, item: dict) -> list[dict]:
    """Append one item to cart. Returns updated cart list."""
    cart = get_cart(telegram_id)
    cart.append(item)
    save_cart(telegram_id, cart)
    return cart


def clear_cart(telegram_id: int) -> None:
    try:
        r = _get_redis()
        r.delete(CART_KEY.format(telegram_id))
    except Exception as e:
        logger.warning(f"clear_cart error: {e}")
