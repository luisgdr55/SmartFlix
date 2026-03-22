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

        # Get available profile
        profiles = await get_available_profiles(platform_id, plan_type)
        if not profiles:
            await update.message.reply_text(
                "⚠️ No hay perfiles disponibles en este momento. "
                "Tu pago fue recibido y te asignaremos un perfil pronto. "
                "Por favor espera o contacta a soporte.",
                reply_markup=payment_received_keyboard(),
            )
            # Notify admin
            from services.notification_service import send_to_admin
            await send_to_admin(
                f"⚠️ <b>Sin perfiles disponibles</b>\n"
                f"Usuario: {telegram_id}\n"
                f"Suscripción: #{short_id(sub_id)}\n"
                f"Platform ID: {platform_id}\n"
                f"Plan: {plan_type}",
            )
            return

        profile = profiles[0]
        profile_id = str(profile["id"])

        # Upload payment image URL (simplified - store reference)
        payment_image_url = f"tg://photo/{photo.file_id}"
        reference = result.get("reference") or result.get("data", {}).get("referencia") or "N/A"

        # Confirm subscription
        await confirm_subscription(sub_id, profile_id, reference, payment_image_url)
        await assign_profile(profile_id)

        # Update user purchase count
        user = await get_user_by_telegram_id(telegram_id)
        if user:
            from database.users import increment_user_purchases
            await increment_user_purchases(telegram_id)

        # Get full data for access delivery
        platform = await get_platform_by_id(platform_id)
        account = await get_account_by_id(str(profile.get("account_id", "")))

        from datetime import datetime
        import pytz
        now = venezuela_now()
        durations = {"monthly": 30, "express": 1, "week": 7}
        end_date = now + timedelta(days=durations.get(plan_type, 30))

        pin_line = PIN_LINE.format(pin=profile.get("pin")) if profile.get("pin") else ""
        platform_slug = (platform or {}).get("slug", "netflix")
        instructions_tpl = ACCESS_INSTRUCTIONS.get(platform_slug, ACCESS_INSTRUCTIONS.get("netflix", ""))
        instructions = instructions_tpl.format(profile_name=profile.get("profile_name", ""))

        access_text = ACCESS_DELIVERED.format(
            platform=f"{(platform or {}).get('icon_emoji', '')} {(platform or {}).get('name', '')}",
            profile_name=profile.get("profile_name", ""),
            email=(account or {}).get("email", ""),
            password=(account or {}).get("password", ""),
            pin_line=pin_line,
            instructions=instructions,
        )

        confirmed_text = PAYMENT_CONFIRMED.format(
            platform=f"{(platform or {}).get('icon_emoji', '')} {(platform or {}).get('name', '')}",
            start_date=format_datetime_vzla(now),
            end_date=format_datetime_vzla(end_date),
            reference=reference,
        )

        await update.message.reply_text(confirmed_text, parse_mode="HTML")
        await update.message.reply_text(access_text, parse_mode="HTML")

        # Notify admin
        from services.notification_service import send_to_admin
        await send_to_admin(
            f"✅ <b>Nuevo pago confirmado</b>\n"
            f"👤 Usuario: {telegram_id}\n"
            f"📺 Plataforma: {(platform or {}).get('name', '')}\n"
            f"💵 Monto: ${float(get_user_data(telegram_id, 'price_usd') or 0):.2f} USD\n"
            f"🔖 Referencia: {reference}"
        )

        # Clear state
        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

        await update.message.reply_text(
            "¿Necesitas algo más? 😊",
            reply_markup=main_menu_keyboard(),
        )

    except Exception as e:
        logger.error(f"Error in handle_payment_photo: {e}")
        await update.message.reply_text(
            "Error al procesar tu comprobante. Por favor intenta nuevamente o contacta a soporte.",
            reply_markup=payment_received_keyboard(),
        )
