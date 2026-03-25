from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import platforms_keyboard, confirm_order_keyboard, express_no_stock_keyboard, main_menu_keyboard
from bot.messages import EXPRESS_PLATFORM_SELECT, EXPRESS_NO_STOCK, EXPRESS_DELIVERED, ACCESS_DELIVERED, ACCESS_INSTRUCTIONS, PIN_LINE
from bot.middleware import (
    get_user_state, set_user_state, clear_user_state,
    get_user_data, set_user_data, clear_user_data
)
from database.analytics import get_platform_availability
from database.platforms import get_platform_by_id
from database.profiles import get_available_profiles, assign_profile
from database.subscriptions import create_subscription, confirm_subscription
from database.users import get_user_by_telegram_id
from database.accounts import get_account_by_id
from services.exchange_service import calculate_price_bs, get_current_rate
from services.payment_service import validate_payment, get_payment_config
from utils.helpers import venezuela_now, short_id, format_datetime_vzla

logger = logging.getLogger(__name__)


async def show_express_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show platform selection for express 24h plan."""
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
            count = p.get("express_available", 0)
            status = f"{count} disponible{'s' if count != 1 else ''}" if count > 0 else "Sin stock"
            platform_list_text += f"{icon} <b>{name}</b> - {status}\n"

        await query.edit_message_text(
            EXPRESS_PLATFORM_SELECT.format(platform_list=platform_list_text),
            parse_mode="HTML",
            reply_markup=platforms_keyboard(availability, "express"),
        )
    except Exception as e:
        logger.error(f"Error in show_express_platforms: {e}")
        await query.edit_message_text("Error al cargar plataformas. Intenta de nuevo.")


async def handle_express_platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle express platform selection."""
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

        # Check express stock
        profiles = await get_available_profiles(platform_id, "express")
        if not profiles:
            await query.edit_message_text(
                EXPRESS_NO_STOCK.format(platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}"),
                parse_mode="HTML",
                reply_markup=express_no_stock_keyboard(platform_id),
            )
            return

        price_usd = float(platform.get("express_price_usd") or 1.00)
        price_bs = await calculate_price_bs(price_usd)
        rate = await get_current_rate()
        rate_value = float((rate or {}).get("usd_binance") or 36.0)

        set_user_data(telegram_id, "selected_platform_id", platform_id)
        set_user_data(telegram_id, "selected_plan_type", "express")
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_value))
        set_user_state(telegram_id, "confirm_order:express")

        confirm_text = (
            f"⚡ <b>Confirmar Pedido Express</b>\n\n"
            f"📺 Plataforma: <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b>\n"
            f"⏰ Duración: <b>24 horas</b>\n"
            f"💵 Precio: <b>${price_usd:.2f} USD</b> = <b>Bs {price_bs:,.2f}</b>\n"
            f"📊 Tasa: Bs {rate_value:.2f}/USD\n\n"
            f"¿Confirmamos?"
        )

        await query.edit_message_text(
            confirm_text,
            parse_mode="HTML",
            reply_markup=confirm_order_keyboard(platform_id, "express"),
        )
    except Exception as e:
        logger.error(f"Error in handle_express_platform_selected: {e}")
        await query.edit_message_text("Error al procesar. Intenta de nuevo.")


async def handle_express_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle express order confirmation."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        # Extract platform_id from callback data first (survives Redis expiry)
        cb_parts = (query.data or "").split(":")
        platform_id = cb_parts[2] if len(cb_parts) > 2 else get_user_data(telegram_id, "selected_platform_id")

        if not platform_id:
            await query.edit_message_text("Sesión expirada. Usa /start para comenzar.")
            return

        # Try Redis for price data; recalculate if missing
        price_usd_str = get_user_data(telegram_id, "price_usd")
        price_bs_str  = get_user_data(telegram_id, "price_bs")
        rate_str      = get_user_data(telegram_id, "rate_used")

        if price_usd_str and price_bs_str and rate_str:
            price_usd = float(price_usd_str)
            price_bs  = float(price_bs_str)
            rate_used = float(rate_str)
        else:
            platform_tmp = await get_platform_by_id(platform_id)
            price_usd = float((platform_tmp or {}).get("express_price_usd") or 1.00)
            price_bs  = await calculate_price_bs(price_usd)
            rate_obj  = await get_current_rate()
            rate_used = float((rate_obj or {}).get("usd_binance") or 36.0)

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Error de usuario. Usa /start.")
            return

        now = venezuela_now()
        end_date = now + timedelta(hours=24)

        sub = await create_subscription(
            user_id=str(user["id"]),
            platform_id=platform_id,
            plan_type="express",
            price_usd=price_usd,
            price_bs=price_bs,
            rate_used=rate_used,
            end_date=end_date,
        )

        if not sub:
            await query.edit_message_text("Error al crear el pedido. Intenta de nuevo.")
            return

        sub_id = str(sub["id"])
        # Refresh all session data so handle_payment_photo always has what it needs
        set_user_data(telegram_id, "current_sub_id", sub_id)
        set_user_data(telegram_id, "selected_platform_id", platform_id)
        set_user_data(telegram_id, "selected_plan_type", "express")
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_used))
        set_user_state(telegram_id, "awaiting_payment")

        payment_cfg = await get_payment_config()
        if not payment_cfg:
            await query.edit_message_text("Error de configuración. Contacta a soporte.")
            return

        from bot.messages import PAYMENT_INSTRUCTIONS
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
        logger.error(f"Error in handle_express_confirmed: {e}")
        await query.edit_message_text("Error al procesar. Intenta de nuevo.")


async def handle_queue_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle joining express queue for a platform."""
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
        from database.users import get_user_by_telegram_id
        from database import get_supabase
        from utils.helpers import venezuela_now

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.answer("Error al obtener usuario", show_alert=True)
            return

        sb = get_supabase()
        # Check if already in queue
        existing = (
            sb.table("express_queue")
            .select("id")
            .eq("user_id", str(user["id"]))
            .eq("platform_id", platform_id)
            .eq("status", "waiting")
            .execute()
        )

        if existing.data:
            await query.answer("Ya estás en la lista de espera", show_alert=True)
            return

        now = venezuela_now()
        sb.table("express_queue").insert({
            "user_id": str(user["id"]),
            "platform_id": platform_id,
            "status": "waiting",
            "expires_at": (now + timedelta(hours=24)).isoformat(),
        }).execute()

        platform = await get_platform_by_id(platform_id)
        platform_name = (platform or {}).get("name", "la plataforma")

        await query.edit_message_text(
            f"✅ <b>¡Te has unido a la lista de espera!</b>\n\n"
            f"Te notificaremos cuando haya disponibilidad Express en <b>{platform_name}</b>.\n\n"
            f"La notificación estará activa por 24 horas. 🔔",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in handle_queue_join: {e}")
        await query.answer("Error al unirse a la cola", show_alert=True)
