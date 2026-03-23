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


def get_bot() -> Bot:
    global _bot
    if _bot is None:
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
    """Send 3-day expiry reminder to user."""
    from database.subscriptions import get_subscription_by_id, mark_reminder_sent
    from bot.messages import EXPIRY_REMINDER_3DAYS

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False

        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        if not telegram_id:
            return False

        from datetime import datetime
        import pytz
        end_date_str = sub.get("end_date")
        if end_date_str:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_left = days_remaining(end_dt)
        else:
            days_left = 3

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
    """Send expiry notification to user."""
    from database.subscriptions import get_subscription_by_id, mark_expiry_notified
    from bot.messages import EXPIRY_NOTIFICATION

    try:
        sub = await get_subscription_by_id(subscription_id)
        if not sub:
            return False

        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        if not telegram_id:
            return False

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
        end_raw = (sub.get("end_date") or "")[:10]
        message = SOFT_CUT_NOTIFICATION.format(
            name=user.get("name", ""),
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
            end_date=end_raw,
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
