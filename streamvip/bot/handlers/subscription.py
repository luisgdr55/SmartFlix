from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    platforms_keyboard, confirm_order_keyboard, payment_received_keyboard, main_menu_keyboard
)
from bot.messages import (
    SUBSCRIPTION_PLATFORM_SELECT, SUBSCRIPTION_CONFIRM, PAYMENT_INSTRUCTIONS,
    PAYMENT_CONFIRMED, ACCESS_DELIVERED, ACCESS_INSTRUCTIONS, PIN_LINE, PAYMENT_INVALID
)
from bot.middleware import (
    get_user_state, set_user_state, clear_user_state,
    get_user_data, set_user_data, clear_user_data
)
from database.users import get_user_by_telegram_id
from database.platforms import get_platform_by_id
from database.accounts import get_account_by_id
from database.profiles import get_available_profiles, assign_profile
from database.subscriptions import create_subscription, confirm_subscription
from database.analytics import get_platform_availability
from services.exchange_service import calculate_price_bs, get_current_rate
from services.payment_service import validate_payment, get_payment_config
from utils.helpers import venezuela_now, short_id, format_datetime_vzla

logger = logging.getLogger(__name__)


async def show_subscription_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show platform selection for monthly subscription."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        availability = await get_platform_availability()
        monthly_available = [p for p in availability if p.get("monthly_available", 0) > 0]

        platform_list_text = ""
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            count = p.get("monthly_available", 0)
            platform_list_text += f"{icon} <b>{name}</b> - {count} disponible{'s' if count != 1 else ''}\n"

        await query.edit_message_text(
            SUBSCRIPTION_PLATFORM_SELECT.format(platform_list=platform_list_text),
            parse_mode="HTML",
            reply_markup=platforms_keyboard(availability, "monthly"),
        )
    except Exception as e:
        logger.error(f"Error in show_subscription_platforms: {e}")
        await query.edit_message_text("Error al cargar plataformas. Intenta de nuevo.")


async def handle_platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle platform selection callback for monthly plan."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id
    # callback_data format: platform:monthly:{platform_id}
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    plan_type = parts[1]
    platform_id = parts[2]

    try:
        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.answer("Plataforma no encontrada", show_alert=True)
            return

        # Get price
        price_field = {
            "monthly": "monthly_price_usd",
            "express": "express_price_usd",
            "week": "week_price_usd",
        }.get(plan_type, "monthly_price_usd")
        price_usd = float(platform.get(price_field) or 4.50)
        price_bs = await calculate_price_bs(price_usd)
        rate = await get_current_rate()
        rate_value = float((rate or {}).get("usd_binance") or 36.0)

        # Store selection in Redis
        set_user_data(telegram_id, "selected_platform_id", platform_id)
        set_user_data(telegram_id, "selected_plan_type", plan_type)
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_value))
        set_user_state(telegram_id, f"confirm_order:{plan_type}")

        plan_label = {"monthly": "Mensual (30 días)", "express": "Express (24h)", "week": "Semanal (7 días)"}.get(plan_type, plan_type)

        confirm_text = SUBSCRIPTION_CONFIRM.format(
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
            price_usd=f"${price_usd:.2f}",
            price_bs=f"Bs {price_bs:,.2f}",
            rate=f"{rate_value:.2f}",
        ).replace("Mensual (30 días)", plan_label)

        await query.edit_message_text(
            confirm_text,
            parse_mode="HTML",
            reply_markup=confirm_order_keyboard(platform_id, plan_type),
        )
    except Exception as e:
        logger.error(f"Error in handle_platform_selected: {e}")
        await query.edit_message_text("Error al procesar selección. Intenta de nuevo.")


