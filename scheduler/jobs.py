from __future__ import annotations

import asyncio
import functools
import logging
from datetime import timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

VENEZUELA_TZ = pytz.timezone("America/Caracas")
scheduler = AsyncIOScheduler(timezone=VENEZUELA_TZ)


def _locked_job(name: str, ttl_seconds: int):
    """Candado Redis por job (O02 Lote 6): dos pasadas o dos procesos no corren el mismo job a la vez.
    Falla cerrada: si Redis no responde, esta pasada se omite (mejor saltarla que correrla dos veces)."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            from services.locks import lock
            with lock(f"job:{name}", ttl_seconds) as token:
                if token is None:
                    logger.warning(f"job {name}: otra pasada lo tiene o Redis no responde; se omite esta pasada")
                    return None
                return await fn(*args, **kwargs)
        return wrapper
    return deco


# ============================================================
# JOB 1: Expiry reminders (D-3) - daily at 10AM VE time
# ============================================================
@_locked_job("expiry_reminders", 900)
async def job_expiry_reminders() -> None:
    """Send 3-day expiry reminders. Cada aviso se reclama antes de enviarlo (UPDATE condicional) para
    que dos pasadas simultáneas no lo manden dos veces; si el envío falla, se devuelve el reclamo."""
    logger.info("Running job: expiry_reminders")
    try:
        from database.subscriptions import claim_flag, get_expiring_subscriptions, release_flag
        from services.notification_service import send_expiry_reminder

        expiring = await get_expiring_subscriptions(days_ahead=3)
        logger.info(f"Found {len(expiring)} expiring subscriptions to remind")

        for sub in expiring:
            sub_id = str(sub["id"])
            try:
                if not await claim_flag(sub_id, "reminder_sent"):
                    continue
                if not await send_expiry_reminder(sub_id):
                    await release_flag(sub_id, "reminder_sent")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending expiry reminder for {sub_id}: {e}")
    except Exception as e:
        logger.error(f"Error in job_expiry_reminders: {e}")


# ============================================================
# JOB 2: Expiry notifications (D+0) - hourly
# ============================================================
@_locked_job("expiry_notifications", 900)
async def job_expiry_notifications() -> None:
    """Aviso D+0 a las suscripciones recién vencidas (`active` o `expired` heredado, últimas 48 h),
    reclamado de forma atómica antes de enviar."""
    logger.info("Running job: expiry_notifications")
    try:
        from database.subscriptions import claim_flag, get_expired_subscriptions_to_notify, release_flag
        from services.notification_service import send_expiry_notification

        expired = await get_expired_subscriptions_to_notify()
        logger.info(f"Found {len(expired)} expired subscriptions to notify")

        for sub in expired:
            sub_id = str(sub["id"])
            try:
                if not await claim_flag(sub_id, "expiry_notified"):
                    continue
                if not await send_expiry_notification(sub_id):
                    await release_flag(sub_id, "expiry_notified")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending expiry notification for {sub_id}: {e}")
    except Exception as e:
        logger.error(f"Error in job_expiry_notifications: {e}")


# ============================================================
# JOB 3: Express release - every 5 minutes
# ============================================================
@_locked_job("express_release", 240)
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

                if sub.get("status") == "expired":
                    # O02-H: `auto_expire` la marcó antes que este job. Se libera solo si el perfil
                    # sigue ocupado y ninguna otra suscripción activa lo usa (idempotente).
                    from database.profiles import release_profile_for_cut
                    rel = await release_profile_for_cut(str(profile_id), exclude_sub_id=sub_id)
                    if not rel or not rel["released"]:
                        logger.info(
                            f"job_express_release: express {sub_id} (expired) no liberada: "
                            f"{(rel or {}).get('reason') or 'error al leer el perfil'}"
                        )
                        continue
                    old_pin = rel["old_pin"]
                    new_pin = rel["new_pin"]
                else:
                    # Expire subscription
                    expired_ok = await expire_subscription(sub_id)
                    if not expired_ok:
                        logger.warning(
                            f"job_express_release: expire_subscription failed for {sub_id}, skipping to avoid double processing"
                        )
                        continue

                # Change PIN + release profile
                if sub.get("status") == "expired":
                    pass  # ya liberado arriba
                elif profile_id:
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
@_locked_job("queue_cleanup", 600)
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
@_locked_job("new_releases_scan", 900)
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
@_locked_job("pending_payment_cleanup", 300)
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
@_locked_job("daily_admin_report", 600)
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
@_locked_job("debt_reminders", 1800)
async def job_debt_reminders_and_cuts() -> None:
    """Recordatorios de deuda D+1 a D+6 y resumen diario de mora al admin.

    OPCIÓN B (decisión del Orquestador, 27/11/2026): **no hay corte automático**. El servicio de un
    cliente en mora sigue activo; cortar es una acción manual del admin. Después del día 6 no se envían más
    recordatorios. (El nombre de la función se conserva por compatibilidad con el id del job.)
    """
    logger.info("Running job: debt_reminders")
    try:
        from database.subscriptions import (
            claim_debt_reminder, get_subscriptions_for_debt_reminders, release_debt_reminder,
        )
        from services.notification_service import send_debt_reminder

        subs = await get_subscriptions_for_debt_reminders()
        logger.info(f"Found {len(subs)} subscriptions due a debt reminder today")

        for sub in subs:
            sub_id = str(sub["id"])
            day = int(sub["days_overdue"])
            try:
                if not await claim_debt_reminder(sub_id, day):
                    continue
                if not await send_debt_reminder(sub, day):
                    await release_debt_reminder(sub_id, day)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending debt reminder for {sub_id}: {e}")
    except Exception as e:
        logger.error(f"Error in job_debt_reminders_and_cuts (recordatorios): {e}")

    await _send_mora_summary()


async def _send_mora_summary() -> None:
    """Resumen diario al admin: clientes con 7 días de mora o más, su deuda y un botón por cliente."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        from config import settings
        from database.subscriptions import get_overdue_subscriptions
        from services import mora
        from services.notification_service import send_to_admin

        rows = mora.mora_rows(await get_overdue_subscriptions(mora.MIN_DAYS))
        if not rows:
            return
        base = (settings.APP_URL or "").rstrip("/")
        panel_url = f"{base}/panel/subscriptions?status=overdue" if base.startswith("http") else ""
        buttons = [[InlineKeyboardButton(text, callback_data=cb)] for text, cb in mora.client_buttons(rows)]
        if panel_url.startswith("https://"):
            buttons.append([InlineKeyboardButton("📋 Ver todos en el panel", url=panel_url)])
        await send_to_admin(mora.summary_text(rows, panel_url), keyboard=InlineKeyboardMarkup(buttons) if buttons else None)
    except Exception as e:
        logger.error(f"Error in _send_mora_summary: {e}")


