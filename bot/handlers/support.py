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
from services.gmail_service import get_verification_code
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

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = [s for s in subs if s.get("status") == "active" and s.get("profile_id")]

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

        pin_line = PIN_LINE.format(pin=profile.get("pin")) if profile.get("pin") else ""

        credentials_text = SUPPORT_NO_CREDENTIALS.format(
            platform=f"{platform.get('icon_emoji','')} {platform.get('name','')}",
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

        subs = await get_user_active_subscriptions(str(user["id"]))
        active_subs = [s for s in subs if s.get("status") == "active" and s.get("profile_id")]

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

        await _fetch_verification_code(query, active_subs[0])
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
            await _fetch_verification_code(query, sub)
        else:
            await _send_credentials(query, sub)
    except Exception as e:
        logger.error(f"Error in handle_support_platform_selected: {e}")
        await query.edit_message_text("Error al procesar.")


def from_middleware_get_state(telegram_id: int):
    from bot.middleware import get_user_state
    return get_user_state(telegram_id)


async def _fetch_verification_code(query, sub: dict) -> None:
    """Fetch verification code from Gmail for subscription account."""
    try:
        platform = sub.get("platforms") or {}
        profile_id = sub.get("profile_id")

        if not profile_id:
            await query.edit_message_text("No hay perfil asignado.")
            return

        profile = await get_profile_by_id(str(profile_id))
        account = await get_account_by_id(str((profile or {}).get("account_id", "")))

        if not account:
            await query.edit_message_text("No se encontró la cuenta.")
            return

        platform_name = platform.get("name", "")
        platform_slug = platform.get("slug", "")

        await query.edit_message_text(
            SUPPORT_VERIFICATION_CODE.format(platform=platform_name),
            parse_mode="HTML",
        )

        # Try Gmail API if enabled
        code = None
        if account.get("gmail_api_enabled") and account.get("gmail_credentials"):
            code = await get_verification_code(
                account_email=account.get("email", ""),
                platform=platform_slug,
                credentials_json=account.get("gmail_credentials", {}),
            )

        if code:
            await query.edit_message_text(
                SUPPORT_CODE_FOUND.format(platform=platform_name, code=code),
                parse_mode="HTML",
                reply_markup=support_keyboard(),
            )
        else:
            await query.edit_message_text(
                SUPPORT_CODE_NOT_FOUND.format(platform=platform_name),
                parse_mode="HTML",
                reply_markup=support_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error in _fetch_verification_code: {e}")
        await query.edit_message_text("Error al obtener el código. Contacta a soporte.")


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
        await send_to_admin(
            f"🆘 <b>Usuario solicita soporte</b>\n\n"
            f"👤 Usuario: @{update.effective_user.username or 'sin username'}\n"
            f"🆔 ID: {telegram_id}\n"
            f"📝 Nombre: {update.effective_user.full_name}"
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
