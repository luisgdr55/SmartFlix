from __future__ import annotations

import logging
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def create_profile(account_id: str, platform_id: str, name: str, pin: Optional[str], profile_type: str = "monthly") -> Optional[dict]:
    """Create a new profile."""
    try:
        sb = get_supabase()
        result = sb.table("profiles").insert({
            "account_id": account_id,
            "platform_id": platform_id,
            "profile_name": name,
            "pin": pin,
            "profile_type": profile_type,
            "status": "available",
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in create_profile: {e}")
        return None


async def get_available_profiles(platform_id: str, profile_type: str = "monthly") -> list[dict]:
    """List truly available profiles: status=available OR (status=reserved AND TTL expired)."""
    try:
        sb = get_supabase()
        now_iso = venezuela_now().isoformat()
        result = (
            sb.table("profiles")
            .select("*")
            .eq("platform_id", platform_id)
            .eq("profile_type", profile_type)
            .or_(f"status.eq.available,and(status.eq.reserved,reserved_until.lt.{now_iso})")
            .order("last_released", desc=False, nullsfirst=True)
            .order("created_at", desc=False)
            .execute()
        )
        profiles = result.data or []
        logger.debug(
            f"get_available_profiles(platform={platform_id}, type={profile_type}) → {len(profiles)} results"
        )
        return profiles
    except Exception as e:
        logger.error(f"Error in get_available_profiles: {e}")
        return []


async def get_all_profiles_for_platform(platform_id: str) -> list[dict]:
    """List ALL profiles for a platform regardless of status/type (for diagnostics)."""
    try:
        sb = get_supabase()
        result = (
            sb.table("profiles")
            .select("id, profile_name, profile_type, status, is_extra_member")
            .eq("platform_id", platform_id)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_all_profiles_for_platform: {e}")
        return []


async def assign_profile(profile_id: str) -> bool:
    """Mark profile as occupied, clearing any reservation."""
    try:
        sb = get_supabase()
        sb.table("profiles").update({
            "status": "occupied",
            "reserved_for": None,
            "reserved_until": None,
        }).eq("id", profile_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in assign_profile: {e}")
        return False


async def release_profile(profile_id: str) -> bool:
    """Mark profile as available again."""
    try:
        sb = get_supabase()
        sb.table("profiles").update({
            "status": "available",
            "last_released": venezuela_now().isoformat(),
        }).eq("id", profile_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in release_profile: {e}")
        return False


async def update_profile_pin(profile_id: str, new_pin: str) -> bool:
    """Update profile PIN."""
    try:
        sb = get_supabase()
        sb.table("profiles").update({"pin": new_pin}).eq("id", profile_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_profile_pin: {e}")
        return False


async def get_profile_by_subscription(subscription_id: str) -> Optional[dict]:
    """Retorna el perfil asociado a una suscripción activa."""
    try:
        result = get_supabase().table('subscriptions').select(
            'profile_id, profiles!inner(id, profile_name, pin, status, account_id,'
            '  accounts!inner(id, email, account_health))'
        ).eq('id', str(subscription_id)).execute()
        if result.data:
            return result.data[0].get('profiles')
        return None
    except Exception as e:
        logger.error(f"[profiles] get_profile_by_subscription: {e}")
        return None


async def count_available_profiles(platform_id: str, profile_type: str = "monthly") -> int:
    """Count available profiles for a platform type."""
    try:
        profiles = await get_available_profiles(platform_id, profile_type)
        return len(profiles)
    except Exception as e:
        logger.error(f"Error in count_available_profiles: {e}")
        return 0


async def get_available_profile_counts() -> dict[tuple[str, str], int]:
    """Return {(platform_id, profile_type): count} for all platforms in one query."""
    try:
        sb = get_supabase()
        now_iso = venezuela_now().isoformat()
        result = (
            sb.table("profiles")
            .select("platform_id, profile_type")
            .or_(f"status.eq.available,and(status.eq.reserved,reserved_until.lt.{now_iso})")
            .execute()
        )
        counts: dict[tuple[str, str], int] = {}
        for row in (result.data or []):
            key = (row["platform_id"], row["profile_type"])
            counts[key] = counts.get(key, 0) + 1
        return counts
    except Exception as e:
        logger.error(f"Error in get_available_profile_counts: {e}")
        return {}


async def get_profiles_by_account(account_id: str) -> list[dict]:
    """List all profiles for an account."""
    try:
        sb = get_supabase()
        result = sb.table("profiles").select("*").eq("account_id", account_id).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_profiles_by_account: {e}")
        return []


async def get_profile_by_id(profile_id: str) -> Optional[dict]:
    """Get profile by UUID."""
    try:
        sb = get_supabase()
        result = sb.table("profiles").select("*").eq("id", profile_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_profile_by_id: {e}")
        return None


async def reserve_profile(profile_id: str, user_id: str, ttl_minutes: int = 30) -> bool:
    """Reserve a profile for a user for TTL minutes while payment is pending."""
    try:
        from datetime import timedelta
        sb = get_supabase()
        reserved_until = (venezuela_now() + timedelta(minutes=ttl_minutes)).isoformat()
        sb.table("profiles").update({
            "status": "reserved",
            "reserved_for": user_id,
            "reserved_until": reserved_until,
        }).eq("id", profile_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in reserve_profile: {e}")
        return False


async def release_expired_reservations() -> int:
    """Release all profiles whose reservation TTL has expired. Returns count released."""
    try:
        sb = get_supabase()
        now_iso = venezuela_now().isoformat()
        res = sb.table("profiles").update({
            "status": "available",
            "reserved_for": None,
            "reserved_until": None,
        }).eq("status", "reserved").lt("reserved_until", now_iso).execute()
        count = len(res.data or [])
        if count:
            logger.info(f"release_expired_reservations: released {count} profile(s)")
        return count
    except Exception as e:
        logger.error(f"Error in release_expired_reservations: {e}")
        return 0
