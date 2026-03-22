from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def get_or_create_user(telegram_id: int, username: Optional[str], name: Optional[str]) -> dict:
    """Upsert user by telegram_id. Returns user dict."""
    try:
        sb = get_supabase()
        # Try to get existing user
        result = sb.table("users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
        existing = result.data[0] if result.data else None
        if existing:
            # Update last_seen and username if changed
            update_data: dict = {"last_seen": venezuela_now().isoformat()}
            if username and existing.get("username") != username:
                update_data["username"] = username
            sb.table("users").update(update_data).eq("telegram_id", telegram_id).execute()
            return {**existing, **update_data}
        # Create new user
        new_user = {
            "telegram_id": telegram_id,
            "username": username,
            "name": name,
            "last_seen": venezuela_now().isoformat(),
            "status": "active",
            "total_purchases": 0,
            "receives_promos": True,
            "is_admin": False,
        }
        create_result = sb.table("users").insert(new_user).execute()
        return create_result.data[0] if create_result.data else new_user
    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        raise


async def get_user_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """Fetch user by telegram_id."""
    try:
        sb = get_supabase()
        result = sb.table("users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_user_by_telegram_id: {e}")
        return None


async def update_user_name(telegram_id: int, name: str) -> bool:
    """Update user's display name."""
    try:
        sb = get_supabase()
        sb.table("users").update({"name": name}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_user_name: {e}")
        return False


async def update_user_last_seen(telegram_id: int) -> bool:
    """Update user last_seen timestamp."""
    try:
        sb = get_supabase()
        sb.table("users").update({"last_seen": venezuela_now().isoformat()}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_user_last_seen: {e}")
        return False


async def block_user(telegram_id: int) -> bool:
    """Set user status to blocked."""
    try:
        sb = get_supabase()
        sb.table("users").update({"status": "blocked"}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in block_user: {e}")
        return False


async def unblock_user(telegram_id: int) -> bool:
    """Set user status to active."""
    try:
        sb = get_supabase()
        sb.table("users").update({"status": "active"}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in unblock_user: {e}")
        return False


async def get_all_active_users() -> list[dict]:
    """List all active users."""
    try:
        sb = get_supabase()
        result = sb.table("users").select("*").eq("status", "active").execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_all_active_users: {e}")
        return []


async def get_users_by_criteria(criteria: dict) -> list[dict]:
    """
    Filter users for campaigns.
    criteria can have: receives_promos, preferred_platform, status
    """
    try:
        sb = get_supabase()
        query = sb.table("users").select("*")
        for key, value in criteria.items():
            query = query.eq(key, value)
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_users_by_criteria: {e}")
        return []


async def update_user_phone(telegram_id: int, phone: str) -> bool:
    """Update user's phone number."""
    try:
        sb = get_supabase()
        sb.table("users").update({"phone": phone}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_user_phone: {e}")
        return False


async def increment_user_purchases(telegram_id: int) -> bool:
    """Increment total_purchases counter for a user."""
    try:
        sb = get_supabase()
        user = await get_user_by_telegram_id(telegram_id)
        if user:
            new_count = (user.get("total_purchases") or 0) + 1
            sb.table("users").update({"total_purchases": new_count}).eq("telegram_id", telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in increment_user_purchases: {e}")
        return False


async def log_admin_action(admin_telegram_id: int, action: str, details: dict) -> bool:
    """Log an admin action to admin_log table."""
    try:
        sb = get_supabase()
        sb.table("admin_log").insert({
            "admin_telegram_id": admin_telegram_id,
            "action": action,
            "details": details,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Error in log_admin_action: {e}")
        return False
