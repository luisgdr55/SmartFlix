from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError

from config import settings
from utils.helpers import parse_telegram_ids, format_datetime_vzla, days_remaining

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def init_notification_bot(bot: Bot) -> None:
    """Register the application's already-initialized bot instance."""
    global _bot
    _bot = bot


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        # Fallback: create bare instance (may not work in all ptb versions)
        _bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    return _bot


async def send_to_user(
    telegram_id: int,
    message: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
    photo_bytes: Optional[bytes] = None,
) -> bool:
    """Send a message or photo to a user."""
    try:
        bot = get_bot()
        if photo_bytes:
            await bot.send_photo(
                chat_id=telegram_id,
                photo=photo_bytes,
                caption=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        return True
    except TelegramError as e:
        logger.error(f"Telegram error sending to {telegram_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error sending to user {telegram_id}: {e}")
        return False


async def send_to_admin(
    message: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
    photo_bytes: Optional[bytes] = None,
) -> None:
    """Send a message to all configured admins."""
    admin_ids = parse_telegram_ids(settings.ADMIN_TELEGRAM_IDS)
    for admin_id in admin_ids:
        await send_to_user(admin_id, message, keyboard, photo_bytes)
        await asyncio.sleep(0.1)


async def broadcast_campaign(
    campaign_id: str,
    user_ids: list[int],
    message_template: str,
    photo_url: Optional[str] = None,
) -> dict:
    """
    Send a campaign to a list of users.
    Includes 500ms anti-spam delay between messages.
    Returns stats dict.
    """
    from database import get_supabase
    from database.users import get_user_by_telegram_id

    sent = 0
    failed = 0
    bot = get_bot()

    for telegram_id in user_ids:
        try:
            # Personalize message
            user = await get_user_by_telegram_id(telegram_id)
            name = (user or {}).get("name") or "amigo/a"
            message = message_template.replace("{name}", name)

            if photo_url:
                await bot.send_photo(
                    chat_id=telegram_id,
                    photo=photo_url,
                    caption=message,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=message,
                    parse_mode="HTML",
                )
            sent += 1
        except TelegramError as e:
            logger.warning(f"Failed to send campaign to {telegram_id}: {e}")
            failed += 1
        except Exception as e:
            logger.error(f"Unexpected error sending campaign to {telegram_id}: {e}")
            failed += 1

        # Anti-spam delay 500ms
        await asyncio.sleep(0.5)

    # Update campaign stats
    try:
        sb = get_supabase()
        from utils.helpers import venezuela_now
        sb.table("campaigns").update({
            "sent_count": sent,
            "status": "sent",
            "sent_at": venezuela_now().isoformat(),
        }).eq("id", campaign_id).execute()
    except Exception as e:
        logger.error(f"Error updating campaign stats: {e}")

    return {"sent": sent, "failed": failed, "total": len(user_ids)}


async def notify_express_queue(platform_id: str) -> int:
    """Notify users in express queue that a slot is available."""
    from database import get_supabase
    from utils.helpers import venezuela_now

    notified_count = 0
    try:
        sb = get_supabase()
        result = (
            sb.table("express_queue")
            .select("*, users(telegram_id, name), platforms(name, icon_emoji)")
            .eq("platform_id", platform_id)
            .eq("status", "waiting")
            .order("requested_at")
            .execute()
        )
        entries = result.data or []

        for entry in entries[:3]:  # Notify up to 3 users
            user = entry.get("users") or {}
            platform = entry.get("platforms") or {}
            telegram_id = user.get("telegram_id")
            if not telegram_id:
                continue

            message = (
                f"🎉 <b>¡Buenas noticias!</b>\n\n"
                f"Ya hay disponibilidad en <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b>.\n"
                f"¡Date prisa! Los slots Express se llenan rápido 🏃\n\n"
                f"Usa /start para hacer tu pedido."
            )
            await send_to_user(telegram_id, message)
            notified_count += 1

            # Update queue entry
            sb.table("express_queue").update({
                "status": "notified",
                "notified_at": venezuela_now().isoformat(),
            }).eq("id", entry["id"]).execute()

            await asyncio.sleep(0.3)
    except Exception as e:
        logger.error(f"Error in notify_express_queue: {e}")

    return notified_count


async def send_expiry_reminder(subscription_id: str) -> bool:
    """Send 3-day expiry reminder to user.
    If the user has no telegram_id (external client), notifies admin instead."""
    from database.subscriptions import get_subscription_by_id, mark_reminder_sent
    from bot.messages import EXPIRY_REMINDER_3DAYS

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False

        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")

        from datetime import datetime
        import pytz
        end_date_str = sub.get("end_date")
        if end_date_str:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_left = days_remaining(end_dt)
        else:
            days_left = 3
            end_dt = None

        # External client: no telegram_id — notify admin instead
        if not telegram_id:
            client_name = user.get("name") or "Sin nombre"
            client_contact = user.get("phone") or user.get("notes") or "Sin contacto"
            platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
            end_fmt = format_datetime_vzla(end_dt) if end_dt else "pronto"
            message = (
                f"⏰ <b>Recordatorio — Cliente Externo</b>\n\n"
                f"El siguiente cliente sin Telegram está por vencer en <b>{days_left} día(s)</b>.\n"
                f"Notifícalo por su medio de contacto.\n\n"
                f"👤 <b>Cliente:</b> {client_name}\n"
                f"📞 <b>Contacto:</b> {client_contact}\n"
                f"📺 <b>Plataforma:</b> {platform_label}\n"
                f"📅 <b>Plan:</b> {sub.get('plan_type', 'mensual')}\n"
                f"📆 <b>Vence:</b> {end_fmt}\n\n"
                f"<i>Este cliente no tiene Telegram. Avísale tú directamente.</i>"
            )
            success = await send_to_admin(message)
            if success:
                await mark_reminder_sent(subscription_id)
            return bool(success)

        message = EXPIRY_REMINDER_3DAYS.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
            days=days_left,
            end_date=format_datetime_vzla(end_dt) if end_date_str else "pronto",
        )

        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(sub.get("platform_id", ""), sub.get("plan_type", "monthly"))
        success = await send_to_user(telegram_id, message, keyboard)
        if success:
            await mark_reminder_sent(subscription_id)
        return success
    except Exception as e:
        logger.error(f"Error in send_expiry_reminder: {e}")
        return False


