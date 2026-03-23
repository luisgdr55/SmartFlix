from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def get_dashboard_stats() -> dict:
    """Get all stats for the /admin dashboard."""
    try:
        sb = get_supabase()
        now = venezuela_now()

        # Total users
        users_result = sb.table("users").select("id", count="exact").execute()
        total_users = users_result.count or 0

        # Active subscriptions
        active_result = sb.table("subscriptions").select("id", count="exact").eq("status", "active").execute()
        active_subs = active_result.count or 0

        # Pending payments
        pending_result = sb.table("subscriptions").select("id", count="exact").eq("status", "pending_payment").execute()
        pending_payments = pending_result.count or 0

        # Revenue this month
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        revenue_result = (
            sb.table("subscriptions")
            .select("price_usd")
            .eq("status", "active")
            .gte("payment_confirmed_at", month_start.isoformat())
            .execute()
        )
        monthly_revenue_usd = sum(row.get("price_usd", 0) or 0 for row in (revenue_result.data or []))

        # Expiring in 3 days
        three_days = now + timedelta(days=3)
        expiring_result = (
            sb.table("subscriptions")
            .select("id", count="exact")
            .eq("status", "active")
            .lte("end_date", three_days.isoformat())
            .gte("end_date", now.isoformat())
            .execute()
        )
        expiring_soon = expiring_result.count or 0

        # New users today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_result = (
            sb.table("users")
            .select("id", count="exact")
            .gte("created_at", today_start.isoformat())
            .execute()
        )
        new_users_today = new_users_result.count or 0

        # Platform availability
        availability = await get_platform_availability()

        return {
            "total_users": total_users,
            "active_subscriptions": active_subs,
            "pending_payments": pending_payments,
            "monthly_revenue_usd": round(monthly_revenue_usd, 2),
            "expiring_soon": expiring_soon,
            "new_users_today": new_users_today,
            "platform_availability": availability,
        }
    except Exception as e:
        logger.error(f"Error in get_dashboard_stats: {e}")
        return {}


async def get_income_report(period: str = "month") -> dict:
    """Get financial report for a period (month, week, today)."""
    try:
        sb = get_supabase()
        now = venezuela_now()

        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start = now - timedelta(days=7)
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = (
            sb.table("subscriptions")
            .select("price_usd, price_bs, plan_type, platforms(name)")
            .in_("status", ["active", "expired"])
            .gte("payment_confirmed_at", start.isoformat())
            .execute()
        )

        rows = result.data or []
        total_usd = sum(row.get("price_usd", 0) or 0 for row in rows)
        total_bs = sum(row.get("price_bs", 0) or 0 for row in rows)

        by_platform: dict = {}
        by_plan: dict = {}
        for row in rows:
            platform_name = (row.get("platforms") or {}).get("name", "Unknown")
            plan = row.get("plan_type", "unknown")
            by_platform[platform_name] = by_platform.get(platform_name, 0) + (row.get("price_usd") or 0)
            by_plan[plan] = by_plan.get(plan, 0) + (row.get("price_usd") or 0)

        return {
            "period": period,
            "total_usd": round(total_usd, 2),
            "total_bs": round(total_bs, 2),
            "transaction_count": len(rows),
            "by_platform": {k: round(v, 2) for k, v in by_platform.items()},
            "by_plan": {k: round(v, 2) for k, v in by_plan.items()},
        }
    except Exception as e:
        logger.error(f"Error in get_income_report: {e}")
        return {}


async def get_clients_list(page: int = 1, per_page: int = 10) -> dict:
    """Get paginated clients list."""
    try:
        sb = get_supabase()
        offset = (page - 1) * per_page

        count_result = sb.table("users").select("id", count="exact").execute()
        total = count_result.count or 0

        result = (
            sb.table("users")
            .select("telegram_id, name, username, status, total_purchases, created_at, last_seen")
            .order("created_at", desc=True)
            .range(offset, offset + per_page - 1)
            .execute()
        )

        return {
            "clients": result.data or [],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        }
    except Exception as e:
        logger.error(f"Error in get_clients_list: {e}")
        return {"clients": [], "total": 0, "page": page, "per_page": per_page, "total_pages": 0}


async def get_client_detail(telegram_id: int) -> Optional[dict]:
    """Get full client info including subscription history."""
    try:
        sb = get_supabase()
        user_result = sb.table("users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
        if not user_result.data:
            return None

        user = user_result.data[0]

        subs_result = (
            sb.table("subscriptions")
            .select("*, platforms(name, icon_emoji)")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        return {
            "user": user,
            "subscriptions": subs_result.data or [],
        }
    except Exception as e:
        logger.error(f"Error in get_client_detail: {e}")
        return None


async def get_platform_availability() -> list[dict]:
    """Get stock counts per platform."""
    try:
        from database.platforms import get_active_platforms
        from database.profiles import count_available_profiles

        platforms = await get_active_platforms()
        availability = []
        for p in platforms:
            monthly_count = await count_available_profiles(p["id"], "monthly")
            express_count = await count_available_profiles(p["id"], "express")
            week_count = await count_available_profiles(p["id"], "week")
            availability.append({
                "platform_id": p["id"],
                "name": p["name"],
                "slug": p["slug"],
                "icon_emoji": p.get("icon_emoji", ""),
                "monthly_available": monthly_count,
                "express_available": express_count,
                "week_available": week_count,
            })
        return availability
    except Exception as e:
        logger.error(f"Error in get_platform_availability: {e}")
        return []
