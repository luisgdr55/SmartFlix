from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

VENEZUELA_TZ = pytz.timezone("America/Caracas")
scheduler = AsyncIOScheduler(timezone=VENEZUELA_TZ)


# ============================================================
# JOB 1: Expiry reminders (D-3) - daily at 10AM VE time
# ============================================================
async def job_expiry_reminders() -> None:
    """Send 3-day expiry reminders."""
    logger.info("Running job: expiry_reminders")
    try:
        from database.subscriptions import get_expiring_subscriptions
        from services.notification_service import send_expiry_reminder

        expiring = await get_expiring_subscriptions(days_ahead=3)
        logger.info(f"Found {len(expiring)} expiring subscriptions to remind")

        for sub in expiring:
            try:
                await send_expiry_reminder(str(sub["id"]))
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending expiry reminder for {sub['id']}: {e}")
    except Exception as e:
        logger.error(f"Error in job_expiry_reminders: {e}")


# ============================================================
# JOB 2: Expiry notifications (D+0) - hourly
# ============================================================
async def job_expiry_notifications() -> None:
    """Send expiry notifications for subscriptions that just expired."""
    logger.info("Running job: expiry_notifications")
    try:
        from database.subscriptions import get_expired_subscriptions_to_notify
        from services.notification_service import send_expiry_notification

        expired = await get_expired_subscriptions_to_notify()
        logger.info(f"Found {len(expired)} expired subscriptions to notify")

        for sub in expired:
            try:
                await send_expiry_notification(str(sub["id"]))
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending expiry notification for {sub['id']}: {e}")
    except Exception as e:
        logger.error(f"Error in job_expiry_notifications: {e}")


# ============================================================
# JOB 3: Express release - every 5 minutes
# ============================================================
async def job_express_release() -> None:
    """Release expired express subscriptions, rotate PIN, notify admin and queue."""
    logger.info("Running job: express_release")
    try:
        import random
        import string
        from database.subscriptions import get_expired_express_subscriptions, expire_subscription
        from database.profiles import release_profile
        from services.notification_service import send_express_expired, notify_express_queue, send_to_admin

        expired_express = await get_expired_express_subscriptions()

        for sub in expired_express:
            try:
                sub_id = str(sub["id"])
                profile = sub.get("profiles") or {}
                profile_id = profile.get("id")
                profile_name = profile.get("profile_name", "—")
                old_pin = profile.get("pin", "—")
                account = profile.get("accounts") or {}
                platform_id = str(sub.get("platform_id", ""))
                platform = sub.get("platforms") or {}
                platform_name = f"{platform.get('icon_emoji','')} {platform.get('name','')}"
                user = sub.get("users") or {}
                client_name = user.get("name") or user.get("username") or "Sin nombre"

                # Expire subscription
                expired_ok = await expire_subscription(sub_id)
                if not expired_ok:
                    logger.warning(
                        f"job_express_release: expire_subscription failed for {sub_id}, skipping to avoid double processing"
                    )
                    continue

                # Change PIN + release profile
                if profile_id:
                    new_pin = "".join(random.choices(string.digits, k=4))
                    from database import get_supabase
                    from utils.helpers import venezuela_now
                    sb = get_supabase()
                    sb.table("profiles").update({
                        "pin": new_pin,
                        "status": "available",
                        "last_released": venezuela_now().isoformat(),
                    }).eq("id", profile_id).execute()
                else:
                    new_pin = "—"

                # Notify client of expiry + upsell
                await send_express_expired(sub_id)

                # Notify admin
                admin_msg = (
                    f"⚡ <b>Express liberado</b>\n\n"
                    f"🎬 Plataforma: <b>{platform_name}</b>\n"
                    f"👤 Perfil: <b>{profile_name}</b>\n"
                    f"👥 Cliente: <b>{client_name}</b>\n\n"
                    f"🔐 <b>Datos de la cuenta:</b>\n"
                    f"📧 Email: <code>{account.get('email', '—')}</code>\n"
                    f"🔑 Contraseña: <code>{account.get('password', '—')}</code>\n"
                    f"🔢 PIN anterior: <code>{old_pin}</code>\n"
                    f"🔄 PIN nuevo asignado: <code>{new_pin}</code>\n\n"
                    f"⚠️ Entra con el PIN anterior y cámbialo al nuevo en la plataforma."
                )
                await send_to_admin(admin_msg)

                # Notify queue if there are waiting users
                if platform_id:
                    await notify_express_queue(platform_id)

                await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Error releasing express sub {sub.get('id')}: {e}")
    except Exception as e:
        logger.error(f"Error in job_express_release: {e}")


