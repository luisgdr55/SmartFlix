from __future__ import annotations

import logging
from typing import Optional

from database import get_supabase

logger = logging.getLogger(__name__)


async def create_account(platform_id: str, email: str, password: str, billing_date: Optional[str] = None) -> Optional[dict]:
    """Create a new streaming account."""
    try:
        sb = get_supabase()
        data = {
            "platform_id": platform_id,
            "email": email,
            "password": password,
            "status": "active",
        }
        if billing_date:
            data["billing_date"] = billing_date
        result = sb.table("accounts").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in create_account: {e}")
        return None


async def get_accounts_by_platform(platform_id: str) -> list[dict]:
    """List all accounts for a platform."""
    try:
        sb = get_supabase()
        result = sb.table("accounts").select("*").eq("platform_id", platform_id).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_accounts_by_platform: {e}")
        return []


async def get_account_by_id(account_id: str) -> Optional[dict]:
    """Get account by UUID."""
    try:
        sb = get_supabase()
        result = sb.table("accounts").select("*").eq("id", account_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_account_by_id: {e}")
        return None


async def update_account_status(account_id: str, status: str) -> bool:
    """Update account status."""
    try:
        sb = get_supabase()
        sb.table("accounts").update({"status": status}).eq("id", account_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_account_status: {e}")
        return False


async def update_account_credentials(account_id: str, email: str, password: str) -> bool:
    """Update account email and password."""
    try:
        sb = get_supabase()
        sb.table("accounts").update({"email": email, "password": password}).eq("id", account_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in update_account_credentials: {e}")
        return False
