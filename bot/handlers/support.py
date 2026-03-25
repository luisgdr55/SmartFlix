from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import support_keyboard, platform_select_for_support, main_menu_keyboard
from bot.messages import (
    SUPPORT_MENU, SUPPORT_NO_CREDENTIALS, SUPPORT_VERIFICATION_CODE,
    SUPPORT_CODE_FOUND, SUPPORT_CODE_NOT_FOUND, TROUBLESHOOTING, PIN_LINE
)
from database.users import get_user_by_telegram_id
from database.subscriptions import get_user_active_subscriptions
from database.profiles import get_profile_by_id
from database.accounts import get_account_by_id
from services.gemini_service import generate_troubleshooting_response

logger = logging.getLogger(__name__)


async def show_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show support options menu."""
    query = update.callback_query
    message = update.message

    if query:
        await query.answer()
        await query.edit_message_text(
            SUPPORT_MENU,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
        )
    elif message:
        await message.reply_text(
            SUPPORT_MENU,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
        )


async def handle_support_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Option 1: Resend credentials to user."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Error al obtener tu perfil. Usa /start.")
            return

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = []
        for s in subs:
            if s.get("status") != "active" or not s.get("profile_id"):
                continue
            # Double-check end_date regardless of status field
            end_raw = s.get("end_date")
            if end_raw:
                try:
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if end_dt < now:
                        continue  # expired but scheduler hasn't updated yet
                except Exception:
                    pass
            active_subs.append(s)

        if not active_subs:
            await query.edit_message_text(
                "No tienes suscripciones activas con perfil asignado.\n\n"
                "Si crees que hay un error, contacta a soporte.",
                reply_markup=support_keyboard(),
            )
            return

        if len(active_subs) == 1:
            await _send_credentials(query, active_subs[0])
        else:
            await query.edit_message_text(
                "¿Para cuál servicio necesitas las credenciales?",
                parse_mode="HTML",
                reply_markup=platform_select_for_support(active_subs),
            )
    except Exception as e:
        logger.error(f"Error in handle_support_credentials: {e}")
        await query.edit_message_text("Error al obtener credenciales. Contacta a soporte.")


async def _send_credentials(query, sub: dict) -> None:
    """Send credentials for a specific subscription."""
    try:
        profile_id = sub.get("profile_id")
        platform = sub.get("platforms") or {}

        if not profile_id:
            await query.edit_message_text("No hay perfil asignado a este servicio.")
            return

        profile = await get_profile_by_id(str(profile_id))
        if not profile:
            await query.edit_message_text("No se encontró el perfil.")
            return

        account = await get_account_by_id(str(profile.get("account_id", "")))
        if not account:
            await query.edit_message_text("No se encontró la cuenta.")
            return

        platform_str = f"{platform.get('icon_emoji','')} {platform.get('name','')}"

        if profile.get("is_extra_member") and profile.get("extra_email"):
            # Cupo adicional de hogar — tiene sus propias credenciales
            credentials_text = (
                f"🏠 <b>Cupo Adicional de Hogar</b>\n"
                f"📺 Plataforma: {platform_str}\n\n"
                f"📧 <b>Email:</b> <code>{profile.get('extra_email','')}</code>\n"
                f"🔑 <b>Contraseña:</b> <code>{profile.get('extra_password','')}</code>\n\n"
                f"<i>Inicia sesión con estas credenciales en la app de {platform.get('name','')}.</i>"
            )
        else:
            pin_line = PIN_LINE.format(pin=profile.get("pin")) if profile.get("pin") else ""
            credentials_text = SUPPORT_NO_CREDENTIALS.format(
                platform=platform_str,
                profile_name=profile.get("profile_name", "N/A"),
                email=account.get("email", ""),
                password=account.get("password", ""),
                pin_line=pin_line,
            )

        await query.edit_message_text(
            credentials_text,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in _send_credentials: {e}")
        await query.edit_message_text("Error al obtener credenciales.")


async def handle_support_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Option 2: Get verification code via Gmail API."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Error de usuario. Usa /start.")
            return

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = []
        for s in subs:
            if s.get("status") != "active" or not s.get("profile_id"):
                continue
            end_raw = s.get("end_date")
            if end_raw:
                try:
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if end_dt < now:
                        continue
                except Exception:
                    pass
            active_subs.append(s)

        if not active_subs:
            await query.edit_message_text(
                "No tienes suscripciones activas.",
                reply_markup=support_keyboard(),
            )
            return

        # Show platform selection if multiple
        if len(active_subs) > 1:
            await query.edit_message_text(
                "¿Para cuál plataforma necesitas el código de verificación?",
                reply_markup=platform_select_for_support(active_subs),
            )
            from bot.middleware import set_user_state
            set_user_state(telegram_id, "support:verification_code")
            return

        await _fetch_verification_code(query, active_subs[0], telegram_id)
    except Exception as e:
        logger.error(f"Error in handle_support_verification_code: {e}")
        await query.edit_message_text("Error al procesar. Intenta de nuevo.")


async def handle_support_platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle platform selection in support flow."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    parts = query.data.split(":")
    # format: support:platform:{platform_id}:{sub_id}
    if len(parts) < 4:
        return

    sub_id = parts[3]
    state = from_middleware_get_state(update.effective_user.id)

    try:
        from database.subscriptions import get_subscription_by_id
        sub = await get_subscription_by_id(sub_id)
        if not sub:
            await query.edit_message_text("Suscripción no encontrada.")
            return

        if state == "support:verification_code":
            await _fetch_verification_code(query, sub, update.effective_user.id)
        else:
            await _send_credentials(query, sub)
    except Exception as e:
        logger.error(f"Error in handle_support_platform_selected: {e}")
        await query.edit_message_text("Error al procesar.")


def from_middleware_get_state(telegram_id: int):
    from bot.middleware import get_user_state
    return get_user_state(telegram_id)


async def _fetch_verification_code(query, sub: dict, telegram_id: int) -> None:
    """
    Start an async verification code fetch for the given subscription.

    - Shows an immediate "searching..." message to the client.
    - Polls the central IMAP inbox every 15 s for up to 4 minutes.
    - If code found → sends it to the client automatically.
    - If not found → notifies admin with a one-click "Send code" button.
    """
    import asyncio
    import time
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    from database.verification import (
        create_verification_request,
        mark_request_sent,
        mark_request_pending_admin,
    )
    from services.imap_reader import poll_for_code
    from services.notification_service import send_to_admin, send_to_user

    platform = sub.get("platforms") or {}
    platform_name = platform.get("name", "la plataforma")
    platform_emoji = platform.get("icon_emoji", "📺")
    platform_slug = platform.get("slug", "")
    sub_id = str(sub.get("id", ""))

    try:
        user = await get_user_by_telegram_id(telegram_id)
        user_id = str(user["id"]) if user else ""
        client_name = (user or {}).get("name", "Cliente")
    except Exception:
        user_id = ""
        client_name = "Cliente"

    # Create DB request record for audit trail and admin fallback
    request = await create_verification_request(
        user_id=user_id,
        client_telegram_id=telegram_id,
        subscription_id=sub_id,
        platform_name=platform_name,
        platform_emoji=platform_emoji,
        platform_slug=platform_slug,
        client_name=client_name,
    )
    request_id = str(request["id"]) if request else "unknown"
    since_ts = time.time()

    # Acknowledge immediately so the client isn't left waiting
    await query.edit_message_text(
        f"🔍 <b>Buscando tu código de verificación...</b>\n\n"
        f"{platform_emoji} {platform_name}\n\n"
        f"⏳ Puede tardar hasta 4 minutos si el correo aún no llegó.\n"
        f"Te lo enviamos en cuanto lo tengamos. <b>No necesitas hacer nada más.</b>",
        parse_mode="HTML",
    )

    # Background task: poll IMAP then notify client or escalate to admin
    async def _bg_task():
        code = await poll_for_code(platform_slug, since_ts, timeout=240)

        if code:
            await send_to_user(
                telegram_id,
                f"🔑 <b>Tu código de verificación</b>\n\n"
                f"{platform_emoji} <b>{platform_name}</b>\n\n"
                f"<code>{code}</code>\n\n"
                f"⚠️ Úsalo de inmediato, expira pronto.\n"
                f"¿Más problemas? Regresa a Soporte y solicita un nuevo código.",
            )
            if request:
                await mark_request_sent(request_id, code)
        else:
            # Escalate: notify admin and keep client informed
            if request:
                await mark_request_pending_admin(request_id)

            await send_to_user(
                telegram_id,
                f"⏳ <b>Código en camino</b>\n\n"
                f"No pudimos obtenerlo automáticamente esta vez.\n"
                f"Nuestro equipo lo está revisando y te lo enviará en breve.\n\n"
                f"{platform_emoji} <b>{platform_name}</b>",
            )

            await send_to_admin(
                f"🔑 <b>Solicitud de código de verificación</b>\n\n"
                f"👤 Cliente: <b>{client_name}</b>\n"
                f"📺 Plataforma: {platform_emoji} {platform_name}\n"
                f"🆔 Request: <code>{request_id}</code>\n\n"
                f"El sistema no encontró el código en 4 min.\n"
                f"Revisa el correo reenviado y usa el botón:",
                keyboard=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✉️ Ingresar y enviar código",
                        callback_data=f"verif:send:{request_id}:{telegram_id}",
                    )
                ]]),
            )

    asyncio.create_task(_bg_task())


async def handle_support_troubleshooting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Option 3: Show troubleshooting guide."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Usa /start para registrarte.")
            return

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = [s for s in subs if s.get("status") == "active"]

        if not active_subs:
            # Generic guide
            await query.edit_message_text(
                "🔧 <b>Guía General de Problemas</b>\n\n"
                "1️⃣ Verifica tu conexión a internet\n"
                "2️⃣ Cierra y vuelve a abrir la app\n"
                "3️⃣ Borra el caché de la aplicación\n"
                "4️⃣ Reinstala la app si es necesario\n"
                "5️⃣ Contacta a soporte si persiste\n\n"
                "Para ayuda específica por plataforma, necesitas una suscripción activa.",
                parse_mode="HTML",
                reply_markup=support_keyboard(),
            )
            return

        # Show guide for first active subscription
        sub = active_subs[0]
        platform = sub.get("platforms") or {}
        platform_slug = platform.get("slug", "netflix")
        guide = TROUBLESHOOTING.get(platform_slug, TROUBLESHOOTING.get("netflix", ""))

        await query.edit_message_text(
            guide,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in handle_support_troubleshooting: {e}")
        await query.edit_message_text("Error al cargar guía.")


async def handle_support_profile_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Option 4: Check profile status."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            await query.edit_message_text("Usa /start para registrarte.")
            return

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = [s for s in subs if s.get("status") == "active"]

        if not active_subs:
            await query.edit_message_text(
                "No tienes suscripciones activas.",
                reply_markup=support_keyboard(),
            )
            return

        status_text = "📊 <b>Estado de tus Perfiles</b>\n\n"
        for sub in active_subs:
            platform = sub.get("platforms") or {}
            profile = sub.get("profiles") or {}
            icon = platform.get("icon_emoji", "📺")
            name = platform.get("name", "?")
            profile_name = profile.get("profile_name", "N/A")
            sub_status = sub.get("status", "?")

            from datetime import datetime
            from utils.helpers import days_remaining, format_datetime_vzla
            end_date_str = sub.get("end_date")
            end_dt = None
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            days_left = days_remaining(end_dt) if end_dt else 0
            status_emoji = "✅" if sub_status == "active" else "⚠️"

            status_text += (
                f"{status_emoji} {icon} <b>{name}</b>\n"
                f"   👤 Perfil: {profile_name}\n"
                f"   ⏰ Días restantes: {days_left}\n\n"
            )

        await query.edit_message_text(
            status_text,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in handle_support_profile_status: {e}")
        await query.edit_message_text("Error al obtener estado.")


async def handle_contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Option 5: Escalate to admin."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    try:
        from services.notification_service import send_to_admin
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        username = update.effective_user.username
        tg_link = f"https://t.me/{username}" if username else f"tg://user?id={telegram_id}"
        link_label = f"@{username}" if username else f"ID {telegram_id}"
        await send_to_admin(
            f"🆘 <b>Usuario solicita soporte</b>\n\n"
            f"👤 Nombre: {update.effective_user.full_name}\n"
            f"🔗 Contacto: {tg_link}\n"
            f"🆔 ID: <code>{telegram_id}</code>",
            keyboard=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"💬 Abrir chat con {link_label}", url=tg_link)
            ]])
        )

        await query.edit_message_text(
            "👨‍💼 <b>Soporte notificado</b>\n\n"
            "Hemos notificado a nuestro equipo de soporte. "
            "Te contactaremos a la brevedad posible.\n\n"
            "⏰ Horario de atención: Lunes a Domingo 8AM - 10PM",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in handle_contact_admin: {e}")
        await query.edit_message_text("Error al contactar soporte. Intenta más tarde.")