async def handle_order_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle order confirmation and show payment instructions."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        platform_id = get_user_data(telegram_id, "selected_platform_id")
        plan_type = get_user_data(telegram_id, "selected_plan_type") or "monthly"
        price_usd = float(get_user_data(telegram_id, "price_usd") or 4.50)
        price_bs = float(get_user_data(telegram_id, "price_bs") or 150.0)
        rate_used = float(get_user_data(telegram_id, "rate_used") or 36.0)

        if not platform_id:
            await query.edit_message_text("Sesión expirada. Usa /start para comenzar de nuevo.")
            return

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Error al obtener tu perfil. Usa /start.")
            return

        # Calculate end date based on plan
        now = venezuela_now()
        durations = {"monthly": 30, "express": 1, "week": 7}
        end_date = now + timedelta(days=durations.get(plan_type, 30))

        # Create pending subscription
        sub = await create_subscription(
            user_id=str(user["id"]),
            platform_id=platform_id,
            plan_type=plan_type,
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

        # Get payment config
        payment_cfg = await get_payment_config()
        if not payment_cfg:
            await query.edit_message_text("Error al obtener datos de pago. Contacta a soporte.")
            return

        payment_text = PAYMENT_INSTRUCTIONS.format(
            banco=payment_cfg.get("banco", "N/A"),
            telefono=payment_cfg.get("telefono", "N/A"),
            cedula=payment_cfg.get("cedula", "N/A"),
            titular=payment_cfg.get("titular", "N/A"),
            amount_bs=f"{price_bs:,.2f}",
            order_id=short_id(sub_id),
        )

        await query.edit_message_text(
            payment_text,
            parse_mode="HTML",
        )

        # Set 30-minute payment timer in Redis
        from bot.middleware import set_user_data as sdata
        sdata(telegram_id, "payment_expires_at", str(int((now + timedelta(minutes=30)).timestamp())))

    except Exception as e:
        logger.error(f"Error in handle_order_confirmed: {e}")
        await query.edit_message_text("Error al procesar el pedido. Intenta de nuevo.")


async def handle_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process payment comprobante photo for subscription flow."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    state = get_user_state(telegram_id)

    if state != "awaiting_payment":
        return

    try:
        sub_id = get_user_data(telegram_id, "current_sub_id")
        price_bs_str = get_user_data(telegram_id, "price_bs")
        platform_id = get_user_data(telegram_id, "selected_platform_id")
        plan_type = get_user_data(telegram_id, "selected_plan_type") or "monthly"

        if not sub_id or not price_bs_str or not platform_id:
            await update.message.reply_text("Sesión expirada. Usa /start para iniciar un nuevo pedido.")
            clear_user_state(telegram_id)
            return

        price_bs = float(price_bs_str)

        # Download photo
        photo = update.message.photo[-1]  # Largest photo
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        await update.message.reply_text("⏳ Verificando tu comprobante de pago...")

        # Validate payment
        result = await validate_payment(bytes(image_bytes), price_bs, sub_id)

        if result.get("valid") is False:
            reason_map = {
                "duplicate_image": "Comprobante duplicado",
                "not_comprobante": "No es un comprobante válido",
                "amount_mismatch": result.get("message", "Monto incorrecto"),
                "duplicate_reference": result.get("message", "Referencia duplicada"),
                "payment_too_old": "Comprobante con más de 60 minutos",
            }
            reason = reason_map.get(result.get("reason", ""), result.get("message", "Pago inválido"))
            await update.message.reply_text(
                PAYMENT_INVALID.format(reason=reason, amount_bs=f"{price_bs:,.2f}"),
                parse_mode="HTML",
                reply_markup=payment_received_keyboard(),
            )
            return

        # Save proof and notify admin for manual approval
        payment_image_url = f"tg://photo/{photo.file_id}"
        reference = result.get("reference") or result.get("data", {}).get("referencia") or "N/A"

        from database.subscriptions import save_payment_proof
        await save_payment_proof(sub_id, reference, payment_image_url)

        # Tell client to wait
        user = await get_user_by_telegram_id(telegram_id)
        platform = await get_platform_by_id(platform_id)
        plan_label = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}.get(plan_type, plan_type)
        client_name = (user or {}).get("name", "") if user else ""

        await update.message.reply_text(
            f"⏳ <b>Comprobante recibido</b>\n\n"
            f"Hola <b>{client_name}</b>, tu comprobante está siendo revisado por nuestro equipo.\n\n"
            f"📺 <b>{(platform or {}).get('name','')}</b> — Plan {plan_label}\n"
            f"🔖 Ref: <code>{reference}</code>\n\n"
            f"En breve recibirás tus datos de acceso. ¡Gracias por tu paciencia! 🙏",
            parse_mode="HTML",
        )

        # Notify admin with photo + approve/reject buttons
        from services.notification_service import send_to_admin
        from bot.keyboards import pending_payment_keyboard

        price_usd = float(get_user_data(telegram_id, "price_usd") or 0)
        admin_caption = (
            f"💳 <b>Nuevo comprobante de pago</b>\n\n"
            f"👤 Cliente: <b>{client_name}</b> (TG: <code>{telegram_id}</code>)\n"
            f"📺 Plataforma: <b>{(platform or {}).get('name','')}</b>\n"
            f"📅 Plan: <b>{plan_label}</b>\n"
            f"💵 Monto: <b>${price_usd:.2f} USD</b>\n"
            f"🔖 Referencia: <code>{reference}</code>\n"
            f"🆔 Pedido: <code>#{short_id(sub_id)}</code>\n\n"
            f"Revisa el comprobante y aprueba o rechaza:"
        )
        await send_to_admin(admin_caption, keyboard=pending_payment_keyboard(sub_id), photo_bytes=bytes(image_bytes))

        # Clear state
        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

    except Exception as e:
        logger.error(f"Error in handle_payment_photo: {e}")
        await update.message.reply_text(
            "Error al procesar tu comprobante. Por favor intenta nuevamente o contacta a soporte.",
            reply_markup=payment_received_keyboard(),
        )
