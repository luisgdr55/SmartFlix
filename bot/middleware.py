from __future__ import annotations

import logging
from typing import Optional

import redis

from config import settings
from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW = 60   # seconds
RATE_LIMIT_MAX = 30      # messages per window


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


async def check_user_blocked(telegram_id: int) -> bool:
    """Return True if user is blocked."""
    try:
        sb = get_supabase()
        result = (
            sb.table("users")
            .select("status")
            .eq("telegram_id", telegram_id)
            .maybe_single()
            .execute()
        )
        if result.data:
            return result.data.get("status") == "blocked"
        return False
    except Exception as e:
        logger.error(f"Error checking user blocked status: {e}")
        return False


async def rate_limit_check(telegram_id: int) -> bool:
    """
    Check if user has exceeded rate limit.
    Returns True if rate limited (should block), False if OK.
    Uses Redis sliding window counter: 30 msg/min.
    """
    try:
        r = _get_redis()
        key = f"rl:{telegram_id}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_WINDOW)
        results = pipe.execute()
        count = results[0]
        if count > RATE_LIMIT_MAX:
            logger.warning(f"Rate limited user {telegram_id}: {count} messages in {RATE_LIMIT_WINDOW}s")
            return True
        return False
    except Exception as e:
        logger.error(f"Error in rate_limit_check: {e}")
        return False  # Don't block on Redis error


async def log_interaction(telegram_id: int, update_type: str, data: Optional[dict] = None) -> None:
    """Log user interaction to database."""
    try:
        sb = get_supabase()
        sb.table("admin_log").insert({
            "admin_telegram_id": telegram_id,
            "action": f"user_interaction:{update_type}",
            "details": data or {},
        }).execute()
    except Exception as e:
        logger.debug(f"Error logging interaction: {e}")


def get_user_state(telegram_id: int) -> Optional[str]:
    """Get current conversation state for user from Redis."""
    try:
        r = _get_redis()
        return r.get(f"state:{telegram_id}")
    except Exception:
        return None


def set_user_state(telegram_id: int, state: str, ttl: int = 1800) -> None:
    """Set conversation state for user in Redis."""
    try:
        r = _get_redis()
        r.setex(f"state:{telegram_id}", ttl, state)
    except Exception as e:
        logger.error(f"Error setting user state: {e}")


def clear_user_state(telegram_id: int) -> None:
    """Clear conversation state for user."""
    try:
        r = _get_redis()
        r.delete(f"state:{telegram_id}")
    except Exception as e:
        logger.error(f"Error clearing user state: {e}")


def get_user_data(telegram_id: int, key: str) -> Optional[str]:
    """Get a specific data value for user from Redis."""
    try:
        r = _get_redis()
        return r.get(f"data:{telegram_id}:{key}")
    except Exception:
        return None


def set_user_data(telegram_id: int, key: str, value: str, ttl: int = 1800) -> None:
    """Store a data value for user in Redis."""
    try:
        r = _get_redis()
        r.setex(f"data:{telegram_id}:{key}", ttl, value)
    except Exception as e:
        logger.error(f"Error setting user data: {e}")


def clear_user_data(telegram_id: int) -> None:
    """Clear all data for user from Redis."""
    try:
        r = _get_redis()
        # Use scan to find all keys for this user
        cursor = 0
        keys_to_delete = []
        while True:
            cursor, keys = r.scan(cursor, match=f"data:{telegram_id}:*", count=50)
            keys_to_delete.extend(keys)
            if cursor == 0:
                break
        if keys_to_delete:
            r.delete(*keys_to_delete)
    except Exception as e:
        logger.error(f"Error clearing user data: {e}")
