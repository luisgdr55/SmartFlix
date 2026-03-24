from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def create_subscription(
    user_id: str,
    platform_id: str,
    plan_type: str,
    price_usd: float,
    price_bs: float,
    rate_used: float,
    end_date: datetime,
) -> Optional[dict]:
    """Create a new pending subscription."""
    try:
        sb = get_supabase()
        result = sb.table("subscriptions").insert({
            "user_id": user_id,
            "platform_id": platform_id,
            "plan_type": plan_type,
            "price_usd": price_usd,
            "price_bs": price_bs,
            "rate_used": rate_used,
            "end_date": end_date.isoformat(),
            "start_date": venezuela_now().isoformat(),
            "status": "pending_payment",
            "reminder_sent": False,
            "expiry_notified": False,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in create_subscription: {e}")
        return None


async def get_user_active_subscriptions(user_id: str) -> list[dict]:
    """List all active subscriptions for a user."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji), profiles(profile_name, pin)")
            .eq("user_id", user_id)
            .in_("status", ["active", "pending_payment"])
            .order("end_date", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_user_active_subscriptions: {e}")
        return []


async def get_user_platform_active_subscription(user_id: str, platform_id: str) -> Optional[dict]:
    """Find the most recent active/expired subscription for user+platform that has a profile assigned.
    Used to detect renewals at approval time."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, profiles(id, profile_name, pin, account_id)")
            .eq("user_id", user_id)
            .eq("platform_id", platform_id)
            .in_("status", ["active", "expired"])
            .not_.is_("profile_id", "null")
            .order("end_date", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_user_platform_active_subscription: {e}")
        return None


async def confirm_renewal_subscription(
    sub_id: str,
    profile_id: str,
    payment_reference: str,
    new_end_date: datetime,
) -> bool:
    """Confirm a renewal: mark as active with the existing profile and a new end_date."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({
            "status": "active",
            "profile_id": profile_id,
            "payment_reference": payment_reference,
            "payment_confirmed_at": venezuela_now().isoformat(),
            "end_date": new_end_date.isoformat(),
        }).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in confirm_renewal_subscription: {e}")
        return False


async def get_user_attention_subscriptions(user_id: str) -> dict:
    """Return subs that need user attention: unpaid debt or expired/vencidas.
    Covers all statuses (pending_payment, expired_payment, active-past-end, expired).
    Returns {'pending': [...], 'expired': [...]}
    """
    try:
        sb = get_supabase()
        now = venezuela_now()
        today = now.strftime("%Y-%m-%d")
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji)")
            .eq("user_id", user_id)
            .not_.in_("status", ["cancelled"])
            .execute()
        )
        subs = result.data or []
        pending = [s for s in subs if s.get("status") in ("pending_payment", "expired_payment")]
        expired = [
            s for s in subs
            if s.get("status") in ("active", "expired")
            and s.get("end_date")
            and s["end_date"][:10] <= today
        ]
        return {"pending": pending, "expired": expired}
    except Exception as e:
        logger.error(f"Error in get_user_attention_subscriptions: {e}")
        return {"pending": [], "expired": []}


async def get_user_pending_subscription(user_id: str) -> Optional[dict]:
    """Return the most recent pending_payment subscription for a user."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji)")
            .eq("user_id", user_id)
            .eq("status", "pending_payment")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_user_pending_subscription: {e}")
        return None


async def save_payment_proof(sub_id: str, payment_reference: str, payment_image_url: str) -> bool:
    """Save payment proof to a pending subscription (awaiting admin approval)."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({
            "payment_reference": payment_reference,
            "payment_image_url": payment_image_url,
        }).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in save_payment_proof: {e}")
        return False


async def confirm_subscription(
    sub_id: str,
    profile_id: str,
    payment_reference: str,
    payment_image_url: str,
) -> bool:
    """Confirm payment and assign profile to subscription."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({
            "status": "active",
            "profile_id": profile_id,
            "payment_reference": payment_reference,
            "payment_image_url": payment_image_url,
            "payment_confirmed_at": venezuela_now().isoformat(),
        }).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in confirm_subscription: {e}")
        return False


async def expire_subscription(sub_id: str) -> bool:
    """Mark subscription as expired."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"status": "expired"}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in expire_subscription: {e}")
        return False


async def cancel_subscription(sub_id: str) -> bool:
    """Mark subscription as cancelled."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"status": "cancelled"}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in cancel_subscription: {e}")
        return False


