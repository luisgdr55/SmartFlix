from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu_keyboard
from bot.messages import MAIN_MENU
from database.users import get_user_by_telegram_id, update_user_last_seen
from database.analytics import get_platform_availability

logger = logging.getLogger(__name__)


async def _build_availability_text() -> str:
    """Build availability summary text for menu."""
    try:
        availability = await get_platform_availability()
        lines = []
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            monthly = p.get("monthly_available", 0)
            express = p.get("express_available", 0)
            if monthly > 0 or express > 0:
                lines.append(f"{icon} {name}: {monthly} mensual | {express} express")
            else:
                lines.append(f"{icon} {name}: Sin stock")
        return "\n".join(lines) if lines else "Consultando disponibilidad..."
    except Exception:
        return "Ver disponibilidad disponible"


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the main menu with real-time availability."""
    query = update.callback_query
    message = update.message
    effective_user = update.effective_user

    if not effective_user:
        return

    telegram_id = effective_user.id
    await update_user_last_seen(telegram_id)

    try:
        user = await get_user_by_telegram_id(telegram_id)
        name = (user or {}).get("name") or effective_user.first_name or "amigo/a"
        availability = await _build_availability_text()
        menu_text = MAIN_MENU.format(name=name, availability=availability)

        if query:
            await query.answer()
            await query.edit_message_text(
                menu_text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        elif message:
            await message.reply_text(
                menu_text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error in show_main_menu: {e}")
        if query:
            await query.answer("Error al cargar menú")
