"""
Monthly closing report — financial reality for the previous month.
Called by Job 11 on the 1st of each month at 9:00 AM Venezuela time.
"""

import logging
from calendar import monthrange

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def get_monthly_closing_report() -> dict:
    try:
        sb = get_supabase()
        now = venezuela_now()

        # Rango del mes ANTERIOR
        if now.month == 1:
            prev_year  = now.year - 1
            prev_month = 12
        else:
            prev_year  = now.year
            prev_month = now.month - 1

        days_in_prev_month = monthrange(prev_year, prev_month)[1]

        month_start = now.replace(
            year=prev_year, month=prev_month, day=1,
            hour=0, minute=0, second=0, microsecond=0
        )
        month_end = now.replace(
            year=prev_year, month=prev_month, day=days_in_prev_month,
            hour=23, minute=59, second=59, microsecond=999999
        )

        MONTH_NAMES = [
            "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]
        month_label = f"{MONTH_NAMES[prev_month]} {prev_year}"

        # 1. Ingresos — todo lo cobrado en el mes anterior
        revenue_result = sb.table("subscriptions").select(
            "id, user_id, price_usd, plan_type, payment_confirmed_at, platforms(name)"
        ).in_("status", ["active", "expired"]).gte(
            "payment_confirmed_at", month_start.isoformat()
        ).lte(
            "payment_confirmed_at", month_end.isoformat()
        ).execute()

        rows = revenue_result.data or []

        total_revenue       = round(sum(r.get("price_usd") or 0 for r in rows), 2)
        total_transactions  = len(rows)

        by_platform: dict = {}
        by_plan: dict     = {}
        express_count     = 0
        express_revenue   = 0.0
        user_ids_this_month = set()

        for r in rows:
            platform_name = (r.get("platforms") or {}).get("name", "Desconocida")
            plan          = r.get("plan_type", "unknown")
            amount        = r.get("price_usd") or 0
            uid           = r.get("user_id")

            by_platform[platform_name] = round(
                by_platform.get(platform_name, 0) + amount, 2
            )
            by_plan[plan] = round(by_plan.get(plan, 0) + amount, 2)

            if plan == "express":
                express_count   += 1
                express_revenue += amount

            if uid:
                user_ids_this_month.add(uid)

        express_revenue = round(express_revenue, 2)

        # 2. Costos — cuentas activas × costo mensual completo (sin prorrateo)
        cost_result = sb.table("accounts").select(
            "email, cost_usd_monthly, platforms(name)"
        ).eq("status", "active").execute()

        cost_rows  = cost_result.data or []
        total_cost = round(
            sum(r.get("cost_usd_monthly") or 0 for r in cost_rows), 2
        )

        account_breakdown = []
        for r in cost_rows:
            pname = (r.get("platforms") or {}).get("name", "Desconocida")
            cost  = r.get("cost_usd_monthly") or 0
            if cost > 0:
                account_breakdown.append({
                    "email":    r.get("email", ""),
                    "platform": pname,
                    "cost":     cost,
                })

        # 3. Ganancia neta
        net_profit = round(total_revenue - total_cost, 2)
        margin_pct = (
            round(net_profit / total_revenue * 100, 1)
            if total_revenue > 0 else 0.0
        )

        # 4. Nuevos clientes vs renovaciones
        new_clients = 0
        renewals    = 0

        for uid in user_ids_this_month:
            prior = sb.table("subscriptions").select("id").eq(
                "user_id", uid
            ).in_("status", ["active", "expired"]).lt(
                "payment_confirmed_at", month_start.isoformat()
            ).limit(1).execute()

            if prior.data:
                renewals    += 1
            else:
                new_clients += 1

        # 5. No-renovaciones — expiraron el mes pasado y NO pagaron ese mes
        expired_prev = sb.table("subscriptions").select(
            "user_id"
        ).eq("status", "expired").eq("plan_type", "monthly").gte(
            "end_date", month_start.isoformat()
        ).lte(
            "end_date", month_end.isoformat()
        ).execute()

        expired_uids = {r["user_id"] for r in (expired_prev.data or [])}
        non_renewals = len(expired_uids - user_ids_this_month)

        return {
            "month_label":        month_label,
            "total_revenue_usd":  total_revenue,
            "total_cost_usd":     total_cost,
            "net_profit_usd":     net_profit,
            "margin_pct":         margin_pct,
            "total_transactions": total_transactions,
            "by_platform":        by_platform,
            "by_plan":            by_plan,
            "new_clients":        new_clients,
            "renewals":           renewals,
            "non_renewals":       non_renewals,
            "express_count":      express_count,
            "express_revenue":    express_revenue,
            "account_breakdown":  account_breakdown,
        }

    except Exception as e:
        logger.error(f"Error in get_monthly_closing_report: {e}")
        return {}
