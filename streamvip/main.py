from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException
from telegram import Update, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, Defaults
)

from config import settings

# Admin panel router
from admin_panel.router import panel_router

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global telegram application
_telegram_app: Application | None = None


def build_telegram_app() -> Application:
    """Build and configure the Telegram bot application with all handlers."""
    from bot.handlers.start import start_handler, handle_name_response, handle_contact_shared
    from bot.handlers.menu import show_main_menu
    from bot.handlers.subscription import show_subscription_platforms, handle_platform_selected, handle_order_confirmed
    from bot.handlers.express import show_express_platforms, handle_express_platform_selected, handle_express_confirmed, handle_queue_join
    from bot.handlers.my_services import show_my_services, handle_service_detail, handle_renewal
    from bot.handlers.support import (
        show_support_menu, handle_support_credentials, handle_support_verification_code,
        handle_support_troubleshooting, handle_support_profile_status, handle_contact_admin,
        handle_support_platform_selected
    )
    from bot.handlers.payment import handle_payment_image
    from bot.handlers.admin import (
        admin_dashboard, cmd_tasa, cmd_tasabcv, cmd_addcuenta, cmd_addexpress,
        cmd_editpin, cmd_clientes, cmd_cliente, cmd_pendientes, cmd_ingresos,
        cmd_bloquear, cmd_broadcast, cmd_flyer, cmd_promo, cmd_config, cmd_testllm,
        cmd_testnotif, handle_admin_callback
    )
    from bot.handlers._prices_addon import cmd_precios, handle_prices_callback

    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).defaults(Defaults(do_quote=False)).build()

    # =====================================================
    # COMMAND HANDLERS
    # =====================================================
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CommandHandler("tasa", cmd_tasa))
    app.add_handler(CommandHandler("tasabcv", cmd_tasabcv))
    app.add_handler(CommandHandler("addcuenta", cmd_addcuenta))
    app.add_handler(CommandHandler("addexpress", cmd_addexpress))
    app.add_handler(CommandHandler("editpin", cmd_editpin))
    app.add_handler(CommandHandler("clientes", cmd_clientes))
    app.add_handler(CommandHandler("cliente", cmd_cliente))
    app.add_handler(CommandHandler("pendientes", cmd_pendientes))
    app.add_handler(CommandHandler("ingresos", cmd_ingresos))
    app.add_handler(CommandHandler("bloquear", cmd_bloquear))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("flyer", cmd_flyer))
    app.add_handler(CommandHandler("promo", cmd_promo))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("testllm", cmd_testllm))
    app.add_handler(CommandHandler("testnotif", cmd_testnotif))
    app.add_handler(CommandHandler("precios", cmd_precios))

    # =====================================================
    # CALLBACK QUERY HANDLERS
    # =====================================================
    # Main menu
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern="^menu:main$"))
    app.add_handler(CallbackQueryHandler(show_subscription_platforms, pattern="^menu:subscribe$"))
    app.add_handler(CallbackQueryHandler(show_express_platforms, pattern="^menu:express$"))
    app.add_handler(CallbackQueryHandler(show_my_services, pattern="^menu:my_services$"))
    app.add_handler(CallbackQueryHandler(show_support_menu, pattern="^menu:support$"))

    # Platform selection
    app.add_handler(CallbackQueryHandler(handle_platform_selected, pattern="^platform:monthly:"))
    app.add_handler(CallbackQueryHandler(handle_express_platform_selected, pattern="^platform:express:"))

    # Order confirmation
    app.add_handler(CallbackQueryHandler(handle_order_confirmed, pattern="^confirm:monthly:"))
    app.add_handler(CallbackQueryHandler(handle_express_confirmed, pattern="^confirm:express:"))

    # My services
    app.add_handler(CallbackQueryHandler(handle_service_detail, pattern="^service:detail:"))
    app.add_handler(CallbackQueryHandler(handle_renewal, pattern="^renew:"))

    # Support
    app.add_handler(CallbackQueryHandler(show_support_menu, pattern="^menu:support$"))
    app.add_handler(CallbackQueryHandler(handle_support_credentials, pattern="^support:credentials$"))
    app.add_handler(CallbackQueryHandler(handle_support_verification_code, pattern="^support:verification_code$"))
    app.add_handler(CallbackQueryHandler(handle_support_troubleshooting, pattern="^support:troubleshooting$"))
    app.add_handler(CallbackQueryHandler(handle_support_profile_status, pattern="^support:profile_status$"))
    app.add_handler(CallbackQueryHandler(handle_contact_admin, pattern="^support:contact_admin$"))
    app.add_handler(CallbackQueryHandler(handle_support_platform_selected, pattern="^support:platform:"))

    # Express queue
    app.add_handler(CallbackQueryHandler(handle_queue_join, pattern="^queue:join:"))

    # Admin callbacks
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^campaign:"))

    # Price management callbacks (prices:*)
    app.add_handler(CallbackQueryHandler(handle_prices_callback, pattern="^prices:"))

    # Shopping cart callbacks
    from bot.handlers.subscription import handle_cart_confirm, handle_cart_clear, handle_cart_add

    async def _handle_cart_callback(update, context):
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        if data == "cart:confirm":
            await handle_cart_confirm(update, context)
        elif data == "cart:clear":
            await handle_cart_clear(update, context)
        elif data.startswith("cart:add:"):
            await handle_cart_add(update, context)

    app.add_handler(CallbackQueryHandler(_handle_cart_callback, pattern="^cart:"))

    # =====================================================
    # MESSAGE HANDLERS
    # =====================================================
    # Contact sharing - for pre-registered client linking by phone
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact_shared))

    # Photos - for payment comprobantes
    app.add_handler(MessageHandler(filters.PHOTO, handle_payment_image))

    # Text messages - for name capture and admin flows
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_message_router))

    # Global error handler
    app.add_error_handler(_global_error_handler)

    return app