async def send_expiry_notification(subscription_id: str) -> bool:
    """Send expiry notification to user.
    If the user has no telegram_id (external client), notifies admin instead."""
    from database.subscriptions import get_subscription_by_id, mark_expiry_notified
    from bot.messages import EXPIRY_NOTIFICATION

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False

        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")

        # External client: notify admin
        if not telegram_id:
            client_name = user.get("name") or "Sin nombre"
            client_contact = user.get("phone") or user.get("notes") or "Sin contacto"
            platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
            end_date_str = sub.get("end_date", "")
            end_fmt = end_date_str[:10] if end_date_str else "N/A"
            message = (
                f"🔴 <b>Suscripción Vencida — Cliente Externo</b>\n\n"
                f"La suscripción del siguiente cliente sin Telegram acaba de vencer.\n"
                f"Notifícalo por su medio de contacto para que renueve.\n\n"
                f"👤 <b>Cliente:</b> {client_name}\n"
                f"📞 <b>Contacto:</b> {client_contact}\n"
                f"📺 <b>Plataforma:</b> {platform_label}\n"
                f"📅 <b>Plan:</b> {sub.get('plan_type', 'mensual')}\n"
                f"📆 <b>Venció:</b> {end_fmt}\n\n"
                f"<i>Tiene 6 días de gracia antes de que se libere el perfil.</i>"
            )
            success = await send_to_admin(message)
            if success:
                await mark_expiry_notified(subscription_id)
            return bool(success)

        message = EXPIRY_NOTIFICATION.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
        )

        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(sub.get("platform_id", ""), sub.get("plan_type", "monthly"))
        success = await send_to_user(telegram_id, message, keyboard)
        if success:
            await mark_expiry_notified(subscription_id)
        return success
    except Exception as e:
        logger.error(f"Error in send_expiry_notification: {e}")
        return False


async def send_debt_reminder(sub: dict, day_number: int) -> bool:
    """Send a daily debt reminder (day 1-6 after expiration).
    If the user has no telegram_id (external client), notifies admin instead."""
    from bot.messages import DEBT_REMINDER
    from utils.helpers import format_datetime_vzla

    try:
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")

        # External client: notify admin
        if not telegram_id:
            client_name = user.get("name") or "Sin nombre"
            client_contact = user.get("phone") or user.get("notes") or "Sin contacto"
            platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
            end_date_str = sub.get("end_date", "")
            end_fmt = end_date_str[:10] if end_date_str else "N/A"
            days_left = 6 - day_number
            if days_left > 1:
                urgency = f"Quedan {days_left} días antes del corte."
            elif days_left == 1:
                urgency = "¡Mañana se libera el perfil si no renueva!"
            else:
                urgency = "¡Hoy es el último día! El perfil se libera si no renueva."
            message = (
                f"⚠️ <b>Recordatorio de Deuda #{day_number} — Cliente Externo</b>\n\n"
                f"El siguiente cliente sin Telegram aún no ha renovado. {urgency}\n\n"
                f"👤 <b>Cliente:</b> {client_name}\n"
                f"📞 <b>Contacto:</b> {client_contact}\n"
                f"📺 <b>Plataforma:</b> {platform_label}\n"
                f"📆 <b>Venció:</b> {end_fmt}\n\n"
                f"<i>Contacta al cliente por su medio externo para cobrar la renovación.</i>"
            )
            return bool(await send_to_admin(message))

        if not telegram_id:
            return False

        end_date_str = sub.get("end_date", "")
        end_dt = None
        if end_date_str:
            try:
                from datetime import datetime
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        end_date_fmt = format_datetime_vzla(end_dt) if end_dt else "N/A"
        platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()

        days_left = 6 - day_number
        if days_left > 1:
            urgency_line = f"⏳ Te quedan <b>{days_left} días</b> antes de que se libere tu perfil."
        elif days_left == 1:
            urgency_line = "🚨 <b>¡Último día!</b> Mañana se liberará tu perfil si no renuevas."
        else:
            urgency_line = "🚨 <b>¡Hoy es el último día!</b> Tu perfil se liberará si no renuevas."

        message = DEBT_REMINDER.format(
            day=day_number,
            name=user.get("name", ""),
            platform=platform_label,
            end_date=end_date_fmt,
            urgency_line=urgency_line,
        )

        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(str(sub.get("platform_id", "")), "monthly")
        return await send_to_user(telegram_id, message, keyboard)
    except Exception as e:
        logger.error(f"Error in send_debt_reminder: {e}")
        return False


