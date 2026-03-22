from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import platforms_keyboard, confirm_order_keyboard, main_menu_keyboard
from bot.messages import PAYMENT_INSTRUCTIONS
from bot.middleware import (
    get_user_state, set_user_state, clear_user_state,
    get_user_data, set_user_data, clear_user_data
)
from database.analytics import get_platform_availability
from database.platforms import get_platform_by_id
from database.subscriptions import create_subscription
from database.users import get_user_by_telegram_id
from services.exchange_service import calculate_price_bs, get_current_rate
from services.payment_service import get_payment_config
from utils.helpers import venezuela_now, short_id

logger = logging.getLogger(__name__)


async def show_week_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show platform selection for weekly plan."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        availability = await get_platform_availability()

        platform_list_text = ""
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            count = p.get("week_available", 0) or p.get("monthly_available", 0)
            status = f"{count} disponible{'s' if count != 1 else ''}" if count > 0 else "Sin stock"
            platform_list_text += f"{icon} <b>{name}</b> - {status}\n"

        week_text = (
            "📅 <b>Pack Semanal</b>\n\n"
            "Acceso completo por 7 días 🗓️\n\n"
            f"Plataformas disponibles:\n\n{platform_list_text}\n\n"
            "💡 <i>Ideal para maratonear series o ver contenido nuevo</i>"
        )

        await query.edit_message_text(
            week_text,
            parse_mode="HTML",
            reply_markup=platforms_keyboard(availability, "week"),
        )
    except Exception as e:
        logger.error(f"Error in show_week_platforms: {e}")
        await query.edit_message_text("Error al cargar plataformas.")


async def handle_week_platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle weekly plan platform selection."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    platform_id = parts[2]

    try:
        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.answer("Plataforma no encontrada", show_alert=True)
            return

        price_usd = float(platform.get("week_price_usd") or platform.get("monthly_price_usd") or 2.50)
        price_bs = await calculate_price_bs(price_usd)
        rate = await get_current_rate()
        rate_value = float((rate or {}).get("usd_binance") or 36.0)

        set_user_data(telegram_id, "selected_platform_id", platform_id)
        set_user_data(telegram_id, "selected_plan_type", "week")
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_value))
        set_user_state(telegram_id, "confirm_order:week")

        confirm_text = (
            f"📅 <b>Confirmar Pack Semanal</b>\n\n"
            f"📺 Plataforma: <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b>\n"
            f"⏰ Duración: <b>7 días</b>\n"
            f"💵 Precio: <b>${price_usd:.2f} USD</b> = <b>Bs {price_bs:,.2f}</b>\n"
            f"📊 Tasa: Bs {rate_value:.2f}/USD\n\n"
            f"¿Confirmamos?"
        )

        await query.edit_message_text(
            confirm_text,
            parse_mode="HTML",
            reply_markup=confirm_order_keyboard(platform_id, "week"),
        )
    except Exception as e:
        logger.error(f"Error in handle_week_platform_selected: {e}")
        await query.edit_message_text("Error al procesar. Intenta de nuevo.")


async def handle_week_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle week pack order confirmation."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        platform_id = get_user_data(telegram_id, "selected_platform_id")
        price_usd = float(get_user_data(telegram_id, "price_usd") or 2.50)
        price_bs = float(get_user_data(telegram_id, "price_bs") or 90.0)
        rate_used = float(get_user_data(telegram_id, "rate_used") or 36.0)

        if not platform_id:
            await query.edit_message_text("Sesión expirada. Usa /start.")
            return

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Error de usuario. Usa /start.")
            return

        now = venezuela_now()
        end_date = now + timedelta(days=7)

        sub = await create_subscription(
            user_id=str(user["id"]),
            platform_id=platform_id,
            plan_type="week",
            price_usd=price_usd,
            price_bs=price_bs,
            rate_used=rate_used,
            end_date=end_date,
        )

        if not sub:
            await query.edit_message_text("Error al crear el pedido. Intenta de nuevo.")
            return

        sub_id = str(sub["id"])
        set_user_data(telegram_id, "current_sub_id", sub_id)
        set_user_state(telegram_id, "awaiting_payment")

        payment_cfg = await get_payment_config()
        if not payment_cfg:
            await query.edit_message_text("Error de configuración. Contacta a soporte.")
            return

        payment_text = PAYMENT_INSTRUCTIONS.format(
            banco=payment_cfg.get("banco", "N/A"),
            telefono=payment_cfg.get("telefono", "N/A"),
            cedula=payment_cfg.get("cedula", "N/A"),
            titular=payment_cfg.get("titular", "N/A"),
            amount_bs=f"{price_bs:,.2f}",
            order_id=short_id(sub_id),
        )

        await query.edit_message_text(payment_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in handle_week_confirmed: {e}")
        await query.edit_message_text("Error al procesar. Intenta de nuevo.")