async def _text_message_router(update: Update, context) -> None:
    """Route text messages based on current user state."""
    if not update.message or not update.effective_user:
        return

    from bot.middleware import get_user_state, check_user_blocked, rate_limit_check
    telegram_id = update.effective_user.id

    # Rate limit
    if await rate_limit_check(telegram_id):
        return

    # Blocked check
    if await check_user_blocked(telegram_id):
        await update.message.reply_text("❌ Tu cuenta ha sido suspendida.")
        return

    state = get_user_state(telegram_id)

    if state == "awaiting_name":
        from bot.handlers.start import handle_name_response
        await handle_name_response(update, context)
    elif state and state.startswith("admin:addcuenta"):
        await _handle_admin_addcuenta_flow(update, context, state)
    elif state and (state.startswith("admin:precios:") or state == "admin:tasa_manual"):
        from bot.handlers._prices_addon import handle_price_text_input
        await handle_price_text_input(update, context, state)
    elif state and state.startswith("admin:edit_client:"):
        await _handle_admin_edit_client_flow(update, context, state)
    else:
        # AI-powered free-text handler — understands any message by intent
        from bot.handlers.ai_chat import handle_free_text
        await handle_free_text(update, context)


async def _handle_admin_addcuenta_flow(update: Update, context, state: str) -> None:
    """Handle multi-step admin account creation flow."""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    from bot.middleware import set_user_state, get_user_data, set_user_data

    if state == "admin:addcuenta:select_platform":
        from database.platforms import get_platform_by_slug, get_active_platforms
        platforms = await get_active_platforms()
        platform = None

        # Try to match by number
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(platforms):
                platform = platforms[idx]
        else:
            platform = await get_platform_by_slug(text.lower())

        if not platform:
            await update.message.reply_text("❌ Plataforma no válida. Intenta de nuevo.")
            return

        set_user_data(telegram_id, "addcuenta_platform_id", str(platform["id"]))
        set_user_data(telegram_id, "addcuenta_platform_name", platform["name"])
        set_user_state(telegram_id, "admin:addcuenta:enter_email")
        await update.message.reply_text(f"✅ Plataforma: {platform['name']}\n\nAhora ingresa el <b>email</b> de la cuenta:", parse_mode="HTML")

    elif state == "admin:addcuenta:enter_email":
        set_user_data(telegram_id, "addcuenta_email", text)
        set_user_state(telegram_id, "admin:addcuenta:enter_password")
        await update.message.reply_text("✅ Email guardado.\n\nAhora ingresa la <b>contraseña</b>:", parse_mode="HTML")

    elif state == "admin:addcuenta:enter_password":
        set_user_data(telegram_id, "addcuenta_password", text)
        set_user_state(telegram_id, "admin:addcuenta:enter_billing")
        await update.message.reply_text("✅ Contraseña guardada.\n\nIngresa la <b>fecha de facturación</b> (DD/MM/YYYY) o escribe 'omitir':", parse_mode="HTML")

    elif state == "admin:addcuenta:enter_billing":
        billing_date = None
        if text.lower() != "omitir":
            try:
                from datetime import datetime
                billing_date = datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                await update.message.reply_text("❌ Formato de fecha inválido. Usa DD/MM/YYYY o escribe 'omitir'.")
                return

        platform_id = get_user_data(telegram_id, "addcuenta_platform_id")
        platform_name = get_user_data(telegram_id, "addcuenta_platform_name")
        email = get_user_data(telegram_id, "addcuenta_email")
        password = get_user_data(telegram_id, "addcuenta_password")

        from database.accounts import create_account
        account = await create_account(platform_id, email, password, billing_date)

        from bot.middleware import clear_user_state, clear_user_data
        clear_user_state(telegram_id)
        clear_user_data(telegram_id)

        if account:
            from database.users import log_admin_action
            await log_admin_action(telegram_id, "add_account", {"platform": platform_name, "email": email})
            account_id = str(account["id"])
            await update.message.reply_text(
                f"✅ <b>Cuenta creada exitosamente</b>\n\n"
                f"📺 Plataforma: {platform_name}\n"
                f"📧 Email: {email}\n"
                f"🔖 ID: <code>{account_id}</code>\n\n"
                f"Para agregar perfiles, usa:\n"
                f"/addexpress {get_user_data(telegram_id, 'addcuenta_platform_id') or 'slug'} NombrePerfil {account_id}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Error al crear la cuenta.")