async def delete_subscription(sub_id: str) -> bool:
    """Permanently delete a subscription record."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").delete().eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in delete_subscription: {e}")
        return False


async def get_expiring_subscriptions(days_ahead: int = 3) -> list[dict]:
    """Get subscriptions expiring in the next N days (for reminders)."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        target_date = now + timedelta(days=days_ahead)
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji)")
            .eq("status", "active")
            .eq("reminder_sent", False)
            .lte("end_date", target_date.isoformat())
            .gte("end_date", now.isoformat())
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_expiring_subscriptions: {e}")
        return []


async def get_pending_subscriptions() -> list[dict]:
    """List all pending payment subscriptions."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name, username), platforms(name, slug, icon_emoji)")
            .eq("status", "pending_payment")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_pending_subscriptions: {e}")
        return []


async def get_expired_subscriptions_to_notify() -> list[dict]:
    """Get active subscriptions that have passed end_date but not yet notified."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(id)")
            .eq("status", "active")
            .eq("expiry_notified", False)
            .lte("end_date", now.isoformat())
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_expired_subscriptions_to_notify: {e}")
        return []


async def get_expired_express_subscriptions() -> list[dict]:
    """Get active express subscriptions that have expired (to release profiles)."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(id)")
            .eq("status", "active")
            .eq("plan_type", "express")
            .lte("end_date", now.isoformat())
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_expired_express_subscriptions: {e}")
        return []


async def check_payment_reference_exists(reference: str) -> bool:
    """Check if a payment reference has already been used (anti-duplicate)."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("payment_reference", reference)
            .execute()
        )
        return len(result.data or []) > 0
    except Exception as e:
        logger.error(f"Error in check_payment_reference_exists: {e}")
        return False


async def mark_reminder_sent(sub_id: str) -> bool:
    """Mark that 3-day reminder has been sent."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"reminder_sent": True}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in mark_reminder_sent: {e}")
        return False


async def mark_expiry_notified(sub_id: str) -> bool:
    """Mark that expiry notification has been sent."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"expiry_notified": True}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in mark_expiry_notified: {e}")
        return False


async def get_subscription_by_id(sub_id: str) -> Optional[dict]:
    """Get subscription by UUID with related data."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(profile_name, pin)")
            .eq("id", sub_id)
            
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_subscription_by_id: {e}")
        return None


async def get_expired_subscriptions(limit: int = 50) -> list[dict]:
    """
    Get subscriptions that are effectively expired:
      - status = 'expired'
      - status = 'active' but end_date is in the past
    Results ordered by end_date desc.
    """
    try:
        sb = get_supabase()
        now = venezuela_now()
        fields = "id, end_date, plan_type, price_usd, status, users(telegram_id, name, username), platforms(name, icon_emoji)"

        # Query 1: explicit expired status
        r1 = (
            sb.table("subscriptions")
            .select(fields)
            .eq("status", "expired")
            .order("end_date", desc=True)
            .limit(limit)
            .execute()
        )

        # Query 2: active subs whose end_date is already past
        r2 = (
            sb.table("subscriptions")
            .select(fields)
            .eq("status", "active")
            .lte("end_date", now.isoformat())
            .order("end_date", desc=True)
            .limit(limit)
            .execute()
        )

        # Merge, deduplicate by id
        seen_ids: set = set()
        merged = []
        for row in (r1.data or []) + (r2.data or []):
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                merged.append(row)

        # Sort by end_date descending
        merged.sort(key=lambda x: x.get("end_date") or "", reverse=True)
        return merged[:limit]
    except Exception as e:
        logger.error(f"Error in get_expired_subscriptions: {e}")
        return []


async def cancel_expired_pending_subscriptions() -> int:
    """Cancel pending subscriptions older than 45 minutes. Returns count of cancelled."""
    try:
        sb = get_supabase()
        cutoff = venezuela_now() - timedelta(minutes=45)
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("status", "pending_payment")
            .lte("created_at", cutoff.isoformat())
            .execute()
        )
        ids = [row["id"] for row in (result.data or [])]
        if ids:
            for sub_id in ids:
                sb.table("subscriptions").update({"status": "expired_payment"}).eq("id", sub_id).execute()
        return len(ids)
    except Exception as e:
        logger.error(f"Error in cancel_expired_pending_subscriptions: {e}")
        return 0