async def send_hard_cut_notification(sub: dict) -> bool:
    """Notify user their subscription was cut after grace period.
    If the user has no telegram_id (external client), notifies admin instead."""
    from bot.messages import HARD_CUT_NOTIFICATION
    from utils.helpers import format_datetime_vzla

    try:
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")

        # External client: notify admin
        if not telegram_id:
            client_name = user.get("name") or "Sin nombre"
            client_contact = user.get("phone") or user.get("notes") or "Sin contacto"
            platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
            end_date_str = sub.get("end_date", "")
            end_fmt = end_date_str[:10] if end_date_str else "N/A"
            message = (
                f"🔴 <b>Corte Ejecutado — Cliente Externo</b>\n\n"
                f"El perfil del siguiente cliente fue liberado por falta de pago.\n\n"
                f"👤 <b>Cliente:</b> {client_name}\n"
                f"📞 <b>Contacto:</b> {client_contact}\n"
                f"📺 <b>Plataforma:</b> {platform_label}\n"
                f"📆 <b>Venció:</b> {end_fmt}\n\n"
                f"<i>El perfil ya está disponible para otro cliente. "
                f"Si desea reactivar, usa /afiliar de nuevo.</i>"
            )
            return bool(await send_to_admin(message))

        if not telegram_id:
            return False

        end_date_str = sub.get("end_date", "")
        end_dt = None
        if end_date_str:
            try:
                from datetime import datetime
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        from bot.keyboards import main_menu_keyboard
        message = HARD_CUT_NOTIFICATION.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip(),
            end_date=format_datetime_vzla(end_dt) if end_dt else "N/A",
        )
        return await send_to_user(telegram_id, message, main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error in send_hard_cut_notification: {e}")
        return False


async def send_express_expired(subscription_id: str) -> bool:
    """Send express expiry notification + upsell."""
    from database.subscriptions import get_subscription_by_id
    from bot.messages import EXPRESS_EXPIRED

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False

        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        if not telegram_id:
            return False

        message = EXPRESS_EXPIRED.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
        )

        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(sub.get("platform_id", ""), "monthly")
        return await send_to_user(telegram_id, message, keyboard)
    except Exception as e:
        logger.error(f"Error in send_express_expired: {e}")
        return False


async def send_soft_cut_notification(subscription_id: str) -> bool:
    """Notify user that their PIN was changed (soft cut)."""
    from database.subscriptions import get_subscription_by_id
    from bot.messages import SOFT_CUT_NOTIFICATION

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        if not telegram_id:
            return False
        _ed = (sub.get("end_date") or "")[:10]
        end_fmt = f"{_ed[8:10]}/{_ed[5:7]}/{_ed[0:4]}" if len(_ed) == 10 else _ed
        message = SOFT_CUT_NOTIFICATION.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
            end_date=end_fmt,
        )
        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(sub.get("platform_id", ""), sub.get("plan_type", "monthly"))
        return await send_to_user(telegram_id, message, keyboard)
    except Exception as e:
        logger.error(f"Error in send_soft_cut_notification: {e}")
        return False


async def send_profile_released_notification(subscription_id: str) -> bool:
    """Notify user that their profile was released (hard cut)."""
    from database.subscriptions import get_subscription_by_id
    from bot.messages import PROFILE_RELEASED_NOTIFICATION

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        if not telegram_id:
            return False
        message = PROFILE_RELEASED_NOTIFICATION.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
        )
        from bot.keyboards import renewal_keyboard
        keyboard = renewal_keyboard(sub.get("platform_id", ""), sub.get("plan_type", "monthly"))
        return await send_to_user(telegram_id, message, keyboard)
    except Exception as e:
        logger.error(f"Error in send_profile_released_notification: {e}")
        return False
