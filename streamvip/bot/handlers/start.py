from __future__ import annotations

import logging
import random

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu_keyboard, share_contact_keyboard, remove_keyboard
from bot.messages import WELCOME_NEW_USER, NAME_REQUEST, NAME_CONFIRMED, MAIN_MENU
from bot.middleware import (
    check_user_blocked, get_user_state, set_user_state,
    clear_user_state, rate_limit_check
)
from database.users import (
    get_or_create_user, get_user_by_telegram_id, update_user_name,
    find_user_by_phone, link_user_telegram_id,
)
from database.analytics import get_platform_availability
from services.gemini_service import extract_user_name
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)

RETURNING_GREETINGS = [
    "¡Bienvenido de vuelta, <b>{name}</b>! 👋",
    "¡Hola otra vez, <b>{name}</b>! 😊",
    "¡Qué bueno verte, <b>{name}</b>! 🎬",
    "¡Aquí estamos, <b>{name}</b>! ¿Qué vas a ver hoy? 🍿",
    "¡Hola <b>{name}</b>! Listo para el streaming 🚀",
]


async def _build_availability_text() -> str:
    """Build availability summary text."""
    try:
        availability = await get_platform_availability()
        lines = []
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            monthly = p.get("monthly_available", 0)
            express = p.get("express_available", 0)
            lines.append(f"{icon} {name}: {monthly} mensual | {express} express")
        return "\n".join(lines) if lines else "Cargando disponibilidad..."
    except Exception:
        return "Ver disponibilidad en el menú"


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    username = update.effective_user.username
    full_name = update.effective_user.full_name

    # Rate limit check
    if await rate_limit_check(telegram_id):
        await update.message.reply_text("⚠️ Demasiadas solicitudes. Espera un momento.")
        return

    # Check if blocked
    if await check_user_blocked(telegram_id):
        await update.message.reply_text("❌ Tu cuenta ha sido suspendida. Contacta a soporte.")
        return

    try:
        user = await get_or_create_user(telegram_id, username, full_name)

        # New user with no username — check if they're pre-registered by phone
        if not user.get("name") and not username:
            set_user_state(telegram_id, "awaiting_phone_verify")
            await update.message.reply_text(
                "👋 ¡Hola! Para verificar si ya tienes una cuenta con nosotros, "
                "por favor comparte tu número de teléfono:",
                reply_markup=share_contact_keyboard(),
            )
            return

        # Check if user needs to provide their name
        if not user.get("name"):
            set_user_state(telegram_id, "awaiting_name")
            await update.message.reply_text(WELCOME_NEW_USER, parse_mode="HTML")
            return

        # Returning user - check for debt or expired subscriptions before showing menu
        name = user.get("name", full_name or "amigo/a")
        user_id = str(user.get("id", ""))

        if user_id:
            try:
                import html as _html
                from database.subscriptions import get_user_attention_subscriptions
                attention = await get_user_attention_subscriptions(user_id)
                pending_subs = attention["pending"]
                expired_subs = attention["expired"]

                if pending_subs or expired_subs:
                    safe_name = _html.escape(name)
                    alert_lines = [f"⚠️ <b>Hola {safe_name}, hay cosas que necesitan tu atención:</b>\n"]
                    for s in pending_subs:
                        plat = _html.escape((s.get("platforms") or {}).get("name", "Plataforma"))
                        icon = (s.get("platforms") or {}).get("icon_emoji", "📺")
                        try:
                            price_bs = float(s.get("price_bs") or 0)
                        except (TypeError, ValueError):
                            price_bs = 0.0
                        alert_lines.append(
                            f"{icon} <b>{plat}</b> — pago pendiente\n"
                            f"   💰 Monto: Bs {price_bs:,.0f}\n"
                            f"   📌 Envía tu comprobante de pago para activar."
                        )
                    for s in expired_subs:
                        plat = _html.escape((s.get("platforms") or {}).get("name", "Plataforma"))
                        icon = (s.get("platforms") or {}).get("icon_emoji", "📺")
                        _ed = (s.get("end_date") or "")[:10]
                        end = f"{_ed[8:10]}-{_ed[5:7]}-{_ed[0:4]}" if len(_ed) >= 10 else _ed
                        alert_lines.append(
                            f"{icon} <b>{plat}</b> — vencida el {end}\n"
                            f"   🔄 Renueva para seguir disfrutando."
                        )
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    alert_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Renovar / Pagar ahora", callback_data="menu:subscribe")],
                        [InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")],
                        [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
                    ])
                    await update.message.reply_text(
                        "\n\n".join(alert_lines),
                        parse_mode="HTML",
                        reply_markup=alert_keyboard,
                    )
                    return
            except Exception as alert_err:
                logger.error(f"Debt/expiry alert failed for {telegram_id}: {alert_err}", exc_info=True)

        # Normal menu
        greeting = random.choice(RETURNING_GREETINGS).format(name=name)
        availability = await _build_availability_text()
        menu_text = greeting + "\n\n" + MAIN_MENU.format(name=name, availability=availability)

        await update.message.reply_text(
            menu_text,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error in start_handler: {e}")
        await update.message.reply_text(
            "¡Hola! 👋 Hubo un error al cargar tu perfil. Intenta de nuevo.",
            reply_markup=main_menu_keyboard(),
        )


async def handle_contact_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle when user shares their phone number for pre-registration linking."""
    if not update.message or not update.message.contact or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    username = update.effective_user.username
    contact = update.message.contact
    phone = contact.phone_number  # e.g. "+584121234567"

    try:
        pre_user = await find_user_by_phone(phone)

        if pre_user:
            await link_user_telegram_id(pre_user["id"], telegram_id, username)
            name = pre_user.get("name", "")
            availability = await _build_availability_text()
            greeting = f"✅ ¡Te encontré, <b>{name}</b>! Tu cuenta ha sido vinculada.\n\n"
            menu_text = greeting + MAIN_MENU.format(name=name, availability=availability)
            await update.message.reply_text(
                menu_text,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        else:
            # Not pre-registered — create new user and ask for name
            from database.users import update_user_phone
            user = await get_or_create_user(telegram_id, username, update.effective_user.full_name)
            await update_user_phone(telegram_id, phone)
            await update.message.reply_text(
                remove_keyboard(),
            )
            if not user.get("name"):
                set_user_state(telegram_id, "awaiting_name")
                await update.message.reply_text(WELCOME_NEW_USER, parse_mode="HTML",
                                                reply_markup=remove_keyboard())
            else:
                name = user.get("name", "")
                availability = await _build_availability_text()
                menu_text = MAIN_MENU.format(name=name, availability=availability)
                await update.message.reply_text(menu_text, parse_mode="HTML",
                                                reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error in handle_contact_shared: {e}")
        await update.message.reply_text(
            "Hubo un error. Intenta de nuevo con /start",
            reply_markup=remove_keyboard(),
        )


async def handle_name_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the user's name response after /start for new users."""
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    text = update.message.text

    state = get_user_state(telegram_id)
    if state != "awaiting_name":
        return

    if not text or len(text.strip()) < 2:
        await update.message.reply_text(
            "Por favor dime tu nombre 😊 (mínimo 2 caracteres)"
        )
        return

    # Try to extract name with Gemini, fallback to raw text
    name = await extract_user_name(text)
    if not name:
        # Use first word of text as name
        name = text.strip().split()[0][:50]

    # Capitalize
    name = name.strip().title()

    # Save name
    await update_user_name(telegram_id, name)
    clear_user_state(telegram_id)

    # Confirm and show menu
    availability = await _build_availability_text()
    confirm_text = NAME_CONFIRMED.format(name=name)
    menu_text = confirm_text + "\n\n" + MAIN_MENU.format(name=name, availability=availability)

    await update.message.reply_text(
        menu_text,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
