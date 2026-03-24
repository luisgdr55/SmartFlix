from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import get_user_state

logger = logging.getLogger(__name__)


async def handle_payment_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main photo handler - routes to correct flow based on user state.
    Falls back to DB lookup if Redis state is missing or expired.
    """
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    state = get_user_state(telegram_id)

    if state == "awaiting_payment":
        from bot.handlers.subscription import handle_payment_photo
        await handle_payment_photo(update, context)
        return

    if state == "awaiting_cart_payment":
        from bot.handlers.subscription import handle_cart_payment_photo
        await handle_cart_payment_photo(update, context)
        return

    # State missing or expired — check DB for a pending_payment subscription
    try:
        from database.users import get_user_by_telegram_id
        from database.subscriptions import get_user_pending_subscription

        user = await get_user_by_telegram_id(telegram_id)
        if user:
            pending = await get_user_pending_subscription(str(user["id"]))
            if pending:
                # Restore minimal state so handle_payment_photo can find the sub
                from bot.middleware import set_user_state, set_user_data
                sub_id = str(pending["id"])
                platform = pending.get("platforms") or {}
                set_user_state(telegram_id, "awaiting_payment")
                set_user_data(telegram_id, "current_sub_id", sub_id)
                set_user_data(telegram_id, "selected_platform_id", str(pending.get("platform_id", "")))
                set_user_data(telegram_id, "selected_plan_type", pending.get("plan_type", "monthly"))
                set_user_data(telegram_id, "price_bs", str(pending.get("price_bs", "0")))
                set_user_data(telegram_id, "price_usd", str(pending.get("price_usd", "0")))
                logger.info(f"Restored payment state from DB for user {telegram_id}, sub {sub_id}")
                from bot.handlers.subscription import handle_payment_photo
                await handle_payment_photo(update, context)
                return

    except Exception as e:
        logger.error(f"Error in payment_image DB fallback: {e}")

    # No pending subscription found at all
    await update.message.reply_text(
        "Para enviar un comprobante primero debes realizar un pedido.\n"
        "Usa /start para ver el menú. 📋"
    )
