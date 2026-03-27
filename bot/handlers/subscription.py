from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    """Show platform selection, or renewal cart if user has expired subscriptions."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    try:
        from database.subscriptions import get_user_attention_subscriptions
        user = await get_user_by_telegram_id(update.effective_user.id)
        if user:
            attention = await get_user_attention_subscriptions(str(user["id"]))
            expired = attention.get("expired", [])
            if expired:
                await _show_renewal_cart(query, context, user, expired)
                return

        availability = await get_platform_availability()

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

        plan_label = {"monthly": "Mensual (30 días)", "express": "Express (24h)"}.get(plan_type, plan_type)

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
            price_field = {"monthly": "monthly_price_usd", "express": "express_price_usd"}.get(plan_type, "monthly_price_usd")
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
        durations = {"monthly": 30, "express": 1}
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

    import hashlib
    import html as _html

    telegram_id = update.effective_user.id

    try:
        # ── Step 1: Resolve subscription data (Redis primary, DB fallback) ──
        sub_id = get_user_data(telegram_id, "current_sub_id")
        price_bs_str = get_user_data(telegram_id, "price_bs")
        platform_id = get_user_data(telegram_id, "selected_platform_id")
        plan_type = get_user_data(telegram_id, "selected_plan_type") or "monthly"
        price_usd_str = get_user_data(telegram_id, "price_usd") or "0"

        # If ANY critical key is missing, recover directly from DB
        # (covers: Redis down, TTL expired, state lost between requests)
        if not sub_id or not price_bs_str or not platform_id:
            logger.info(f"Redis session incomplete for {telegram_id}, falling back to DB")
            user_db = await get_user_by_telegram_id(telegram_id)
            if user_db:
                from database.subscriptions import get_user_pending_subscription
                pending = await get_user_pending_subscription(str(user_db["id"]))
                if pending:
                    sub_id = str(pending["id"])
                    platform_id = str(pending.get("platform_id") or "")
                    plan_type = pending.get("plan_type") or "monthly"
                    price_bs_str = str(pending.get("price_bs") or "0")
                    price_usd_str = str(pending.get("price_usd") or "0")
                else:
                    await update.message.reply_text(
                        "No encontramos un pedido pendiente. "
                        "Usa /start para iniciar un nuevo pedido. 📋"
                    )
                    clear_user_state(telegram_id)
                    return
            else:
                await update.message.reply_text(
                    "No encontramos tu cuenta. Usa /start para comenzar. 📋"
                )
                return

        price_bs = float(price_bs_str)
        price_usd = float(price_usd_str)

        # ── Step 2: Download photo ────────────────────────────────────────
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        # ── Step 3: Immediate client acknowledgment ───────────────────────
        await update.message.reply_text(
            "⏳ <b>Comprobante recibido</b>\n\n"
            "Estamos verificando tu pago. En breve recibirás confirmación. 🙏",
            parse_mode="HTML",
        )

        # ── Step 4: Anti-fraud duplicate check ───────────────────────────
        img_hash = hashlib.sha256(bytes(image_bytes)).hexdigest()
        try:
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

        # ── Step 5: OCR via LLM (best-effort, never blocks) ──────────────
        ocr: dict = {}
        ocr_available = False
        try:
            from services.gemini_service import validate_payment_image
            ocr = await validate_payment_image(bytes(image_bytes))
            ocr_available = ocr.get("is_comprobante_valido") is True
        except Exception as e:
            logger.warning(f"OCR failed (non-blocking): {e}")

        reference = ocr.get("referencia") or "SIN-REF"

        # ── Step 6: Build OCR text for dashboard storage ──────────────────
        def _ov(key: str, default: str = "—") -> str:
            val = ocr.get(key)
            return str(val).strip() if val else default

        if ocr_available:
            hora_part = f" {_ov('hora')}" if ocr.get("hora") else ""
            ocr_text_for_db = (
                f"Referencia: {_ov('referencia')}\n"
                f"Fecha: {_ov('fecha')}{hora_part}\n"
                f"Monto: Bs {_ov('monto')}\n"
                f"Celular destino: {_ov('celular_destino')}\n"
                f"Cédula receptor: {_ov('cedula_receptor')}\n"
                f"Banco emisor: {_ov('banco_emisor')}\n"
                f"Banco receptor: {_ov('banco_receptor')}\n"
                f"Concepto: {_ov('concepto')}\n"
                f"Confianza OCR: {_ov('confianza')}"
            )
            reference = ocr.get("referencia") or reference
        else:
            ocr_text_for_db = f"OCR no disponible | file_id:{photo.file_id}"

        # ── Step 7: Save to DB (reference + OCR text as payment_image_url) ─
        from database.subscriptions import save_payment_proof
        await save_payment_proof(sub_id, reference, ocr_text_for_db)

        # Store image hash to prevent duplicates
        try:
            from services.payment_service import _store_image_hash
            await _store_image_hash(img_hash)
        except Exception:
            pass

        # ── Step 8: Load context and build admin ticket ───────────────────
        user = await get_user_by_telegram_id(telegram_id)
        platform = await get_platform_by_id(platform_id)
        plan_label = {"monthly": "Mensual", "express": "Express 24h"}.get(plan_type, plan_type)
        client_name = _html.escape((user or {}).get("name", "") or str(telegram_id))
        platform_name = _html.escape((platform or {}).get("name", "?"))

        from services.notification_service import send_to_admin
        from bot.keyboards import pending_payment_keyboard

        if ocr_available:
            hora_str = f" {_html.escape(_ov('hora'))}" if ocr.get("hora") else ""
            ocr_section = (
                f"📋 <b>DATOS DEL COMPROBANTE (OCR)</b>\n\n"
                f"🔖 <b>Referencia:</b> <code>{_html.escape(_ov('referencia'))}</code>\n"
                f"📅 <b>Fecha:</b> {_html.escape(_ov('fecha'))}{hora_str}\n"
                f"💰 <b>Monto:</b> Bs {_html.escape(_ov('monto'))}\n"
                f"📱 <b>Celular destino:</b> {_html.escape(_ov('celular_destino'))}\n"
                f"🪪 <b>Cédula receptor:</b> {_html.escape(_ov('cedula_receptor'))}\n"
                f"🏦 <b>Banco emisor:</b> {_html.escape(_ov('banco_emisor'))}\n"
                f"🏦 <b>Banco receptor:</b> {_html.escape(_ov('banco_receptor'))}\n"
                f"📝 <b>Concepto:</b> {_html.escape(_ov('concepto'))}\n"
                f"🔍 <b>Confianza OCR:</b> {_html.escape(_ov('confianza'))}"
            )
        else:
            ocr_section = (
                "📋 <b>COMPROBANTE RECIBIDO</b>\n\n"
                "⚠️ OCR no disponible — revisar imagen manualmente."
            )

        admin_ticket = (
            f"💳 <b>TICKET DE PAGO #{short_id(sub_id)}</b>\n\n"
            f"👤 <b>Cliente:</b> {client_name} (<code>{telegram_id}</code>)\n"
            f"📺 <b>Servicio:</b> {platform_name} — {plan_label}\n"
            f"💵 <b>Monto esperado:</b> ${price_usd:.2f} USD / Bs {price_bs:,.2f}\n\n"
            f"{ocr_section}\n\n"
            f"¿Apruebas o rechazas este pago?"
        )

        # ── Step 9: Notify admin (image first, then ticket with buttons) ──
        await send_to_admin("📎 Comprobante del cliente:", photo_bytes=bytes(image_bytes))
        await send_to_admin(admin_ticket, keyboard=pending_payment_keyboard(sub_id))

        # ── Step 10: Clear state ──────────────────────────────────────────
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


async def handle_cart_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add current platform selection to cart, then show cart."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        # callback: cart:add:{plan_type}:{platform_id}
        parts = (query.data or "").split(":")
        plan_type = parts[2] if len(parts) > 2 else "monthly"
        platform_id = parts[3] if len(parts) > 3 else None

        if not platform_id:
            await query.edit_message_text("Error: plataforma no identificada.", reply_markup=main_menu_keyboard())
            return

        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.edit_message_text("Plataforma no encontrada.", reply_markup=main_menu_keyboard())
            return

        # Get price from Redis (already stored by handle_platform_selected), or recalculate
        price_usd_str = get_user_data(telegram_id, "price_usd")
        price_bs_str = get_user_data(telegram_id, "price_bs")
        rate_str = get_user_data(telegram_id, "rate_used")

        if price_usd_str and price_bs_str:
            price_usd = float(price_usd_str)
            price_bs = float(price_bs_str)
            rate_used = float(rate_str or "36")
        else:
            price_field = {"monthly": "monthly_price_usd", "express": "express_price_usd"}.get(plan_type, "monthly_price_usd")
            price_usd = float(platform.get(price_field) or 0)
            rate_obj = await get_current_rate()
            rate_used = float((rate_obj or {}).get("usd_binance") or 36.0)
            price_bs = round(price_usd * rate_used, 2)

        from services.cart_service import add_to_cart, get_cart
        from bot.keyboards import cart_keyboard

        item = {
            "platform_id": platform_id,
            "name": platform.get("name", "?"),
            "emoji": platform.get("icon_emoji") or "📺",
            "plan_type": plan_type,
            "price_usd": price_usd,
            "price_bs": price_bs,
            "rate_used": rate_used,
        }
        cart = add_to_cart(telegram_id, item)

        total_usd = sum(float(i.get("price_usd") or 0) for i in cart)
        total_bs = sum(float(i.get("price_bs") or 0) for i in cart)

        plan_labels = {"monthly": "Mensual", "express": "Express 24h"}
        lines = ["🛒 <b>Carrito actualizado:</b>\n"]
        for i in cart:
            pl = plan_labels.get(i.get("plan_type", "monthly"), i.get("plan_type", ""))
            lines.append(f"{i.get('emoji','📺')} <b>{i.get('name','?')}</b> — {pl}: ${float(i.get('price_usd',0)):.2f} / Bs {float(i.get('price_bs',0)):,.0f}")
        lines.append(f"\n<b>Total: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")
        lines.append("\n¿Agregar otro servicio o confirmar pedido?")

        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=cart_keyboard())

    except Exception as e:
        logger.error(f"handle_cart_add error: {e}", exc_info=True)
        await query.edit_message_text("Error al agregar al carrito.", reply_markup=main_menu_keyboard())


async def handle_cart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create pending_payment subscriptions for all cart items and show payment instructions."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        from services.cart_service import get_cart, clear_cart
        from services.payment_service import get_payment_config

        cart = get_cart(telegram_id)
        if not cart:
            await query.edit_message_text(
                "Tu carrito está vacío. Usa el menú para agregar servicios.",
                reply_markup=main_menu_keyboard(),
            )
            return

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("No encontramos tu cuenta. Usa /start.")
            return

        now = venezuela_now()
        sub_ids = []
        lines = ["📋 <b>Pedido confirmado:</b>\n"]

        for item in cart:
            plan_type = item.get("plan_type", "monthly")
            price_usd = float(item.get("price_usd") or 0)
            price_bs = float(item.get("price_bs") or 0)
            platform_id = item.get("platform_id")
            name = item.get("name", "?")
            emoji = item.get("emoji", "📺")
            plan_label = {"monthly": "Mensual ~30d", "express": "Express 24h"}.get(plan_type, plan_type)
            plan_days = {"monthly": 30, "express": 1}.get(plan_type, 30)
            end_date = now + timedelta(days=plan_days)

            sub = await create_subscription(
                user_id=str(user["id"]),
                platform_id=platform_id,
                plan_type=plan_type,
                price_usd=price_usd,
                price_bs=price_bs,
                rate_used=float(item.get("rate_used") or 36),
                end_date=end_date,
            )
            if sub:
                sub_ids.append(str(sub["id"]))
                lines.append(f"{emoji} <b>{name}</b> — {plan_label}: Bs {price_bs:,.0f}")

        if not sub_ids:
            await query.edit_message_text(
                "Error al crear los pedidos. Intenta de nuevo o contacta soporte.",
                reply_markup=main_menu_keyboard(),
            )
            return

        total_bs = sum(float(i.get("price_bs") or 0) for i in cart)
        total_usd = sum(float(i.get("price_usd") or 0) for i in cart)
        lines.append(f"\n💰 <b>Total a pagar: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")

        payment_cfg = await get_payment_config()

        payment_text = (
            "\n".join(lines) + "\n\n"
            "📲 <b>Instrucciones de pago:</b>\n\n"
            f"🏦 Banco: <b>{payment_cfg.get('banco', 'N/A')}</b>\n"
            f"📱 Teléfono: <b>{payment_cfg.get('telefono', 'N/A')}</b>\n"
            f"🪪 Cédula: <b>{payment_cfg.get('cedula', 'N/A')}</b>\n"
            f"👤 Titular: <b>{payment_cfg.get('titular', 'N/A')}</b>\n\n"
            f"💵 Monto exacto: <b>Bs {total_bs:,.2f}</b>\n\n"
            "📸 Envía el comprobante aquí y el equipo lo revisará enseguida. ¡Gracias! 🙏"
        )

        # Store cart sub IDs and total in state for payment photo routing
        set_user_state(telegram_id, "awaiting_cart_payment")
        set_user_data(telegram_id, "cart_sub_ids", ",".join(sub_ids))
        set_user_data(telegram_id, "cart_total_bs", str(total_bs))
        set_user_data(telegram_id, "cart_total_usd", str(total_usd))

        clear_cart(telegram_id)

        await query.edit_message_text(payment_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"handle_cart_confirm error: {e}", exc_info=True)
        await query.edit_message_text(
            "Error al procesar el pedido. Intenta de nuevo.",
            reply_markup=main_menu_keyboard(),
        )


async def handle_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the shopping cart and show main menu."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer("Carrito vaciado")

    from services.cart_service import clear_cart
    clear_cart(update.effective_user.id)

    await query.edit_message_text(
        "🗑️ Carrito vaciado. ¿Qué deseas hacer?",
        reply_markup=main_menu_keyboard(),
    )


async def handle_cart_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process payment comprobante for a multi-service cart order."""
    if not update.message or not update.effective_user:
        return

    import hashlib
    import html as _html

    telegram_id = update.effective_user.id

    try:
        sub_ids_str = get_user_data(telegram_id, "cart_sub_ids") or ""
        total_bs_str = get_user_data(telegram_id, "cart_total_bs") or "0"
        total_usd_str = get_user_data(telegram_id, "cart_total_usd") or "0"
        sub_ids = [s for s in sub_ids_str.split(",") if s]

        if not sub_ids:
            await update.message.reply_text(
                "No encontramos pedidos pendientes. Usa /start para iniciar. 📋"
            )
            clear_user_state(telegram_id)
            return

        total_bs = float(total_bs_str)
        total_usd = float(total_usd_str)

        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        await update.message.reply_text(
            "⏳ <b>Comprobante recibido</b>\n\nEstamos verificando tu pago. En breve recibirás confirmación. 🙏",
            parse_mode="HTML",
        )

        # Anti-fraud hash check
        img_hash = hashlib.sha256(bytes(image_bytes)).hexdigest()
        try:
            from services.payment_service import _check_image_hash_duplicate, _store_image_hash
            if await _check_image_hash_duplicate(img_hash):
                await update.message.reply_text(
                    "❌ Este comprobante ya fue enviado anteriormente.",
                    reply_markup=payment_received_keyboard(),
                )
                return
        except Exception:
            pass

        # OCR
        ocr: dict = {}
        try:
            from services.gemini_service import validate_payment_image
            ocr = await validate_payment_image(bytes(image_bytes))
        except Exception as e:
            logger.warning(f"OCR failed in cart payment: {e}")

        reference = ocr.get("referencia") or "SIN-REF"
        ocr_text = (
            f"Referencia: {ocr.get('referencia','—')}\nFecha: {ocr.get('fecha','—')}\n"
            f"Monto: Bs {ocr.get('monto','—')}\nBanco emisor: {ocr.get('banco_emisor','—')}"
        ) if ocr.get("is_comprobante_valido") else f"OCR no disponible | file_id:{photo.file_id}"

        # Save proof to all sub IDs
        from database.subscriptions import save_payment_proof
        for sid in sub_ids:
            await save_payment_proof(sid, reference, ocr_text)

        try:
            from services.payment_service import _store_image_hash
            await _store_image_hash(img_hash)
        except Exception:
            pass

        user = await get_user_by_telegram_id(telegram_id)
        client_name = _html.escape((user or {}).get("name", "") or str(telegram_id))

        from services.notification_service import send_to_admin
        from bot.keyboards import pending_payment_keyboard

        await send_to_admin("📎 Comprobante (pedido múltiple):", photo_bytes=bytes(image_bytes))

        # Send one approve button per sub
        for sid in sub_ids:
            await send_to_admin(
                f"💳 <b>PAGO MÚLTIPLE</b>\n👤 {client_name} (<code>{telegram_id}</code>)\n"
                f"💵 Total: ${total_usd:.2f} / Bs {total_bs:,.0f}\n"
                f"🔖 Sub: <code>{short_id(sid)}</code>\n\n¿Apruebas este servicio?",
                keyboard=pending_payment_keyboard(sid),
            )

        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

    except Exception as e:
        logger.error(f"handle_cart_payment_photo error: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "Hubo un problema procesando tu comprobante. Contacta a soporte. 📞",
                reply_markup=payment_received_keyboard(),
            )
        except Exception:
            pass


