from __future__ import annotations

import logging
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def create_verification_request(
    user_id: str,
    client_telegram_id: int,
    subscription_id: str,
    platform_name: str,
    platform_emoji: str,
    platform_slug: str,
    client_name: str = "",
) -> Optional[dict]:
    """Create a pending verification code request."""
    try:
        sb = get_supabase()
        result = (
            sb.table("verification_requests")
            .insert({
                "user_id": user_id,
                "client_telegram_id": client_telegram_id,
                "subscription_id": subscription_id,
                "platform_name": platform_name,
                "platform_emoji": platform_emoji,
                "platform_slug": platform_slug,
                "client_name": client_name,
                "status": "pending",
            })
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error creating verification request: {e}")
        return None


async def get_verification_request(request_id: str) -> Optional[dict]:
    """Get a verification request by ID."""
    try:
        sb = get_supabase()
        result = (
            sb.table("verification_requests")
            .select("*")
            .eq("id", request_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting verification request: {e}")
        return None


async def mark_request_sent(request_id: str, code: str) -> bool:
    """Mark a request as fulfilled (code sent to client)."""
    try:
        sb = get_supabase()
        sb.table("verification_requests").update({
            "status": "sent",
            "code": code,
            "sent_at": venezuela_now().isoformat(),
        }).eq("id", request_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error marking request sent: {e}")
        return False


async def mark_request_pending_admin(request_id: str) -> bool:
    """Mark a request as needing admin to send code manually."""
    try:
        sb = get_supabase()
        sb.table("verification_requests").update({
            "status": "pending_admin",
        }).eq("id", request_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error marking request pending_admin: {e}")
        return False


async def cancel_request(request_id: str) -> bool:
    """Cancel a verification request."""
    try:
        sb = get_supabase()
        sb.table("verification_requests").update({
            "status": "cancelled",
        }).eq("id", request_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error cancelling verification request: {e}")
        return False
