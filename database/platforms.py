from __future__ import annotations

import logging
from typing import Optional

from database import get_supabase

logger = logging.getLogger(__name__)


async def get_active_platforms() -> list[dict]:
    """List all active platforms."""
    try:
        sb = get_supabase()
        result = sb.table("platforms").select("*").eq("is_active", True).order("name").execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_active_platforms: {e}")
        return []


async def get_platform_by_slug(slug: str) -> Optional[dict]:
    """Get platform by slug."""
    try:
        sb = get_supabase()
        result = sb.table("platforms").select("*").eq("slug", slug).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_platform_by_slug: {e}")
        return None


async def get_platform_by_id(platform_id: str) -> Optional[dict]:
    """Get platform by UUID."""
    try:
        sb = get_supabase()
        result = sb.table("platforms").select("*").eq("id", platform_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_platform_by_id: {e}")
        return None


async def update_platform_prices(platform_id: str, monthly: float, express: float, week: float) -> bool:
    """Update platform pricing."""
    try:
        sb = get_supabase()
        sb.table("platforms").update({
            "monthly_price_usd": monthly,
            "express_price_usd": express,
            "week_price_usd": week,
        }).eq("id", platform_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_platform_prices: {e}")
        return False


async def toggle_platform_active(platform_id: str, is_active: bool) -> bool:
    """Toggle platform active status."""
    try:
        sb = get_supabase()
        sb.table("platforms").update({"is_active": is_active}).eq("id", platform_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in toggle_platform_active: {e}")
        return False
