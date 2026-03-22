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
    """List available profiles for a platform and type."""
    try:
        sb = get_supabase()
        result = (
            sb.table("profiles")
            .select("*")
            .eq("platform_id", platform_id)
            .eq("profile_type", profile_type)
            .eq("status", "available")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_available_profiles: {e}")
        return []


async def assign_profile(profile_id: str) -> bool:
    """Mark profile as occupied."""
    try:
        sb = get_supabase()
        sb.table("profiles").update({"status": "occupied"}).eq("id", profile_id).execute()
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


async def count_available_profiles(platform_id: str, profile_type: str = "monthly") -> int:
    """Count available profiles for a platform type."""
    try:
        profiles = await get_available_profiles(platform_id, profile_type)
        return len(profiles)
    except Exception as e:
        logger.error(f"Error in count_available_profiles: {e}")
        return 0


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
        result = sb.table("profiles").select("*").eq("id", profile_id).maybe_single().execute()
        return result.data
    except Exception as e:
        logger.error(f"Error in get_profile_by_id: {e}")
        return None