async def _handle_admin_edit_client_flow(update: Update, context, state: str) -> None:
    """Handle admin edit-client text input (name or phone)."""
    if not update.message or not update.effective_user:
        return
    admin_id = update.effective_user.id
    text = update.message.text.strip()

    from bot.middleware import clear_user_state
    from database.users import update_user_name, update_user_phone, log_admin_action

    # state format: admin:edit_client:<field>:<target_telegram_id>
    parts = state.split(":")
    if len(parts) < 4:
        clear_user_state(admin_id)
        return

    field = parts[2]      # "name" or "phone"
    target_id = int(parts[3])

    if text.lower() == "/cancelar":
        clear_user_state(admin_id)
        await update.message.reply_text("❌ Edición cancelada.")
        return

    if field == "name":
        success = await update_user_name(target_id, text)
        if success:
            await log_admin_action(admin_id, "edit_client_name", {"target": target_id, "name": text})
            await update.message.reply_text(
                f"✅ Nombre actualizado a <b>{text}</b> para el cliente <code>{target_id}</code>.\n\n"
                f"Usa /cliente {target_id} para ver el perfil.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Error al actualizar el nombre.")

    elif field == "phone":
        success = await update_user_phone(target_id, text)
        if success:
            await log_admin_action(admin_id, "edit_client_phone", {"target": target_id, "phone": text})
            await update.message.reply_text(
                f"✅ Teléfono actualizado a <b>{text}</b> para el cliente <code>{target_id}</code>.\n\n"
                f"Usa /cliente {target_id} para ver el perfil.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Error al actualizar el teléfono.")

    clear_user_state(admin_id)


async def _global_error_handler(update: object, context) -> None:
    """Global error handler - log errors and notify user."""
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
    from bot.messages import ERROR_GENERIC
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(ERROR_GENERIC)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    global _telegram_app

    logger.info("Starting StreamVip Bot...")

    # Build telegram application
    _telegram_app = build_telegram_app()
    await _telegram_app.initialize()
    await _telegram_app.start()

    # Set webhook
    webhook_url = f"{settings.APP_URL}/webhook"
    await _telegram_app.bot.set_webhook(
        url=webhook_url,
        secret_token=settings.WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query", "inline_query"],
    )
    logger.info(f"Webhook set to: {webhook_url}")

    # Register initialized bot for outbound notifications
    from services.notification_service import init_notification_bot
    init_notification_bot(_telegram_app.bot)
    logger.info("Notification bot initialized")

    # Start scheduler
    from scheduler.jobs import setup_scheduler
    sched = setup_scheduler()
    sched.start()
    logger.info("Scheduler started")

    yield

    # Shutdown
    logger.info("Shutting down StreamVip Bot...")
    sched.shutdown(wait=False)
    await _telegram_app.stop()
    await _telegram_app.shutdown()


# Create FastAPI app
app = FastAPI(
    title="StreamVip Bot",
    description="Telegram bot for streaming profile rental in Venezuela",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount admin panel
app.include_router(panel_router)


@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram webhook updates."""
    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    try:
        data = await request.json()
        update = Update.de_json(data, _telegram_app.bot)
        await _telegram_app.process_update(update)
        return Response(content="OK", status_code=200)
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        return Response(content="Error", status_code=500)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    from utils.helpers import venezuela_now
    return {
        "status": "ok",
        "service": "StreamVip Bot",
        "timestamp": venezuela_now().isoformat(),
        "bot_running": _telegram_app is not None,
    }


@app.get("/")
async def root() -> dict:
    """Root endpoint."""
    return {"service": "StreamVip Bot", "status": "running"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=settings.DEBUG)
