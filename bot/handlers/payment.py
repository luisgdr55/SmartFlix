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


async def handle_cart_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process payment photo for a multi-service cart order."""
    import hashlib
    import html as _html
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    try:
        from bot.middleware import get_user_data, clear_user_state, clear_user_data
        cart_sub_ids_str = get_user_data(telegram_id, "cart_sub_ids") or ""
        total_bs_str = get_user_data(telegram_id, "cart_total_bs") or "0"
        total_usd_str = get_user_data(telegram_id, "cart_total_usd") or "0"
        sub_ids = [s for s in cart_sub_ids_str.split(",") if s.strip()]

        if not sub_ids:
            await update.message.reply_text("No encontramos tu pedido. Usa /start para comenzar de nuevo.")
            return

        total_bs = float(total_bs_str)
        total_usd = float(total_usd_str)

        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = await photo_file.download_as_bytearray()

        await update.message.reply_text(
            "⏳ <b>Comprobante recibido</b>\n\nVerificando tu pago. Recibirás todos tus accesos en breve. 🙏",
            parse_mode="HTML",
        )

        # Anti-fraud duplicate check
        img_hash = hashlib.sha256(bytes(image_bytes)).hexdigest()
        try:
            from services.payment_service import _check_image_hash_duplicate, _store_image_hash
            if await _check_image_hash_duplicate(img_hash):
                await update.message.reply_text("❌ Este comprobante ya fue enviado anteriormente. Contacta a soporte.")
                return
        except Exception:
            pass

        # OCR
        ocr: dict = {}
        ocr_available = False
        try:
            from services.gemini_service import validate_payment_image
            ocr = await validate_payment_image(bytes(image_bytes))
            ocr_available = ocr.get("is_comprobante_valido") is True
        except Exception as e:
            logger.warning(f"OCR failed (non-blocking): {e}")

        reference = ocr.get("referencia") or f"CARRITO-{sub_ids[0][:6].upper()}"

        def _ov(key: str, default: str = "—") -> str:
            val = ocr.get(key)
            return str(val).strip() if val else default

        if ocr_available:
            hora_part = f" {_ov('hora')}" if ocr.get("hora") else ""
            ocr_text = (
                f"Referencia: {_ov('referencia')}\n"
                f"Fecha: {_ov('fecha')}{hora_part}\n"
                f"Monto: Bs {_ov('monto')}\n"
                f"Banco emisor: {_ov('banco_emisor')}\n"
                f"Banco receptor: {_ov('banco_receptor')}\n"
                f"Confianza OCR: {_ov('confianza')}"
            )
        else:
            ocr_text = f"OCR no disponible | file_id:{photo.file_id}"

        # Save proof to ALL pending subscriptions
        from database.subscriptions import save_payment_proof, get_subscription_by_id
        for sid in sub_ids:
            await save_payment_proof(sid, reference, ocr_text)

        try:
            from services.payment_service import _store_image_hash
            await _store_image_hash(img_hash)
        except Exception:
            pass

        # Load context for admin ticket
        from database.users import get_user_by_telegram_id as _get_user
        user = await _get_user(telegram_id)
        client_name = _html.escape((user or {}).get("name", "") or str(telegram_id))

        # Build list of services for the ticket
        services_lines = []
        for sid in sub_ids:
            sub = await get_subscription_by_id(sid)
            if sub:
                plat = sub.get("platforms") or {}
                plan_label = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}.get(sub.get("plan_type", ""), sub.get("plan_type", ""))
                services_lines.append(f"  {plat.get('icon_emoji', '📺')} {_html.escape(plat.get('name', '?'))} — {plan_label}")

        from services.notification_service import send_to_admin
        from bot.keyboards import pending_payment_keyboard

        if ocr_available:
            hora_str = f" {_html.escape(_ov('hora'))}" if ocr.get("hora") else ""
            ocr_section = (
                f"📋 <b>DATOS DEL COMPROBANTE (OCR)</b>\n\n"
                f"🔖 <b>Referencia:</b> <code>{_html.escape(_ov('referencia'))}</code>\n"
                f"📅 <b>Fecha:</b> {_html.escape(_ov('fecha'))}{hora_str}\n"
                f"💰 <b>Monto:</b> Bs {_html.escape(_ov('monto'))}\n"
                f"🏦 <b>Banco emisor:</b> {_html.escape(_ov('banco_emisor'))}\n"
                f"🔍 <b>Confianza OCR:</b> {_html.escape(_ov('confianza'))}"
            )
        else:
            ocr_section = "📋 <b>COMPROBANTE RECIBIDO</b>\n\n⚠️ OCR no disponible."

        services_text = "\n".join(services_lines) if services_lines else "(no data)"

        admin_ticket = (
            f"🛒 <b>PEDIDO MÚLTIPLE #{reference[:8] if reference else 'N/A'}</b>\n\n"
            f"👤 <b>Cliente:</b> {client_name} (<code>{telegram_id}</code>)\n"
            f"📦 <b>Servicios ({len(sub_ids)}):</b>\n{services_text}\n"
            f"💵 <b>Total:</b> ${total_usd:.2f} / Bs {total_bs:,.0f}\n\n"
            f"{ocr_section}\n\n"
            f"Aprobar cada servicio individualmente 👇"
        )

        await send_to_admin("📎 Comprobante del cliente (pedido múltiple):", photo_bytes=bytes(image_bytes))
        await send_to_admin(admin_ticket)

        # Send approve buttons for each subscription
        for sid in sub_ids:
            sub = await get_subscription_by_id(sid)
            plat = (sub or {}).get("platforms") or {}
            plan = (sub or {}).get("plan_type", "")
            plan_label = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}.get(plan, plan)
            ticket_line = (
                f"✅ <b>Aprobar:</b> {plat.get('icon_emoji', '📺')} {_html.escape(plat.get('name', '?'))} — {plan_label}\n"
                f"📎 Ref: <code>{reference}</code>"
            )
            await send_to_admin(ticket_line, keyboard=pending_payment_keyboard(sid))

        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

    except Exception as e:
        logger.error(f"Error in handle_cart_payment_photo: {e}", exc_info=True)
        try:
            await update.message.reply_text("Hubo un problema. Contacta a soporte directamente. 📞")
        except Exception:
            pass