# ── Renewal cart (pre-filled with expired subscriptions) ─────────────────────

_RCART_KEY = "renewal_cart"


async def _show_renewal_cart(query, context, user, expired_subs: list) -> None:
    """Build renewal cart from expired subscriptions and display it."""
    try:
        rate_obj = await get_current_rate()
        rate = float((rate_obj or {}).get("usd_binance") or 36.0)

        cart: dict = {}
        for sub in expired_subs:
            platform_id = sub.get("platform_id")
            if not platform_id:
                continue
            platform = sub.get("platforms") or {}
            plan_type = sub.get("plan_type") or "monthly"
            price_field = "monthly_price_usd" if plan_type == "monthly" else "express_price_usd"
            plat_data = await get_platform_by_id(str(platform_id)) or {}
            price_usd = float(plat_data.get(price_field) or 0)
            price_bs = round(price_usd * rate, 2)
            sub_id = str(sub["id"])
            cart[sub_id] = {
                "sub_id": sub_id,
                "platform_id": str(platform_id),
                "name": platform.get("name") or plat_data.get("name") or "?",
                "emoji": platform.get("icon_emoji") or plat_data.get("icon_emoji") or "📺",
                "plan_type": plan_type,
                "price_usd": price_usd,
                "price_bs": price_bs,
                "rate_used": rate,
                "selected": True,
            }

        if not cart:
            # Fallback: no price data, go to normal platform picker
            availability = await get_platform_availability()
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
            return

        context.user_data[_RCART_KEY] = cart
        await _render_renewal_cart(query, context)

    except Exception as e:
        logger.error(f"_show_renewal_cart error: {e}", exc_info=True)
        await query.edit_message_text("Error al cargar servicios. Intenta de nuevo.", reply_markup=main_menu_keyboard())