# ============================================================
# JOB 4: Queue cleanup - daily at 3AM
# ============================================================
async def job_queue_cleanup() -> None:
    """Remove expired entries from express_queue."""
    logger.info("Running job: queue_cleanup")
    try:
        from database import get_supabase
        from utils.helpers import venezuela_now

        sb = get_supabase()
        now = venezuela_now()
        sb.table("express_queue").delete().lte("expires_at", now.isoformat()).execute()
        logger.info("Express queue cleanup completed")
    except Exception as e:
        logger.error(f"Error in job_queue_cleanup: {e}")


# ============================================================
# JOB 5: New releases scan - Mon+Thu 9AM
# ============================================================
async def job_new_releases_scan() -> None:
    """Scan for new content releases in Venezuela."""
    logger.info("Running job: new_releases_scan")
    try:
        from services.tmdb_service import scan_new_releases_venezuela, check_venezuela_availability
        from database import get_supabase
        from utils.helpers import venezuela_now

        sb = get_supabase()
        releases = await scan_new_releases_venezuela()
        logger.info(f"Found {len(releases)} potential new releases")

        for item in releases[:20]:
            try:
                tmdb_id = item.get("id")
                content_type = item.get("content_type", "movie")
                title = item.get("title") or item.get("name", "Unknown")

                if not tmdb_id:
                    continue

                # Check if already announced
                existing = (
                    sb.table("announced_content")
                    .select("id")
                    .eq("tmdb_id", tmdb_id)
                    .execute()
                )
                if existing.data:
                    continue

                # Check Venezuela availability
                availability = await check_venezuela_availability(tmdb_id, content_type)
                if not availability.get("available"):
                    continue

                # Log for admin review (don't auto-announce)
                logger.info(f"New content available: {title} (TMDB {tmdb_id}) - Providers: {availability.get('providers', [])}")

            except Exception as e:
                logger.warning(f"Error processing release {item.get('id')}: {e}")
                await asyncio.sleep(0.2)

    except Exception as e:
        logger.error(f"Error in job_new_releases_scan: {e}")


# ============================================================
# JOB 6: Pending payment cleanup - every 45 minutes
# ============================================================
async def job_pending_payment_cleanup() -> None:
    """Cancel pending subscriptions older than 45 minutes."""
    logger.info("Running job: pending_payment_cleanup")
    try:
        from database.subscriptions import cancel_expired_pending_subscriptions
        cancelled = await cancel_expired_pending_subscriptions()
        if cancelled > 0:
            logger.info(f"Cancelled {cancelled} expired pending subscriptions")
    except Exception as e:
        logger.error(f"Error in job_pending_payment_cleanup: {e}")


# ============================================================
# JOB 7: Daily admin report - 8AM VE time
# ============================================================
async def job_daily_admin_report() -> None:
    """Send daily stats report to admins."""
    logger.info("Running job: daily_admin_report")
    try:
        from database.analytics import get_dashboard_stats
        from services.notification_service import send_to_admin
        from services.exchange_service import check_rate_staleness
        from utils.helpers import format_date_vzla, venezuela_now

        stats = await get_dashboard_stats()
        stale_warning = await check_rate_staleness()

        availability = stats.get("platform_availability", [])
        avail_text = ""
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            monthly = p.get("monthly_available", 0)
            express = p.get("express_available", 0)
            avail_text += f"{icon} {name}: {monthly}M | {express}E\n"

        report_text = (
            f"📊 <b>Reporte Diario - {format_date_vzla(venezuela_now())}</b>\n\n"
            f"👥 Usuarios totales: <b>{stats.get('total_users', 0)}</b>\n"
            f"🆕 Nuevos (últimos 7 días): <b>{stats.get('new_users_week', 0)}</b>\n"
            f"✅ Suscripciones activas: <b>{stats.get('active_subscriptions', 0)}</b>\n"
            f"⏳ Pagos pendientes: <b>{stats.get('pending_payments', 0)}</b>\n"
            f"⚠️ Vencen en 3 días: <b>{stats.get('expiring_soon', 0)}</b>\n"
            f"💰 Ingresos brutos: <b>${stats.get('monthly_revenue_usd', 0):.2f} USD</b>\n"
            f"💸 Costos al día de hoy: <b>${stats.get('monthly_cost_usd', 0):.2f} USD</b>\n"
            f"📈 Ganancia neta: <b>${stats.get('monthly_profit_usd', 0):.2f} USD</b>\n\n"
            f"📦 Stock:\n{avail_text}"
        )

        if stale_warning:
            report_text += f"\n⚠️ {stale_warning}"

        await send_to_admin(report_text)
    except Exception as e:
        logger.error(f"Error in job_daily_admin_report: {e}")