# ============================================================
# JOB 9: Release expired profile reservations - every 10 min
# ============================================================
@_locked_job("release_expired_reservations", 300)
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
# JOB 10 (retirado): auto_expire. La mora es un dato derivado (end_date < ahora); ningún job cambia
# el estado de una suscripción por estar vencida (O02 Lote 6, opción B).
# ============================================================

# ============================================================
# JOB 12: Vencimiento de órdenes de pago sin pagar (24 h) - cada 30 min, solo con CHECKOUT_V2_ENABLED
# ============================================================
@_locked_job("expire_orders", 300)
async def job_expire_orders() -> None:
    """Pasa a `expired` las órdenes pendientes cuyo plazo (24 h) venció. No se borran: si el cliente
    pagó tarde, el admin puede aprobarlas con motivo."""
    try:
        from config import settings
        if not settings.CHECKOUT_V2_ENABLED:
            return
        from database.orders import expire_stale_orders
        n = await expire_stale_orders()
        if n:
            logger.info(f"job_expire_orders: {n} orden(es) vencida(s)")
    except Exception as e:
        logger.error(f"Error in job_expire_orders: {e}")


@_locked_job("monthly_closing_report", 900)
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


def _add(func, trigger, job_id: str, name: str, grace: int) -> None:
    """Registra un job con `coalesce` (pasadas perdidas = una sola), una instancia a la vez y una
    tolerancia explícita de retraso (`misfire_grace_time`)."""
    scheduler.add_job(
        func, trigger, id=job_id, name=name, replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=grace,
    )


def setup_scheduler() -> AsyncIOScheduler:
    """Configure and return the scheduler with all jobs."""
    _add(job_expiry_reminders, CronTrigger(hour=10, minute=0, timezone=VENEZUELA_TZ), "expiry_reminders", "Expiry Reminders (D-3)", 3600)
    _add(job_expiry_notifications, IntervalTrigger(hours=1), "expiry_notifications", "Expiry Notifications (D+0)", 900)
    _add(job_express_release, IntervalTrigger(minutes=5), "express_release", "Express Release", 120)
    _add(job_queue_cleanup, CronTrigger(hour=3, minute=0, timezone=VENEZUELA_TZ), "queue_cleanup", "Queue Cleanup", 3600)
    _add(job_new_releases_scan, CronTrigger(day_of_week="mon,thu", hour=9, minute=0, timezone=VENEZUELA_TZ), "new_releases_scan", "New Releases Scan", 3600)
    _add(job_pending_payment_cleanup, IntervalTrigger(hours=4), "pending_payment_cleanup", "Pending Payment Cleanup", 900)
    _add(job_daily_admin_report, CronTrigger(hour=8, minute=0, timezone=VENEZUELA_TZ), "daily_admin_report", "Daily Admin Report", 3600)
    # Recordatorios de deuda D+1..D+6 y resumen de mora (SIN corte automático).
    _add(job_debt_reminders_and_cuts, CronTrigger(hour=9, minute=0, timezone=VENEZUELA_TZ), "debt_reminders_and_cuts", "Debt Reminders + Mora Summary", 3600)
    _add(job_release_expired_reservations, IntervalTrigger(minutes=10), "release_expired_reservations", "Release Expired Profile Reservations", 300)
    _add(job_monthly_closing_report, CronTrigger(day=1, hour=9, minute=0, timezone=VENEZUELA_TZ), "job_monthly_closing_report", "Monthly Closing Report", 3600)
    _add(job_expire_orders, IntervalTrigger(minutes=30), "expire_orders", "Expire Unpaid Orders (24h)", 600)

    logger.info("Scheduler configured with 11 jobs (sin auto_expire ni corte automático)")
    return scheduler
