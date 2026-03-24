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
    PAYMENT_CONFIRMED, ACCESS_DELIVERED, ACCESS_INSTRUCTIONS, PIN_LINE
)
from bot.middleware import (
    set_user_state, clear_user_state,
    get_user_data, set_user_data, clear_user_data
)
from database.users import get_user_by_telegram_id
from database.platforms import get_platform_by_id
from database.accounts import get_account_by_id
from database.profiles import get_available_profiles, assign_profile
from database.subscriptions import create_subscription, confirm_subscription
from database.analytics import get_platform_availability
from services.exchange_service import calculate_price_bs, get_current_rate
from services.payment_service import get_payment_config
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
        # Extract platform_id and plan_type from callback data (primary source)
        # callback format: confirm:{plan_type}:{platform_id}
        cb_parts = (query.data or "").split(":")
        plan_type = cb_parts[1] if len(cb_parts) > 1 else (get_user_data(telegram_id, "selected_plan_type") or "monthly")
        platform_id = cb_parts[2] if len(cb_parts) > 2 else get_user_data(telegram_id, "selected_platform_id")

        if not platform_id:
            await query.edit_message_text("Sesión expirada. Usa /start para comenzar de nuevo.")
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
            # Redis data lost — recalculate from platform
            platform_tmp = await get_platform_by_id(platform_id)
            price_field = {"monthly": "monthly_price_usd", "express": "express_price_usd", "week": "week_price_usd"}.get(plan_type, "monthly_price_usd")
            price_usd = float((platform_tmp or {}).get(price_field) or 4.50)
            price_bs  = await calculate_price_bs(price_usd)
            rate_obj  = await get_current_rate()
            rate_used = float((rate_obj or {}).get("usd_binance") or 36.0)

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
        # Refresh all session data so handle_payment_photo always has what it needs,
        # even if the user takes time before sending the comprobante.
        set_user_data(telegram_id, "current_sub_id", sub_id)
        set_user_data(telegram_id, "selected_platform_id", platform_id)
        set_user_data(telegram_id, "selected_plan_type", plan_type)
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_used))
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
        price_usd = float(get_user_data(telegram_id, "price_usd") or 0)

        # Download photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        # Immediately acknowledge to client — no matter what happens next
        await update.message.reply_text(
            "⏳ <b>Comprobante recibido</b>\n\n"
            "Estamos verificando tu pago. En breve recibirás confirmación. 🙏",
            parse_mode="HTML",
        )

        # ── Anti-fraud: duplicate image check (fast, no LLM needed) ──────
        import hashlib
        img_hash = hashlib.sha256(bytes(image_bytes)).hexdigest()
        from database.subscriptions import check_payment_reference_exists
        try:
            from bot.middleware import get_user_data as _gd, set_user_data as _sd
            from services.payment_service import _check_image_hash_duplicate, _store_image_hash
            if await _check_image_hash_duplicate(img_hash):
                await update.message.reply_text(
                    "❌ Este comprobante ya fue enviado anteriormente. "
                    "Si crees que es un error, contacta a soporte.",
                    reply_markup=payment_received_keyboard(),
                )
                return
        except Exception:
            pass  # Don't let anti-fraud block the flow

        # ── OCR via LLM (best-effort, never blocks) ──────────────────────
        ocr: dict = {}
        ocr_available = False
        try:
            from services.gemini_service import validate_payment_image
            ocr = await validate_payment_image(bytes(image_bytes))
            ocr_available = ocr.get("is_comprobante_valido") is True
        except Exception as e:
            logger.warning(f"OCR failed (non-blocking): {e}")

        reference = ocr.get("referencia") or "N/A"

        # ── Save proof to DB ─────────────────────────────────────────────
        payment_image_url = f"tg://photo/{photo.file_id}"
        from database.subscriptions import save_payment_proof
        await save_payment_proof(sub_id, reference, payment_image_url)

        # Store image hash to prevent duplicates
        try:
            from services.payment_service import _store_image_hash
            await _store_image_hash(img_hash)
        except Exception:
            pass

        # ── Load context for ticket ──────────────────────────────────────
        user = await get_user_by_telegram_id(telegram_id)
        platform = await get_platform_by_id(platform_id)
        plan_label = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}.get(plan_type, plan_type)
        client_name = (user or {}).get("name", "") if user else str(telegram_id)
        platform_name = (platform or {}).get("name", "?")

        # ── Build admin ticket ───────────────────────────────────────────
        from services.notification_service import send_to_admin
        from bot.keyboards import pending_payment_keyboard

        if ocr_available:
            def _v(key: str, default: str = "—") -> str:
                val = ocr.get(key)
                return str(val).strip() if val else default

            hora_str = f" {_v('hora', '')}" if ocr.get("hora") else ""
            ocr_section = (
                f"📋 <b>DATOS DEL COMPROBANTE (OCR)</b>\n\n"
                f"🔖 <b>Referencia:</b> <code>{_v('referencia')}</code>\n"
                f"📅 <b>Fecha:</b> {_v('fecha')}{hora_str}\n"
                f"💰 <b>Monto:</b> Bs {_v('monto')}\n"
                f"📱 <b>Celular destino:</b> {_v('celular_destino')}\n"
                f"🪪 <b>Cédula receptor:</b> {_v('cedula_receptor')}\n"
                f"🏦 <b>Banco emisor:</b> {_v('banco_emisor')}\n"
                f"🏦 <b>Banco receptor:</b> {_v('banco_receptor')}\n"
                f"📝 <b>Concepto:</b> {_v('concepto')}\n"
                f"🔍 <b>Confianza OCR:</b> {_v('confianza')}"
            )
            reference = ocr.get("referencia") or reference
        else:
            ocr_section = (
                f"📋 <b>COMPROBANTE RECIBIDO</b>\n\n"
                f"⚠️ OCR no disponible — revisar imagen manualmente."
            )

        admin_ticket = (
            f"💳 <b>TICKET DE PAGO #{short_id(sub_id)}</b>\n\n"
            f"👤 <b>Cliente:</b> {client_name} (<code>{telegram_id}</code>)\n"
            f"📺 <b>Servicio:</b> {platform_name} — {plan_label}\n"
            f"💵 <b>Monto esperado:</b> ${price_usd:.2f} USD / Bs {price_bs:,.2f}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{ocr_section}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"¿Apruebas o rechazas este pago?"
        )
        await send_to_admin(admin_ticket, keyboard=pending_payment_keyboard(sub_id))

        # Clear state
        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

    except Exception as e:
        logger.error(f"Error in handle_payment_photo: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "Hubo un problema procesando tu comprobante. "
                "Por favor contacta a soporte directamente. 📞",
                reply_markup=payment_received_keyboard(),
            )
        except Exception:
            pass
