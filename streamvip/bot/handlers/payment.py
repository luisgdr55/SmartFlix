from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import get_user_state

logger = logging.getLogger(__name__)


async def handle_payment_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main photo handler - routes to correct flow based on user state.
    """
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    state = get_user_state(telegram_id)

    if not state:
        # No active state - ignore photo
        return

    if state == "awaiting_payment":
        from bot.handlers.subscription import handle_payment_photo
        await handle_payment_photo(update, context)
    else:
        # Unknown state with photo
        await update.message.reply_text(
            "No estaba esperando un comprobante en este momento. "
            "Si quieres hacer un pedido, usa el /start.",
        )
