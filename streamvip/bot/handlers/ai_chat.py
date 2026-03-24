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
# PRICES CONTEXT — real prices from DB for LLM
# ─────────────────────────────────────────────────────────────────
async def _get_prices_context() -> str:
    """Fetch real platform prices from DB for LLM context."""
    try:
        from database import get_supabase
        from services.exchange_service import get_current_rate
        sb = get_supabase()
        result = sb.table("platforms").select(
            "name, icon_emoji, monthly_price_usd, express_price_usd, week_price_usd, slug"
        ).eq("is_active", True).order("name").execute()
        rate_data = await get_current_rate()
        rate = float((rate_data or {}).get("usd_binance") or 36.0)
        lines = []
        for p in (result.data or []):
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            parts = []
            if p.get("monthly_price_usd"):
                usd = float(p["monthly_price_usd"])
                parts.append(f"Mensual ${usd:.2f}/Bs {usd*rate:,.0f}")
            if p.get("week_price_usd"):
                usd = float(p["week_price_usd"])
                parts.append(f"Semanal ${usd:.2f}/Bs {usd*rate:,.0f}")
            if p.get("express_price_usd"):
                usd = float(p["express_price_usd"])
                parts.append(f"Express 24h ${usd:.2f}/Bs {usd*rate:,.0f}")
            if parts:
                lines.append(f"{icon} {name}: {' | '.join(parts)}")
        return "\n".join(lines) if lines else "Precios no disponibles"
    except Exception as e:
        logger.warning(f"_get_prices_context error: {e}")
        return "Precios no disponibles en este momento"


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT — service context for conversational responses
# ─────────────────────────────────────────────────────────────────
def _build_system_prompt(user_name: str, active_subs: list[dict], prices_text: str = "") -> str:
    subs_text = ""
    if active_subs:
        lines = []
        for s in active_subs:
            p = (s.get("platforms") or {})
            lines.append(f"- {p.get('icon_emoji','')} {p.get('name','?')} ({s.get('plan_type','?')})")
        subs_text = "Suscripciones activas del cliente:\n" + "\n".join(lines)
    else:
        subs_text = "El cliente no tiene suscripciones activas actualmente."

    prices_section = f"\nPrecios actuales (REALES, puedes citarlos):\n{prices_text}" if prices_text else ""

    return f"""Eres el asistente virtual de SmartFlixVe, un servicio de streaming premium en Venezuela.

Información del servicio:
- Ofrecemos acceso a Netflix, Disney+, Max, Paramount+, Amazon Prime y más plataformas.
- Planes disponibles: Mensual (~30 días), Semanal (7 días), Express (24 horas).
- Precios en bolívares (Bs) según tasa del día. El cliente paga por Pago Móvil o transferencia.
- Una vez confirmado el pago, el cliente recibe sus credenciales por este mismo chat.

Cliente: {user_name or "Estimado cliente"}
{subs_text}
{prices_section}

Instrucciones:
- Responde en español venezolano, tono amigable y cercano, como el dueño del servicio.
- Sé conciso: máximo 5 oraciones por respuesta.
- Si el cliente pregunta precios, respóndele con los precios reales que tienes arriba.
- Si el cliente quiere suscribirse, dile que toque el botón del menú para iniciar su pedido.
- Usa HTML básico (<b>texto</b>) para negritas cuando sea útil.
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
    2. If actionable → route to correct handler.
    3. Otherwise → generate conversational response.
    """
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        await context.bot.send_chat_action(
            chat_id=update.message.chat_id, action=ChatAction.TYPING
        )
    except Exception:
        pass

    try:
        import asyncio
        conv_task = asyncio.create_task(asyncio.to_thread(get_conversation_context, telegram_id))
        ctx_task = asyncio.create_task(_get_user_context(telegram_id))
        prices_task = asyncio.create_task(_get_prices_context())
        conversation = await conv_task
        user_name, active_subs = await ctx_task
        prices_text = await prices_task
    except Exception as e:
        logger.warning(f"Context load error: {e}")
        conversation, user_name, active_subs, prices_text = [], "", [], "Precios no disponibles"

    store_conversation_message(telegram_id, "user", text)

    intent_data = await interpret_user_intent(text, conversation)
    intent = intent_data.get("intent", "other")
    platform = intent_data.get("platform")
    plan_type_hint = intent_data.get("plan_type")
    confidence = intent_data.get("confidence", "baja")

    logger.info(f"AI intent [{telegram_id}]: {intent} | platform: {platform} | conf: {confidence}")

    bot_reply = None

    try:
        if intent == "subscribe" and confidence != "baja":
            platform_hint = f" de <b>{platform.capitalize()}</b>" if platform else ""
            await _send_platform_menu(
                update.message,
                "monthly",
                f"¡Claro! 🎬 Elige la plataforma{platform_hint} para tu suscripción mensual:",
            )
            bot_reply = f"Menú mensual mostrado{' (' + platform + ')' if platform else ''}."

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
                "🆘 <b>Soporte SmartFlixVe</b>\n\n¿Con qué te puedo ayudar?",
                parse_mode="HTML",
                reply_markup=sk(),
            )
            bot_reply = "Menú soporte mostrado."

        elif intent == "info" and confidence != "baja":
            if platform:
                plat_cap = platform.capitalize()
                plat_lines = [
                    l for l in prices_text.split("\n")
                    if platform.lower() in l.lower() or plat_cap.lower() in l.lower()
                ]
                if plat_lines:
                    response = (
                        f"🎬 Precios de <b>{plat_cap}</b>:\n\n"
                        + "\n".join(plat_lines)
                        + "\n\n¿Cuál plan te interesa? Selecciona abajo 👇"
                    )
                else:
                    response = f"¡Tenemos <b>{plat_cap}</b> disponible! Elige tu plan abajo 👇"
                from database.analytics import get_platform_availability
                availability = await get_platform_availability()
                await update.message.reply_text(
                    response, parse_mode="HTML",
                    reply_markup=platforms_keyboard(availability, "monthly"),
                )
            else:
                if prices_text and "no disponible" not in prices_text:
                    response = f"🎬 <b>Precios actuales de SmartFlixVe:</b>\n\n{prices_text}\n\n¿Cuál plataforma te interesa?"
                else:
                    response = "¡Tenemos varias plataformas disponibles! Selecciona abajo 👇"
                await update.message.reply_text(
                    response, parse_mode="HTML",
                    reply_markup=main_menu_keyboard(),
                )
            bot_reply = f"Precios mostrados{' de ' + platform if platform else ''}."

        elif intent == "multi_order" and confidence != "baja":
            from services.gemini_service import extract_order_items
            from services.cart_service import save_cart
            from services.exchange_service import get_current_rate
            from database import get_supabase
            from bot.keyboards import cart_keyboard

            items_raw = await extract_order_items(text)
            if not items_raw:
                platforms_list = intent_data.get("platforms") or []
                if isinstance(platforms_list, list):
                    items_raw = [{"platform": p, "plan_type": plan_type_hint or "monthly"} for p in platforms_list if p]

            if not items_raw:
                await _send_platform_menu(update.message, "monthly", "¡Claro! ¿Qué plataformas quieres contratar?")
                bot_reply = "Menú mostrado."
            else:
                sb = get_supabase()
                rate_data = await get_current_rate()
                rate = float((rate_data or {}).get("usd_binance") or 36.0)

                cart_items = []
                not_found = []
                for raw_item in items_raw:
                    slug_q = raw_item.get("platform", "").lower().strip()
                    item_plan = raw_item.get("plan_type", "monthly")
                    res = sb.table("platforms").select("*").eq("is_active", True).ilike("slug", f"%{slug_q}%").execute()
                    if not res.data:
                        res = sb.table("platforms").select("*").eq("is_active", True).ilike("name", f"%{slug_q}%").execute()
                    if not res.data:
                        not_found.append(slug_q)
                        continue
                    plat = res.data[0]
                    price_field = {"monthly": "monthly_price_usd", "express": "express_price_usd", "week": "week_price_usd"}.get(item_plan, "monthly_price_usd")
                    price_usd = float(plat.get(price_field) or 0)
                    cart_items.append({
                        "platform_id": str(plat["id"]),
                        "name": plat.get("name", ""),
                        "emoji": plat.get("icon_emoji", "📺"),
                        "plan_type": item_plan,
                        "price_usd": price_usd,
                        "price_bs": round(price_usd * rate, 2),
                        "rate_used": rate,
                    })

                if not cart_items:
                    await update.message.reply_text(
                        "No encontré esas plataformas. Dime cuáles quieres y te ayudo 😊",
                        reply_markup=main_menu_keyboard(),
                    )
                    bot_reply = "Plataformas no encontradas."
                else:
                    save_cart(telegram_id, cart_items)
                    total_usd = sum(i["price_usd"] for i in cart_items)
                    total_bs = sum(i["price_bs"] for i in cart_items)
                    lines = ["🛒 <b>Tu carrito:</b>\n"]
                    for item in cart_items:
                        plan_label = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}.get(item["plan_type"], item["plan_type"])
                        lines.append(f"{item['emoji']} <b>{item['name']}</b> — {plan_label}: ${item['price_usd']:.2f} / Bs {item['price_bs']:,.0f}")
                    lines.append(f"\n💰 <b>Total: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")
                    if not_found:
                        lines.append(f"\n⚠️ No encontré: {', '.join(not_found)}")
                    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=cart_keyboard())
                    bot_reply = f"Carrito con {len(cart_items)} servicios."

        elif intent == "credentials" and confidence != "baja":
            from database.users import get_user_by_telegram_id
            from database.subscriptions import get_user_active_subscriptions
            from database.accounts import get_account_by_id
            from database import get_supabase

            user = await get_user_by_telegram_id(telegram_id)
            subs = await get_user_active_subscriptions(str(user["id"])) if user else []
            active = [s for s in subs if s.get("status") == "active"]

            if not active:
                await update.message.reply_text(
                    f"Hola {user_name or 'amigo/a'}! 👋 No tienes suscripciones activas.\n\n"
                    "¿Quieres contratar un servicio? Usa el menú abajo 👇",
                    reply_markup=main_menu_keyboard(),
                )
            else:
                sb = get_supabase()
                lines = [f"🔐 <b>Tus datos de acceso, {user_name or 'amigo/a'}:</b>\n"]
                for sub in active:
                    plat = sub.get("platforms") or {}
                    icon = plat.get("icon_emoji", "📺")
                    name = plat.get("name", "?")
                    end_raw = (sub.get("end_date") or "")[:10]
                    profile_id = sub.get("profile_id")
                    email, password, pin, profile_name = "—", "—", None, None
                    if profile_id:
                        prof_res = sb.table("profiles").select("profile_name, pin, account_id").eq("id", profile_id).limit(1).execute()
                        if prof_res.data:
                            prof = prof_res.data[0]
                            profile_name = prof.get("profile_name")
                            pin = prof.get("pin")
                            acc_id = prof.get("account_id")
                            if acc_id:
                                acc = await get_account_by_id(str(acc_id))
                                if acc:
                                    email = acc.get("email", "—")
                                    password = acc.get("password", "—")
                    block = [f"\n{icon} <b>{name}</b>"]
                    block.append(f"📧 Email: <code>{email}</code>")
                    block.append(f"🔑 Contraseña: <code>{password}</code>")
                    if profile_name:
                        block.append(f"👤 Perfil: <b>{profile_name}</b>")
                    if pin:
                        block.append(f"🔢 PIN: <code>{pin}</code>")
                    if end_raw:
                        block.append(f"📅 Vence: {end_raw[8:10]}-{end_raw[5:7]}-{end_raw[0:4]}")
                    lines.append("\n".join(block))
                lines.append("\n\n⚠️ <i>No compartas estos datos con nadie.</i>")
                await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu_keyboard())
            bot_reply = "Credenciales mostradas."

        elif intent == "availability" and confidence != "baja":
            from database.analytics import get_platform_availability
            availability = await get_platform_availability()
            plan_labels = {"monthly": "mensual", "express": "express 24h", "week": "semanal"}
            plan_key_map = {"monthly": "monthly_available", "express": "express_available", "week": "week_available"}

            if platform:
                match = next(
                    (p for p in availability if platform.lower() in p.get("name", "").lower() or platform.lower() in p.get("slug", "").lower()),
                    None,
                )
                if not match:
                    response = f"No encontré la plataforma <b>{platform.capitalize()}</b>. ¿Quizás quisiste decir otra?"
                elif plan_type_hint and plan_type_hint in plan_key_map:
                    count = match.get(plan_key_map[plan_type_hint], 0)
                    label = plan_labels[plan_type_hint]
                    icon = match.get("icon_emoji", "📺")
                    pname = match.get("name", "")
                    if count > 0:
                        response = f"{icon} <b>{pname}</b> — plan {label}:\n\n✅ Hay <b>{count} pantalla{'s' if count != 1 else ''} disponible{'s' if count != 1 else ''}</b>.\n\n¿Te animas? Elige tu plan abajo 👇"
                    else:
                        response = f"{icon} <b>{pname}</b> — plan {label}:\n\n😔 Por el momento <b>no hay disponibilidad</b> en ese plan.\nPuedes revisar otros planes abajo."
                else:
                    icon = match.get("icon_emoji", "📺")
                    pname = match.get("name", "")
                    m = match.get("monthly_available", 0)
                    e = match.get("express_available", 0)
                    w = match.get("week_available", 0)
                    def _slot(n): return f"<b>{n}</b> disponible{'s' if n != 1 else ''}" if n > 0 else "<b>sin stock</b>"
                    response = f"{icon} <b>Disponibilidad de {pname}:</b>\n\n📅 Mensual: {_slot(m)}\n📆 Semanal: {_slot(w)}\n⚡ Express 24h: {_slot(e)}\n\n¿Cuál plan te interesa?"
            else:
                avail_lines = ["📊 <b>Disponibilidad actual:</b>\n"]
                for p in availability:
                    m = p.get("monthly_available", 0)
                    e = p.get("express_available", 0)
                    status_icon = "✅" if (m + e) > 0 else "❌"
                    avail_lines.append(f"{status_icon} {p.get('icon_emoji','📺')} <b>{p.get('name','')}</b>: {m} mensual | {e} express")
                response = "\n".join(avail_lines)

            await update.message.reply_text(
                response, parse_mode="HTML",
                reply_markup=platforms_keyboard(availability, plan_type_hint or "monthly"),
            )
            bot_reply = f"Disponibilidad mostrada{' de ' + platform if platform else ''}."

        elif intent in ("renewal", "cancel") and confidence != "baja":
            await update.message.reply_text(
                "📋 Aquí puedes ver y renovar tus servicios activos:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 Ver mis servicios", callback_data="menu:my_services")
                ]]),
            )
            bot_reply = "Menú servicios mostrado."

        else:
            system_prompt = _build_system_prompt(user_name, active_subs, prices_text)
            response = await _chat_response(system_prompt, conversation, text)
            try:
                await update.message.reply_text(response, parse_mode="HTML", reply_markup=main_menu_keyboard())
            except Exception:
                await update.message.reply_text(response, reply_markup=main_menu_keyboard())
            bot_reply = response

    except Exception as e:
        logger.error(f"handle_free_text routing error [{telegram_id}] intent={intent}: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "Disculpa, tuve un problema procesando tu mensaje. ¿En qué te puedo ayudar?",
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            pass

    if bot_reply:
        store_conversation_message(telegram_id, "assistant", bot_reply)
