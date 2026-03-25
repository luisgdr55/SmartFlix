"""
AI-powered free-text message handler.
Primary: keyword-based intent detection (always works, no API needed).
Secondary: LLM intent classification for ambiguous messages.
Conversational: LLM generates response for "other" intent.
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
# KEYWORD-BASED INTENT DETECTION — fast, reliable, no API needed
# ─────────────────────────────────────────────────────────────────

_PLATFORM_KEYWORDS: dict[str, str] = {
    "netflix": "netflix", "nf": "netflix",
    "disney": "disney", "disney+": "disney", "disneyplus": "disney",
    "max": "max", "hbo max": "max", "hbomax": "max", "hbo": "max",
    "paramount": "paramount", "paramount+": "paramount",
    "prime": "prime", "amazon prime": "prime", "amazon": "prime",
    "apple": "apple", "apple tv": "apple", "appletv": "apple",
    "crunchyroll": "crunchyroll", "crunchy": "crunchyroll",
}


def _find_platforms(text: str) -> list[str]:
    t = text.lower()
    found: list[str] = []
    # Sort by length descending so "hbo max" matches before "hbo"
    for kw in sorted(_PLATFORM_KEYWORDS, key=len, reverse=True):
        if kw in t and _PLATFORM_KEYWORDS[kw] not in found:
            found.append(_PLATFORM_KEYWORDS[kw])
    return found


def _detect_intent_keywords(text: str) -> dict | None:
    """
    Fast keyword detection. Returns intent dict or None if no clear match.
    Called BEFORE the LLM to handle common cases reliably.
    """
    t = text.lower()
    platforms = _find_platforms(t)

    is_express = any(k in t for k in ["express", "24h", "24 h", "24 hora"])
    plan = "express" if is_express else "monthly"

    # ── multi_order: 2+ platforms ─────────────────────────────────
    if len(platforms) >= 2:
        return {
            "intent": "multi_order",
            "platform": None,
            "platforms": platforms,
            "plan_type": plan,
            "confidence": "alta",
        }

    # ── credentials ───────────────────────────────────────────────
    cred_kws = [
        "mis datos", "mi datos", "credencial", "usuario y contraseña",
        "mi contraseña", "mis credencial", "datos de acceso",
        "datos de mi cuenta", "mi clave", "mi usuario", "mis claves",
        "mis accesos", "ver mis datos", "dime mis datos",
    ]
    if any(k in t for k in cred_kws):
        return {"intent": "credentials", "platform": None, "platforms": None, "plan_type": None, "confidence": "alta"}

    # ── availability ──────────────────────────────────────────────
    avail_kws = [
        "hay ", "disponible", "disponibilidad", "tienen pantalla",
        "cuantas pantalla", "cuántas pantalla", "pantalla disponible",
        "hay express", "hay stock", "tienen stock", "tienen express",
        "cuantos", "cuántos",
    ]
    if any(k in t for k in avail_kws):
        return {
            "intent": "availability",
            "platform": platforms[0] if platforms else None,
            "platforms": None,
            "plan_type": "express" if is_express else None,
            "confidence": "alta",
        }

    # ── info / prices ─────────────────────────────────────────────
    info_kws = [
        "precio", "precios", "cuánto cuesta", "cuanto cuesta",
        "cuánto vale", "cuanto vale", "tarifa", "planes", "costo",
        "cuanto es", "cuánto es", "cuanto cobran", "cuánto cobran",
        "cuanto me sale", "información", "informacion",
    ]
    if any(k in t for k in info_kws):
        return {
            "intent": "info",
            "platform": platforms[0] if platforms else None,
            "platforms": None,
            "plan_type": None,
            "confidence": "alta",
        }

    # ── support ───────────────────────────────────────────────────
    support_kws = [
        "no funciona", "problema", "no puedo", "no me deja",
        "no carga", "no entra", "no conecta", "falla", "error",
        "soporte", "ayuda", "necesito ayuda",
    ]
    if any(k in t for k in support_kws):
        return {"intent": "support", "platform": None, "platforms": None, "plan_type": None, "confidence": "alta"}

    # ── cart view ─────────────────────────────────────────────────
    cart_kws = ["ver carrito", "mi carrito", "ver mi carrito", "que tengo en el carrito", "carrito"]
    if any(k in t for k in cart_kws):
        return {"intent": "cart_view", "platform": None, "platforms": None, "plan_type": None, "confidence": "alta"}

    # ── renewal ───────────────────────────────────────────────────
    renewal_kws = [
        "renovar", "renovación", "renovacion", "renueva", "vencida",
        "vencido", "vencer", "venció", "vencio", "caducó", "caducado",
        "se me acabó", "se me acabo", "mis servicios",
    ]
    if any(k in t for k in renewal_kws):
        return {"intent": "renewal", "platform": None, "platforms": None, "plan_type": None, "confidence": "alta"}

    # ── express (single or no platform) ──────────────────────────
    if is_express:
        return {
            "intent": "express",
            "platform": platforms[0] if platforms else None,
            "platforms": None,
            "plan_type": "express",
            "confidence": "alta",
        }

    # ── subscribe / single platform order ────────────────────────
    order_kws = [
        "quiero", "necesito", "dame", "contratar", "suscrib",
        "tomar", "pedir", "ordenar", "comprar", "me interesa",
    ]
    if platforms and any(k in t for k in order_kws):
        return {
            "intent": "subscribe",
            "platform": platforms[0],
            "platforms": None,
            "plan_type": "monthly",
            "confidence": "alta",
        }

    return None  # No keyword match → fall through to LLM


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
            "name, icon_emoji, monthly_price_usd, express_price_usd, slug"
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
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────
def _build_system_prompt(user_name: str, active_subs: list[dict], prices_text: str = "") -> str:
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
- Planes: Mensual (~30 días), Express (24 horas).
- Precios en bolívares (Bs) según tasa Binance del día. Pago por Pago Móvil o transferencia.
- Una vez aprobado el pago, el cliente recibe sus credenciales por este chat.

Cliente: {user_name or "Estimado cliente"}
{subs_text}
{prices_section}

Instrucciones:
- Responde en español venezolano, tono amigable y cercano.
- Sé conciso: máximo 5 oraciones.
- Si preguntan precios, cita los precios reales de arriba.
- Usa HTML básico (<b>texto</b>) para negritas cuando sea útil.
- Si no puedes ayudar, indica que puede usar el menú de abajo."""


