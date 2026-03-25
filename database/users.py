from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def get_or_create_user(telegram_id: int, username: Optional[str], name: Optional[str]) -> dict:
    """Upsert user by telegram_id. If not found, checks by username (pre-registered clients).
    Returns user dict."""
    try:
        sb = get_supabase()
        now = venezuela_now().isoformat()

        # 1. Try by telegram_id
        result = sb.table("users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
        existing = result.data[0] if result.data else None
        if existing:
            update_data: dict = {"last_seen": now}
            if username and existing.get("username") != username:
                update_data["username"] = username
            sb.table("users").update(update_data).eq("telegram_id", telegram_id).execute()
            return {**existing, **update_data}

        # 2. Try by username — links pre-registered clients automatically
        if username:
            by_user = sb.table("users").select("*").eq("username", username).is_("telegram_id", "null").limit(1).execute()
            if by_user.data:
                pre = by_user.data[0]
                link_data = {"telegram_id": telegram_id, "last_seen": now, "username": username}
                sb.table("users").update(link_data).eq("id", pre["id"]).execute()
                logger.info(f"Linked pre-registered client @{username} → telegram_id {telegram_id}")
                return {**pre, **link_data}

        # 3. Create new user
        new_user = {
            "telegram_id": telegram_id,
            "username": username,
            "name": name,
            "last_seen": now,
            "status": "active",
            "total_purchases": 0,
            "receives_promos": True,
            "is_admin": False,
        }
        create_result = sb.table("users").insert(new_user).execute()
        created = create_result.data[0] if create_result.data else new_user
        return {**created, "_just_created": True}
    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        raise


async def find_user_by_phone(phone: str) -> Optional[dict]:
    """Find a pre-registered user by phone number (normalizes VE formats)."""
    try:
        sb = get_supabase()
        # Normalize: strip +58 prefix → 0XXX, keep only digits
        digits = "".join(c for c in phone if c.isdigit())
        variants = {phone}
        if digits.startswith("58") and len(digits) > 10:
            local = "0" + digits[2:]
            variants.add(local)
            variants.add("+" + digits)
        elif digits.startswith("0"):
            variants.add(digits)
            variants.add("+58" + digits[1:])
            variants.add("58" + digits[1:])

        for variant in variants:
            res = sb.table("users").select("*").eq("phone", variant).is_("telegram_id", "null").limit(1).execute()
            if res.data:
                return res.data[0]
        return None
    except Exception as e:
        logger.error(f"Error in find_user_by_phone: {e}")
        return None


async def link_user_telegram_id(user_id: str, telegram_id: int, username: Optional[str] = None) -> bool:
    """Link a pre-registered user to their telegram_id."""
    try:
        sb = get_supabase()
        upd: dict = {"telegram_id": telegram_id, "last_seen": venezuela_now().isoformat()}
        if username:
            upd["username"] = username
        sb.table("users").update(upd).eq("id", user_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in link_user_telegram_id: {e}")
        return False


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


async def delete_user(user_id: str) -> bool:
    """Permanently delete a user and all their subscriptions."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").delete().eq("user_id", user_id).execute()
        sb.table("users").delete().eq("id", user_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in delete_user: {e}")
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