async def _render_renewal_cart(query, context) -> None:
    """Render the renewal cart message with toggle buttons."""
    cart: dict = context.user_data.get(_RCART_KEY, {})
    selected = {k: v for k, v in cart.items() if v.get("selected")}

    total_usd = sum(float(v["price_usd"]) for v in selected.values())
    total_bs = sum(float(v["price_bs"]) for v in selected.values())

    plan_labels = {"monthly": "Mensual", "express": "Express 24h"}

    lines = ["🛒 <b>Renovar tus servicios:</b>\n"]
    for item in cart.values():
        check = "✅" if item.get("selected") else "⬜"
        pl = plan_labels.get(item.get("plan_type", "monthly"), "Mensual")
        lines.append(
            f"{check} {item.get('emoji','📺')} <b>{item.get('name','?')}</b> — {pl}: "
            f"${float(item.get('price_usd', 0)):.2f} / Bs {float(item.get('price_bs', 0)):,.0f}"
        )

    if selected:
        lines.append(f"\n💰 <b>Total: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")
        lines.append("\nPuedes desmarcar servicios que no quieras renovar ahora.")
    else:
        lines.append("\n⚠️ Selecciona al menos un servicio para continuar.")

    # Keyboard: one toggle button per item
    buttons = []
    for item in cart.values():
        label = f"✅ {item['name']}" if item.get("selected") else f"☐ {item['name']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"rcart:toggle:{item['sub_id']}")])

    # Action row
    action_row = []
    if selected:
        count = len(selected)
        label = f"💳 Pagar {count} servicio{'s' if count > 1 else ''} — Bs {total_bs:,.0f}"
        action_row.append(InlineKeyboardButton(label, callback_data="rcart:confirm"))
    action_row.append(InlineKeyboardButton("➕ Agregar otro", callback_data="rcart:add_new"))
    buttons.append(action_row)
    buttons.append([InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_renewal_cart_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a subscription in/out of the renewal cart."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    sub_id = (query.data or "").split(":")[-1]
    cart: dict = context.user_data.get(_RCART_KEY, {})
    if sub_id in cart:
        cart[sub_id]["selected"] = not cart[sub_id].get("selected", True)
    await _render_renewal_cart(query, context)


async def handle_renewal_cart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create pending_payment subscriptions for selected renewal items."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        cart: dict = context.user_data.get(_RCART_KEY, {})
        selected = [v for v in cart.values() if v.get("selected")]

        if not selected:
            await query.answer("Selecciona al menos un servicio.", show_alert=True)
            return

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("No encontramos tu cuenta. Usa /start.")
            return

        now = venezuela_now()
        sub_ids = []
        lines = ["📋 <b>Pedido de renovación confirmado:</b>\n"]

        for item in selected:
            plan_type = item.get("plan_type", "monthly")
            price_usd = float(item.get("price_usd") or 0)
            price_bs = float(item.get("price_bs") or 0)
            platform_id = item.get("platform_id")
            name = item.get("name", "?")
            emoji = item.get("emoji", "📺")
            plan_label = {"monthly": "Mensual ~30d", "express": "Express 24h"}.get(plan_type, plan_type)
            plan_days = {"monthly": 30, "express": 1}.get(plan_type, 30)
            end_date = now + timedelta(days=plan_days)

            sub = await create_subscription(
                user_id=str(user["id"]),
                platform_id=platform_id,
                plan_type=plan_type,
                price_usd=price_usd,
                price_bs=price_bs,
                rate_used=float(item.get("rate_used") or 36),
                end_date=end_date,
            )
            if sub:
                sub_ids.append(str(sub["id"]))
                lines.append(f"{emoji} <b>{name}</b> — {plan_label}: Bs {price_bs:,.0f}")

        if not sub_ids:
            await query.edit_message_text(
                "Error al crear los pedidos. Intenta de nuevo o contacta soporte.",
                reply_markup=main_menu_keyboard(),
            )
            return

        total_bs = sum(float(v.get("price_bs") or 0) for v in selected)
        total_usd = sum(float(v.get("price_usd") or 0) for v in selected)
        lines.append(f"\n💰 <b>Total a pagar: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")

        payment_cfg = await get_payment_config()
        payment_text = (
            "\n".join(lines) + "\n\n"
            "📲 <b>Instrucciones de pago:</b>\n\n"
            f"🏦 Banco: <b>{payment_cfg.get('banco', 'N/A')}</b>\n"
            f"📱 Teléfono: <b>{payment_cfg.get('telefono', 'N/A')}</b>\n"
            f"🪪 Cédula: <b>{payment_cfg.get('cedula', 'N/A')}</b>\n"
            f"👤 Titular: <b>{payment_cfg.get('titular', 'N/A')}</b>\n\n"
            f"💵 Monto exacto: <b>Bs {total_bs:,.2f}</b>\n\n"
            "📸 Envía el comprobante aquí y el equipo lo revisará enseguida. ¡Gracias! 🙏"
        )

        set_user_state(telegram_id, "awaiting_cart_payment")
        set_user_data(telegram_id, "cart_sub_ids", ",".join(sub_ids))
        set_user_data(telegram_id, "cart_total_bs", str(total_bs))
        set_user_data(telegram_id, "cart_total_usd", str(total_usd))

        context.user_data.pop(_RCART_KEY, None)

        await query.edit_message_text(payment_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"handle_renewal_cart_confirm error: {e}", exc_info=True)
        await query.edit_message_text("Error al procesar la renovación.", reply_markup=main_menu_keyboard())


async def handle_renewal_add_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open platform picker to add an extra service to the renewal cart."""
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
            count = p.get("monthly_available", 0)
            platform_list_text += f"{icon} <b>{name}</b> - {count} disponible{'s' if count != 1 else ''}\n"

        await query.edit_message_text(
            SUBSCRIPTION_PLATFORM_SELECT.format(platform_list=platform_list_text),
            parse_mode="HTML",
            reply_markup=platforms_keyboard(availability, "monthly"),
        )
    except Exception as e:
        logger.error(f"handle_renewal_add_new error: {e}")
        await query.edit_message_text("Error al cargar plataformas.", reply_markup=main_menu_keyboard())
