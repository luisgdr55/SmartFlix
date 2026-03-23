"""
AI-powered free-text message handler.
Uses OpenRouter (Gemini) to understand user intent and route to the correct flow,
or generate a conversational response when the intent is informational.
"""
from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from services.gemini_service import (
    interpret_user_intent,
    store_conversation_message,
    get_conversation_context,
    _call,
)
from bot.keyboards import main_menu_keyboard, platforms_keyboard, support_keyboard

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT — service context for conversational responses
# ─────────────────────────────────────────────────────────────────
def _build_system_prompt(user_name: str, active_subs: list[dict]) -> str:
    subs_text = ""
    if active_subs:
        lines = []
        for s in active_subs:
            p = (s.get("platforms") or {})
            lines.append(f"- {p.get('icon_emoji','')} {p.get('name','?')} ({s.get('plan_type','?')})")
        subs_text = "Suscripciones activas del cliente:\n" + "\n".join(lines)
    else:
        subs_text = "El cliente no tiene suscripciones activas actualmente."

    return f"""Eres el asistente virtual de StreamVip Venezuela, un servicio de streaming premium.

Información del servicio:
- Ofrecemos acceso a Netflix, Disney+, Max, Paramount+, Amazon Prime y más plataformas.
- Planes disponibles: Mensual (~30 días), Semanal (7 días), Express (24 horas).
- Precios en bolívares (Bs) según tasa del día. El cliente paga por Pago Móvil o transferencia.
- Una vez confirmado el pago, el cliente recibe sus credenciales por este mismo chat.

Cliente: {user_name or "Estimado cliente"}
{subs_text}

Instrucciones:
- Responde en español venezolano, tono amigable y cercano, como el dueño del servicio.
- Sé conciso: máximo 4 oraciones por respuesta.
- Si el cliente quiere suscribirse, dile que toque el botón correspondiente del menú.
- Si el cliente tiene dudas sobre precios, dile que puede ver las opciones en el menú.
- NO inventes precios específicos ni credenciales.
- NO uses asteriscos ni markdown — solo texto plano con emojis si aplica.
- Si no puedes ayudar, indica que puede contactar a soporte con el botón del menú."""


async def _get_user_context(telegram_id: int) -> tuple[str, list[dict]]:
    """Return (user_name, active_subscriptions) for the given telegram_id."""
    try:
        from database.users import get_user_by_telegram_id
        from database.subscriptions import get_user_active_subscriptions
        from datetime import datetime, timezone

        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            return "", []

        name = user.get("name") or user.get("username") or ""
        subs_raw = await get_user_active_subscriptions(str(user["id"]))
        now = datetime.now(timezone.utc)
        active = []
        for s in subs_raw:
            if s.get("status") != "active":
                continue
            end_raw = s.get("end_date", "")
            if end_raw:
                try:
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if end_dt < now:
                        continue
                except Exception:
                    pass
            active.append(s)
        return name, active
    except Exception as e:
        logger.warning(f"Could not load user context: {e}")
        return "", []


async def _chat_response(system_prompt: str, conversation: list[dict], user_message: str) -> str:
    """Generate a full conversational response."""
    messages = [{"role": "system", "content": system_prompt}]
    # Include last 6 turns of conversation history
    for m in conversation[-6:]:
        messages.append(m)
    messages.append({"role": "user", "content": user_message})
    try:
        return await _call(messages, temperature=0.5, max_tokens=300)
    except Exception as e:
        logger.error(f"chat_response error: {e}")
        return "Lo siento, tuve un problema. ¿En qué te puedo ayudar? Usa el menú de abajo 👇"


async def _send_platform_menu(message, plan_type: str, intro: str) -> None:
    """Send platform selection keyboard for a given plan type."""
    from database.analytics import get_platform_availability
    try:
        availability = await get_platform_availability()
        await message.reply_text(
            intro,
            parse_mode="HTML",
            reply_markup=platforms_keyboard(availability, plan_type),
        )
    except Exception as e:
        logger.error(f"Error sending platform menu: {e}")
        await message.reply_text(
            "Usa el menú para elegir tu plataforma 👇",
            reply_markup=main_menu_keyboard(),
        )


# ─────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────
async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle any free-text message.
    1. Detect intent via LLM.
    2. If actionable (subscribe/express/week/support/renewal) → route with inline keyboard.
    3. Otherwise → generate conversational response.
    """
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.message.chat_id, action=ChatAction.TYPING
    )

    # Load conversation history and user context in parallel
    import asyncio
    conv_task = asyncio.create_task(asyncio.to_thread(get_conversation_context, telegram_id))
    ctx_task = asyncio.create_task(_get_user_context(telegram_id))
    conversation = await conv_task
    user_name, active_subs = await ctx_task

    # Store user message
    store_conversation_message(telegram_id, "user", text)

    # Detect intent
    intent_data = await interpret_user_intent(text, conversation)
    intent = intent_data.get("intent", "other")
    platform = intent_data.get("platform")  # e.g. "netflix", "disney", or None
    confidence = intent_data.get("confidence", "baja")

    logger.info(f"AI intent [{telegram_id}]: {intent} | platform: {platform} | conf: {confidence}")

    bot_reply = None

    # ── Route based on intent ──────────────────────────────────────
    if intent == "subscribe" and confidence != "baja":
        platform_hint = f" de <b>{platform.capitalize()}</b>" if platform else ""
        await _send_platform_menu(
            update.message,
            "monthly",
            f"¡Claro! 🎬 Elige la plataforma{platform_hint} para tu suscripción mensual:",
        )
        bot_reply = f"Menú de suscripción mensual mostrado{' (' + platform + ')' if platform else ''}."

    elif intent == "express" and confidence != "baja":
        platform_hint = f" de <b>{platform.capitalize()}</b>" if platform else ""
        await _send_platform_menu(
            update.message,
            "express",
            f"⚡ ¡Express 24h!{' ' + platform_hint if platform else ''} Elige la plataforma:",
        )
        bot_reply = "Menú express mostrado."

    elif intent == "week" and confidence != "baja":
        platform_hint = f" de <b>{platform.capitalize()}</b>" if platform else ""
        await _send_platform_menu(
            update.message,
            "week",
            f"📅 Pack semanal{platform_hint}. Elige la plataforma:",
        )
        bot_reply = "Menú semanal mostrado."

    elif intent == "support" and confidence != "baja":
        from bot.keyboards import support_keyboard as sk
        await update.message.reply_text(
            "🆘 <b>Soporte StreamVip</b>\n\n¿Con qué te puedo ayudar?",
            parse_mode="HTML",
            reply_markup=sk(),
        )
        bot_reply = "Menú de soporte mostrado."

    elif intent in ("renewal", "cancel") and confidence != "baja":
        await update.message.reply_text(
            "📋 Aquí puedes ver y renovar tus servicios activos:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Ver mis servicios", callback_data="menu:my_services")
            ]]),
        )
        bot_reply = "Menú de servicios mostrado."

    else:
        # Conversational response
        system_prompt = _build_system_prompt(user_name, active_subs)
        response = await _chat_response(system_prompt, conversation, text)
        await update.message.reply_text(
            response,
            reply_markup=main_menu_keyboard(),
        )
        bot_reply = response

    # Store bot response in conversation history
    if bot_reply:
        store_conversation_message(telegram_id, "assistant", bot_reply)
