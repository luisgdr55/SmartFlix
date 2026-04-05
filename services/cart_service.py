"""Shopping cart via Redis for multi-service orders."""
from __future__ import annotations
import json
import logging
import redis as _redis_lib

from config import settings

logger = logging.getLogger(__name__)

CART_KEY = "cart:{}"
RENEWAL_CART_KEY = "renewal_cart:{}"
CART_TTL = 1800

_cart_redis_client = None


def _get_redis():
    global _cart_redis_client
    if _cart_redis_client is None:
        _cart_redis_client = _redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=10,
        )
    return _cart_redis_client


def get_cart(telegram_id: int) -> list[dict]:
    try:
        r = _get_redis()
        raw = r.get(CART_KEY.format(telegram_id))
        return json.loads(raw) if raw else []
    except Exception as e:
        logger.error(f"get_cart error: {e}")
        return []


def save_cart(telegram_id: int, items: list[dict]) -> None:
    try:
        r = _get_redis()
        r.setex(CART_KEY.format(telegram_id), CART_TTL, json.dumps(items))
    except Exception as e:
        logger.error(f"save_cart error: {e}")


def add_to_cart(telegram_id: int, item: dict) -> list[dict]:
    """Append one item to cart. Returns updated cart list."""
    cart = get_cart(telegram_id)
    logger.error(f"add_to_cart BEFORE: tid={telegram_id} cart_len={len(cart)} cart={cart}")
    cart.append(item)
    save_cart(telegram_id, cart)
    after = get_cart(telegram_id)
    logger.error(f"add_to_cart AFTER: tid={telegram_id} cart_len={len(after)} after={after}")
    return cart


def clear_cart(telegram_id: int) -> None:
    try:
        r = _get_redis()
        r.delete(CART_KEY.format(telegram_id))
    except Exception as e:
        logger.error(f"clear_cart error: {e}")


def get_renewal_cart(telegram_id: int) -> dict:
    try:
        r = _get_redis()
        raw = r.get(RENEWAL_CART_KEY.format(telegram_id))
        return json.loads(raw) if raw else {}
    except Exception as e:
        logger.error(f"get_renewal_cart error: {e}")
        return {}


def save_renewal_cart(telegram_id: int, cart: dict) -> None:
    try:
        r = _get_redis()
        r.setex(RENEWAL_CART_KEY.format(telegram_id), CART_TTL, json.dumps(cart))
    except Exception as e:
        logger.error(f"save_renewal_cart error: {e}")


def clear_renewal_cart(telegram_id: int) -> None:
    try:
        r = _get_redis()
        r.delete(RENEWAL_CART_KEY.format(telegram_id))
    except Exception as e:
        logger.error(f"clear_renewal_cart error: {e}")