async def _get_user_context(telegram_id: int) -> tuple[str, list[dict]]:
    """Return (user_name, active_subscriptions)."""
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
    """Generate a conversational response via LLM."""
    messages = [{"role": "system", "content": system_prompt}]
    for m in conversation[-6:]:
        messages.append(m)
    messages.append({"role": "user", "content": user_message})
    try:
        return await _call(messages, temperature=0.5, max_tokens=300)
    except Exception as e:
        logger.error(f"chat_response error: {e}")
        return "Lo siento, tuve un problema. ¿En qué te puedo ayudar? Usa el menú de abajo 👇"


async def _send_platform_menu(message, plan_type: str, intro: str) -> None:
    """Send platform selection keyboard."""
    from database.analytics import get_platform_availability
    try:
        availability = await get_platform_availability()
        await message.reply_text(intro, parse_mode="HTML", reply_markup=platforms_keyboard(availability, plan_type))
    except Exception as e:
        logger.error(f"Error sending platform menu: {e}")
        await message.reply_text("Usa el menú para elegir tu plataforma 👇", reply_markup=main_menu_keyboard())


async def _lookup_platforms_from_items(items_raw: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Match platform slugs/names from items_raw against DB platforms.
    Uses get_platform_by_slug (simple .eq() query, same as rest of codebase).
    Returns (cart_item_list, not_found_list).
    """
    from database.platforms import get_platform_by_slug
    from services.exchange_service import get_current_rate

    rate_data = await get_current_rate()
    rate = float((rate_data or {}).get("usd_binance") or 36.0)

    # Slug aliases for common LLM/keyword variations
    _slug_map = {
        "hbomax": "max", "hbo max": "max", "hbo": "max",
        "disney+": "disney", "disney plus": "disney", "disneyplus": "disney",
        "amazon prime": "prime", "amazon": "prime",
        "apple tv": "apple", "appletv": "apple", "apple tv+": "apple",
        "paramount+": "paramount",
    }

    cart_items: list[dict] = []
    not_found: list[str] = []

    for raw_item in items_raw:
        slug_q = raw_item.get("platform", "").lower().strip()
        item_plan = raw_item.get("plan_type", "monthly")

        # Normalize slug using alias map
        slug_normalized = _slug_map.get(slug_q, slug_q)

        plat = await get_platform_by_slug(slug_normalized)
        if not plat:
            not_found.append(slug_q)
            continue

        price_field = {
            "monthly": "monthly_price_usd",
            "express": "express_price_usd",
        }.get(item_plan, "monthly_price_usd")
        price_usd = float(plat.get(price_field) or 0)

        cart_items.append({
            "platform_id": str(plat["id"]),
            "name": plat.get("name", ""),
            "emoji": plat.get("icon_emoji") or "📺",
            "plan_type": item_plan,
            "price_usd": price_usd,
            "price_bs": round(price_usd * rate, 2),
            "rate_used": rate,
        })

    return cart_items, not_found


async def _handle_multi_order(update, telegram_id: int, text: str, intent_data: dict, plan_type_hint) -> str | None:
    """
    Handle multi-platform cart order.
    Returns bot_reply string on success, None on failure.
    Never raises — catches all exceptions including BaseException.
    """
    try:
        from services.gemini_service import extract_order_items
        from services.cart_service import save_cart
        from bot.keyboards import cart_keyboard as _cart_kb

        # Step 1: LLM extraction (for per-platform plan types)
        items_raw: list[dict] = []
        # Only call LLM if we need to (keyword detection already gave platforms)
        kw_platforms = intent_data.get("platforms") or []
        if not kw_platforms:
            # No keyword platforms — need LLM to parse
            try:
                items_raw = await extract_order_items(text)
            except Exception as e:
                logger.warning(f"extract_order_items error: {e}")

        # Step 2: use LLM result or keyword fallback
        if not items_raw:
            items_raw = [
                {"platform": p, "plan_type": plan_type_hint or "monthly"}
                for p in kw_platforms if p
            ]

        if not items_raw:
            await _send_platform_menu(update.message, "monthly", "¡Claro! ¿Qué plataformas quieres contratar?")
            return "Menú mostrado."

        logger.info(f"multi_order items: {items_raw}")

        # Step 3: lookup in DB
        cart_items, not_found = await _lookup_platforms_from_items(items_raw)
        logger.info(f"multi_order cart_items={len(cart_items)} not_found={not_found}")

        if not cart_items:
            await update.message.reply_text(
                "No encontré esas plataformas. Dime cuáles quieres y te ayudo 😊",
                reply_markup=main_menu_keyboard(),
            )
            return "Plataformas no encontradas."

        # Step 4: save and display cart
        save_cart(telegram_id, cart_items)
        total_usd = sum(i["price_usd"] for i in cart_items)
        total_bs = sum(i["price_bs"] for i in cart_items)

        lines = ["🛒 <b>Tu carrito:</b>\n"]
        for item in cart_items:
            plan_label = {"monthly": "Mensual", "express": "Express 24h"}.get(item["plan_type"], item["plan_type"])
            lines.append(f"<b>{item['name']}</b> — {plan_label}: ${item['price_usd']:.2f} / Bs {item['price_bs']:,.0f}")
        lines.append(f"\n<b>Total: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")
        if not_found:
            lines.append(f"\nNo encontre: {', '.join(not_found)}")

        kb = _cart_kb()
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        except Exception:
            await update.message.reply_text("\n".join(lines), reply_markup=kb)

        return f"Carrito con {len(cart_items)} servicios."

    except BaseException as e:
        logger.error(f"_handle_multi_order failed: {type(e).__name__}: {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────
async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle any free-text message.
    1. Try keyword-based intent detection (fast, always works).
    2. If no keyword match, use LLM intent detection.
    3. Route to correct handler or generate conversational response.
    """
    if not update.message or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
    except Exception:
        pass

    # Load context in parallel
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

    # ── 1. Keyword detection (primary, no API) ────────────────────
    intent_data = _detect_intent_keywords(text)

    # ── 2. LLM detection (secondary, for ambiguous messages) ──────
    if intent_data is None:
        intent_data = await interpret_user_intent(text, conversation)

    intent = intent_data.get("intent", "other")
    platform = intent_data.get("platform")
    plan_type_hint = intent_data.get("plan_type")
    confidence = intent_data.get("confidence", "baja")

    logger.info(f"AI intent [{telegram_id}]: {intent} | platform={platform} | plan={plan_type_hint} | conf={confidence}")

    bot_reply = None

    try:
        # ── subscribe ─────────────────────────────────────────────
        if intent == "subscribe" and confidence != "baja":
            if platform:
                # Known platform → direct offer with confirm button
                from database.platforms import get_platform_by_slug
                from services.exchange_service import get_current_rate
                plat_db = await get_platform_by_slug(platform)
                if plat_db:
                    rate_data = await get_current_rate()
                    rate = float((rate_data or {}).get("usd_binance") or 36.0)
                    price_usd = float(plat_db.get("monthly_price_usd") or 0)
                    price_bs = round(price_usd * rate, 2)
                    icon = plat_db.get("icon_emoji", "📺")
                    name = plat_db.get("name", platform.capitalize())
                    pid = str(plat_db["id"])
                    from bot.keyboards import confirm_order_keyboard
                    await update.message.reply_text(
                        f"¡Perfecto! 🎬\n\n"
                        f"{icon} <b>{name}</b> — Plan Mensual\n"
                        f"💵 ${price_usd:.2f} / Bs {price_bs:,.0f}\n\n"
                        f"¿Confirmamos tu pedido?",
                        parse_mode="HTML",
                        reply_markup=confirm_order_keyboard(pid, "monthly"),
                    )
                    bot_reply = f"Oferta directa {name} mensual."
                else:
                    await _send_platform_menu(
                        update.message, "monthly",
                        f"¡Claro! 🎬 Elige la plataforma para tu suscripción mensual:",
                    )
                    bot_reply = "Menú mensual mostrado."
            else:
                await _send_platform_menu(
                    update.message, "monthly",
                    "¡Claro! 🎬 ¿Qué plataforma quieres contratar?",
                )
                bot_reply = "Menú mensual mostrado."

        # ── express ───────────────────────────────────────────────
        elif intent == "express" and confidence != "baja":
            if platform:
                from database.platforms import get_platform_by_slug
                from services.exchange_service import get_current_rate
                plat_db = await get_platform_by_slug(platform)
                if plat_db:
                    rate_data = await get_current_rate()
                    rate = float((rate_data or {}).get("usd_binance") or 36.0)
                    price_usd = float(plat_db.get("express_price_usd") or 0)
                    price_bs = round(price_usd * rate, 2)
                    icon = plat_db.get("icon_emoji", "📺")
                    name = plat_db.get("name", platform.capitalize())
                    pid = str(plat_db["id"])
                    from bot.keyboards import confirm_order_keyboard
                    await update.message.reply_text(
                        f"⚡ ¡Express 24h!\n\n"
                        f"{icon} <b>{name}</b> — Express 24h\n"
                        f"💵 ${price_usd:.2f} / Bs {price_bs:,.0f}\n\n"
                        f"¿Confirmamos tu pedido?",
                        parse_mode="HTML",
                        reply_markup=confirm_order_keyboard(pid, "express"),
                    )
                    bot_reply = f"Oferta directa {name} express."
                else:
                    await _send_platform_menu(update.message, "express", "⚡ ¡Express 24h! Elige la plataforma:")
                    bot_reply = "Menú express mostrado."
            else:
                await _send_platform_menu(update.message, "express", "⚡ ¡Express 24h! ¿Qué plataforma quieres?")
                bot_reply = "Menú express mostrado."

        # ── support ───────────────────────────────────────────────
        elif intent == "support" and confidence != "baja":
            from bot.keyboards import support_keyboard as sk
            await update.message.reply_text(
                "🆘 <b>Soporte SmartFlixVe</b>\n\n¿Con qué te puedo ayudar?",
                parse_mode="HTML", reply_markup=sk(),
            )
            bot_reply = "Menú soporte mostrado."

        # ── info / prices ─────────────────────────────────────────
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
                        + "\n\n¿Quieres contratar? Elige tu plan 👇"
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
                await update.message.reply_text(response, parse_mode="HTML", reply_markup=main_menu_keyboard())
            bot_reply = f"Precios mostrados{' de ' + platform if platform else ''}."

        # ── multi_order ───────────────────────────────────────────
        elif intent == "multi_order" and confidence != "baja":
            result = await _handle_multi_order(update, telegram_id, text, intent_data, plan_type_hint)
            if result is not None:
                bot_reply = result
            else:
                # Cart failed — fall back to conversational with cart keyboard
                from bot.keyboards import cart_keyboard as _ck
                system_prompt = _build_system_prompt(user_name, active_subs, prices_text)
                response = await _chat_response(system_prompt, conversation, text)
                try:
                    await update.message.reply_text(response, parse_mode="HTML", reply_markup=_ck())
                except Exception:
                    await update.message.reply_text(response, reply_markup=_ck())
                bot_reply = response

        # ── credentials ───────────────────────────────────────────
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
                    pname = plat.get("name", "?")
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
                    block = [f"\n{icon} <b>{pname}</b>"]
                    block.append(f"📧 Email: <code>{email}</code>")
                    block.append(f"🔑 Contraseña: <code>{password}</code>")
                    if profile_name:
                        block.append(f"👤 Perfil: <b>{profile_name}</b>")
                    if pin:
                        block.append(f"🔢 PIN: <code>{pin}</code>")
                    if end_raw:
                        block.append(f"📅 Vence: {end_raw[8:10]}/{end_raw[5:7]}/{end_raw[0:4]}")
                    lines.append("\n".join(block))
                lines.append("\n\n⚠️ <i>No compartas estos datos con nadie.</i>")
                await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu_keyboard())
            bot_reply = "Credenciales mostradas."

        # ── availability ──────────────────────────────────────────
        elif intent == "availability" and confidence != "baja":
            from database.analytics import get_platform_availability
            availability = await get_platform_availability()
            plan_labels = {"monthly": "mensual", "express": "express 24h"}
            plan_key_map = {"monthly": "monthly_available", "express": "express_available"}

            if platform:
                match = next(
                    (p for p in availability
                     if platform.lower() in p.get("name", "").lower()
                     or platform.lower() in p.get("slug", "").lower()),
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
                        response = f"{icon} <b>{pname}</b> — plan {label}:\n\n😔 Por el momento <b>no hay disponibilidad</b>.\nPuedes revisar otros planes abajo."
                else:
                    icon = match.get("icon_emoji", "📺")
                    pname = match.get("name", "")
                    m = match.get("monthly_available", 0)
                    e = match.get("express_available", 0)
                    def _slot(n): return f"<b>{n}</b> disponible{'s' if n != 1 else ''}" if n > 0 else "<b>sin stock</b>"
                    response = f"{icon} <b>Disponibilidad de {pname}:</b>\n\n📅 Mensual: {_slot(m)}\n⚡ Express 24h: {_slot(e)}\n\n¿Cuál plan te interesa?"
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
            bot_reply = f"Disponibilidad mostrada."

        # ── cart_view ─────────────────────────────────────────────
        elif intent == "cart_view":
            from services.cart_service import get_cart
            from bot.keyboards import cart_keyboard as _ck
            cart = get_cart(telegram_id)
            if cart:
                from services.exchange_service import get_current_rate
                rate_data = await get_current_rate()
                rate = float((rate_data or {}).get("usd_binance") or 36.0)
                lines = ["🛒 <b>Tu carrito actual:</b>\n"]
                total_usd = 0.0
                total_bs = 0.0
                for item in cart:
                    plan_label = {"monthly": "Mensual", "express": "Express 24h"}.get(item.get("plan_type", "monthly"), item.get("plan_type", ""))
                    p_usd = float(item.get("price_usd") or 0)
                    p_bs = float(item.get("price_bs") or round(p_usd * rate, 2))
                    total_usd += p_usd
                    total_bs += p_bs
                    lines.append(f"{item.get('emoji','📺')} <b>{item.get('name','?')}</b> — {plan_label}: ${p_usd:.2f} / Bs {p_bs:,.0f}")
                lines.append(f"\n<b>Total: ${total_usd:.2f} / Bs {total_bs:,.0f}</b>")
                await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=_ck())
                bot_reply = "Carrito mostrado."
            else:
                await update.message.reply_text(
                    "Tu carrito está vacío.\n\n¿Quieres agregar algún servicio?",
                    reply_markup=main_menu_keyboard(),
                )
                bot_reply = "Carrito vacío."

        # ── renewal / cancel ──────────────────────────────────────
        elif intent in ("renewal", "cancel") and confidence != "baja":
            await update.message.reply_text(
                "📋 Aquí puedes ver y renovar tus servicios activos:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 Ver mis servicios", callback_data="menu:my_services")
                ]]),
            )
            bot_reply = "Menú servicios mostrado."

        # ── conversational (other / low confidence) ───────────────
        else:
            system_prompt = _build_system_prompt(user_name, active_subs, prices_text)
            response = await _chat_response(system_prompt, conversation, text)
            try:
                await update.message.reply_text(response, parse_mode="HTML", reply_markup=main_menu_keyboard())
            except Exception:
                await update.message.reply_text(response, reply_markup=main_menu_keyboard())
            bot_reply = response

    except Exception as e:
        logger.error(f"handle_free_text error [{telegram_id}] intent={intent}: {e}", exc_info=True)
        try:
            from bot.keyboards import cart_keyboard as _ck
            kb = _ck() if intent == "multi_order" else main_menu_keyboard()
            system_prompt = _build_system_prompt(user_name, active_subs, prices_text)
            response = await _chat_response(system_prompt, conversation, text)
            try:
                await update.message.reply_text(response, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await update.message.reply_text(response, reply_markup=kb)
            bot_reply = response
        except Exception as e2:
            logger.error(f"handle_free_text fallback error: {e2}", exc_info=True)
            await update.message.reply_text(
                "Disculpa, tuve un problema. ¿En qué te puedo ayudar?",
                reply_markup=main_menu_keyboard(),
            )

    if bot_reply:
        store_conversation_message(telegram_id, "assistant", bot_reply)