# ============================================================
# JOB 8: Debt reminders + hard cut - daily at 9AM VE time
# ============================================================
async def job_debt_reminders_and_cuts() -> None:
    """
    Daily job for expired monthly subscriptions:
    - Days 1-6 after expiration: send a debt reminder and increment counter.
    - Day 7+ (counter >= 6): release profile, cancel subscription, notify client + admin.
    """
    logger.info("Running job: debt_reminders_and_cuts")
    try:
        from database.subscriptions import (
            get_subscriptions_in_grace_period,
            increment_debt_reminder,
        )
        from services.notification_service import (
            send_debt_reminder,
        )

        # ── PART 1: Send daily debt reminders (days 1-6) ──────────────
        grace_subs = await get_subscriptions_in_grace_period()
        logger.info(f"Found {len(grace_subs)} subscriptions in grace period")

        for sub in grace_subs:
            try:
                sub_id = str(sub["id"])
                current_count = sub.get("debt_reminder_count") or 0
                day_number = current_count + 1
                await send_debt_reminder(sub, day_number)
                await increment_debt_reminder(sub_id, current_count)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending debt reminder for {sub.get('id')}: {e}")
    except Exception as e:
        logger.error(f"Error in job_debt_reminders_and_cuts: {e}")


# ============================================================
# JOB 9: Release expired profile reservations - every 10 min
# ============================================================
async def job_release_expired_reservations() -> None:
    """Release profiles whose 30-min reservation TTL has expired."""
    try:
        from database.profiles import release_expired_reservations
        count = await release_expired_reservations()
        if count:
            logger.info(f"job_release_expired_reservations: released {count} profile(s)")
    except Exception as e:
        logger.error(f"Error in job_release_expired_reservations: {e}")


# ============================================================
# JOB 10: Auto-expire overdue subscriptions - every 15 min
# ============================================================
async def job_auto_expire() -> None:
    """Mark active-but-overdue subscriptions as expired."""
    try:
        from database.subscriptions import auto_expire_overdue_subscriptions
        count = await auto_expire_overdue_subscriptions()
        if count:
            logger.info(f"job_auto_expire: expired {count} overdue subscription(s)")
    except Exception as e:
        logger.error(f"Error in job_auto_expire: {e}")


async def job_monthly_closing_report():
    """Job 11 — Monthly closing report. Runs 1st of each month at 9:00 AM Venezuela."""
    logger.info("Job 11: Sending monthly closing report")
    try:
        from database.monthly_report import get_monthly_closing_report
        from services.notification_service import send_to_admin
        data = await get_monthly_closing_report()
        if not data:
            await send_to_admin("⚠️ Error al generar el reporte mensual de cierre.")
            return

        profit      = data["net_profit_usd"]
        profit_icon = "📈" if profit >= 0 else "📉"
        margin_str  = f"{data['margin_pct']}%" if data["total_revenue_usd"] > 0 else "N/A"

        platform_lines = ""
        for platform, amount in sorted(
            data["by_platform"].items(), key=lambda x: x[1], reverse=True
        ):
            platform_lines += f"  • {platform}: <b>${amount:.2f}</b>\n"

        cost_lines = ""
        for acc in sorted(
            data["account_breakdown"], key=lambda x: x["cost"], reverse=True
        ):
            cost_lines += f"  • {acc['platform']} ({acc['email']}): <b>${acc['cost']:.2f}</b>\n"
        if not cost_lines:
            cost_lines = "  • Sin costos registrados — actualiza en Panel → Cuentas → Editar\n"

        msg = (
            f"📅 <b>CIERRE MENSUAL — {data['month_label'].upper()}</b>\n"
            f"{'═' * 32}\n\n"
            f"💰 <b>INGRESOS</b>\n"
            f"  Total cobrado: <b>${data['total_revenue_usd']:.2f} USD</b>\n"
            f"  Transacciones: {data['total_transactions']}\n"
            f"  Express: {data['express_count']} cobros (${data['express_revenue']:.2f})\n\n"
            f"📊 <b>Por plataforma:</b>\n{platform_lines}\n"
            f"💸 <b>COSTOS DEL MES</b>\n"
            f"{cost_lines}\n"
            f"  Total costos: <b>${data['total_cost_usd']:.2f} USD</b>\n\n"
            f"{profit_icon} <b>RESULTADO</b>\n"
            f"  Ganancia neta: <b>${profit:.2f} USD</b>\n"
            f"  Margen: <b>{margin_str}</b>\n\n"
            f"👥 <b>CLIENTES</b>\n"
            f"  Nuevos este mes: {data['new_clients']}\n"
            f"  Renovaciones: {data['renewals']}\n"
            f"  No renovaron: {data['non_renewals']}\n"
        )

        await send_to_admin(msg)
        logger.info(
            f"Monthly closing report sent — {data['month_label']}: "
            f"revenue=${data['total_revenue_usd']} "
            f"cost=${data['total_cost_usd']} "
            f"profit=${data['net_profit_usd']}"
        )

    except Exception as e:
        logger.error(f"Error in job_monthly_closing_report: {e}")
        await send_to_admin(f"⚠️ Error en reporte mensual: {e}")


def setup_scheduler() -> AsyncIOScheduler:
    """Configure and return the scheduler with all jobs."""
    # Job 1: Expiry reminders - daily at 10:00 AM Venezuela time
    scheduler.add_job(
        job_expiry_reminders,
        CronTrigger(hour=10, minute=0, timezone=VENEZUELA_TZ),
        id="expiry_reminders",
        name="Expiry Reminders (D-3)",
        replace_existing=True,
    )

    # Job 2: Expiry notifications - every hour
    scheduler.add_job(
        job_expiry_notifications,
        IntervalTrigger(hours=1),
        id="expiry_notifications",
        name="Expiry Notifications",
        replace_existing=True,
    )

    # Job 3: Express release - every 5 minutes
    scheduler.add_job(
        job_express_release,
        IntervalTrigger(minutes=5),
        id="express_release",
        name="Express Release",
        replace_existing=True,
    )

    # Job 4: Queue cleanup - daily at 3:00 AM Venezuela time
    scheduler.add_job(
        job_queue_cleanup,
        CronTrigger(hour=3, minute=0, timezone=VENEZUELA_TZ),
        id="queue_cleanup",
        name="Queue Cleanup",
        replace_existing=True,
    )

    # Job 5: New releases scan - Monday and Thursday at 9:00 AM Venezuela time
    scheduler.add_job(
        job_new_releases_scan,
        CronTrigger(day_of_week="mon,thu", hour=9, minute=0, timezone=VENEZUELA_TZ),
        id="new_releases_scan",
        name="New Releases Scan",
        replace_existing=True,
    )

    # Job 6: Pending payment cleanup - every 45 minutes
    scheduler.add_job(
        job_pending_payment_cleanup,
        IntervalTrigger(minutes=45),
        id="pending_payment_cleanup",
        name="Pending Payment Cleanup",
        replace_existing=True,
    )

    # Job 7: Daily admin report - 8:00 AM Venezuela time
    scheduler.add_job(
        job_daily_admin_report,
        CronTrigger(hour=8, minute=0, timezone=VENEZUELA_TZ),
        id="daily_admin_report",
        name="Daily Admin Report",
        replace_existing=True,
    )

    # Job 8: Debt reminders - 9:00 AM Venezuela time
    scheduler.add_job(
        job_debt_reminders_and_cuts,
        CronTrigger(hour=9, minute=0, timezone=VENEZUELA_TZ),
        id="debt_reminders_and_cuts",
        name="Debt Reminders",
        replace_existing=True,
    )

    # Job 9: Release expired profile reservations - every 10 min
    scheduler.add_job(
        job_release_expired_reservations,
        "interval",
        minutes=10,
        id="release_expired_reservations",
        name="Release Expired Profile Reservations",
        replace_existing=True,
    )

    # Job 10: Auto-expire overdue subscriptions - every 15 minutes
    scheduler.add_job(
        job_auto_expire,
        IntervalTrigger(minutes=15),
        id="auto_expire",
        name="Auto Expire Overdue Subscriptions",
        replace_existing=True,
    )

    # Job 11 — Monthly closing report (día 1 de cada mes, 9:00 AM Venezuela)
    scheduler.add_job(
        job_monthly_closing_report,
        CronTrigger(day=1, hour=9, minute=0, timezone=VENEZUELA_TZ),
        id="job_monthly_closing_report",
        name="Monthly Closing Report",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    logger.info("Scheduler configured with 11 jobs")
    return scheduler
