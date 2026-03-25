"""
Admin natural-language handler.
Handles free-text admin commands with keyword + LLM intent detection.
Destructive actions (cancel_sub, block) require inline confirmation.
"""
from __future__ import annotations

import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import settings
from utils.validators import is_admin

logger = logging.getLogger(__name__)


def _is_admin(tid: int) -> bool:
    return is_admin(tid, settings.ADMIN_TELEGRAM_IDS)


# ─────────────────────────────────────────────────────────────────
# KEYWORD INTENT DETECTION
# ─────────────────────────────────────────────────────────────────

def _detect_intent(text: str) -> dict | None:
    t = text.lower().strip()

    # ── income ────────────────────────────────────────────────────
    income_kws = ["ingreso", "venta", "ganado", "recaudado", "facturado", "cuánto hemos", "cuanto hemos"]
    if any(k in t for k in income_kws):
        period = "today" if any(k in t for k in ["hoy", "today"]) else \
                 "week"  if any(k in t for k in ["semana", "semanal", "week"]) else "month"
        return {"action": "income", "period": period}

    # ── pending payments ──────────────────────────────────────────
    pending_kws = ["pendiente", "pago pendiente", "aprobacion", "aprobación", "comprobante", "por aprobar"]
    if any(k in t for k in pending_kws):
        return {"action": "pending"}

    # ── stock / availability ──────────────────────────────────────
    stock_kws = ["stock", "disponib", "pantalla", "cuántas pantalla", "cuantas pantalla", "queda"]
    if any(k in t for k in stock_kws):
        return {"action": "stock"}

    # ── dashboard / stats ─────────────────────────────────────────
    dash_kws = ["dashboard", "estadística", "estadistica", "resumen", "panel", "stats", "reporte general"]
    if any(k in t for k in dash_kws):
        return {"action": "dashboard"}

    # ── exchange rate ─────────────────────────────────────────────
    rate_kws = ["tasa", "cambio", "binance", "dólar", "dolar"]
    if any(k in t for k in rate_kws):
        # Try to extract a number from the text
        nums = re.findall(r"\d+(?:[.,]\d+)?", t)
        if nums:
            try:
                rate_val = float(nums[0].replace(",", "."))
                if rate_val > 1:  # plausible exchange rate
                    return {"action": "rate_update", "rate": rate_val}
            except ValueError:
                pass
        return {"action": "show_rate"}

    # ── block user ────────────────────────────────────────────────
    block_kws = ["bloquea", "bloquear", "suspende", "suspender", "banea", "banear"]
    if any(k in t for k in block_kws):
        name = _extract_name(t, block_kws)
        return {"action": "block_user", "name": name}

    # ── unblock user ──────────────────────────────────────────────
    unblock_kws = ["desbloquea", "desbloquear", "reactiva", "reactivar", "rehabilita"]
    if any(k in t for k in unblock_kws):
        name = _extract_name(t, unblock_kws)
        return {"action": "unblock_user", "name": name}

    # ── cancel / delete subscription ──────────────────────────────
    cancel_kws = ["elimina", "eliminar", "cancela", "cancelar", "borra", "borrar", "quita", "quitar"]
    sub_kws = ["suscripci", "subscripci", "servicio", "sub"]
    if any(k in t for k in cancel_kws) and any(k in t for k in sub_kws):
        name = _extract_name_after_de(t)
        return {"action": "cancel_sub", "name": name}

    # ── expired subscriptions ─────────────────────────────────────
    expired_kws = ["vencida", "vencido", "expirada", "expirado", "cuenta vencida",
                   "suscripción vencida", "subscripcion vencida", "clientes vencidos",
                   "quiénes vencieron", "quienes vencieron", "cuáles vencieron"]
    if any(k in t for k in expired_kws):
        return {"action": "expired_clients"}

    # ── list all clients ──────────────────────────────────────────
    list_kws = ["todos los clientes", "lista de clientes", "listar clientes",
                "ver clientes", "mostrar clientes"]
    if any(k in t for k in list_kws) or t.strip() in ("clientes", "usuarios", "lista clientes"):
        return {"action": "list_clients"}

    # ── find client ───────────────────────────────────────────────
    find_kws = ["busca", "buscar", "info de", "información de", "informacion de",
                "datos de", "perfil de", "cliente", "quién es", "quien es"]
    if any(k in t for k in find_kws):
        name = _extract_name(t, find_kws)
        return {"action": "find_client", "name": name}

    return None


def _extract_name(text: str, action_kws: list[str]) -> str:
    """Extract name after 'a X', 'al X', or after the action keyword."""
    # Try: "bloquea a Pedro García" → "Pedro García"
    m = re.search(r"\b(?:al?|a)\s+(.+)", text)
    if m:
        return m.group(1).strip()
    # Fallback: remove action keyword and return the rest
    for kw in sorted(action_kws, key=len, reverse=True):
        if kw in text:
            return text.replace(kw, "").strip(" a ")
    return text.strip()


def _extract_name_after_de(text: str) -> str:
    """Extract name after 'de X'."""
    m = re.search(r"\bde\s+(.+)", text)
    if m:
        return m.group(1).strip()
    return ""


# ─────────────────────────────────────────────────────────────────
# LLM FALLBACK
# ─────────────────────────────────────────────────────────────────

async def _llm_intent(text: str) -> dict:
    try:
        from services.gemini_service import _call
        import json
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    "Eres un clasificador de intenciones para el ADMINISTRADOR de SmartFlixVe.\n"
                    "El admin está enviando un comando en lenguaje natural. Clasifica la intención.\n\n"
                    "Acciones posibles:\n"
                    "- pending: ver pagos pendientes por aprobar\n"
                    "- income: ver reporte de ingresos (parámetro period: today/week/month)\n"
                    "- stock: ver disponibilidad de pantallas/perfiles\n"
                    "- dashboard: ver resumen general/estadísticas\n"
                    "- show_rate: ver tasa de cambio actual\n"
                    "- rate_update: actualizar tasa (parámetro rate: número)\n"
                    "- list_clients: ver/listar todos los clientes registrados\n"
                    "- find_client: buscar cliente por nombre (parámetro name)\n"
                    "- block_user: bloquear cliente (parámetro name)\n"
                    "- unblock_user: desbloquear cliente (parámetro name)\n"
                    "- cancel_sub: cancelar suscripción de un cliente (parámetro name)\n"
                    "- expired_clients: ver clientes con suscripción vencida\n"
                    "- other: conversación general o comando no reconocido\n\n"
                    "Responde ÚNICAMENTE con JSON válido:\n"
                    '{"action":"...","name":"nombre o null","period":"today/week/month o null","rate":0}\n\n'
                    f"Comando del admin: {text}"
                ),
            }],
            temperature=0.1,
            max_tokens=100,
        )
        data = json.loads(result.replace("```json", "").replace("```", "").strip())
        if data.get("name") in ("null", "none", "", None):
            data["name"] = None
        return data
    except Exception as e:
        logger.error(f"Admin LLM intent error: {e}")
        return {"action": "other"}


# ─────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────

async def _search_client(name_query: str) -> list[dict]:
    """Search users by name or username (case-insensitive)."""
    try:
        from database import get_supabase
        sb = get_supabase()
        q = name_query.strip().lstrip("@")
        # Search by name
        res_name = sb.table("users").select(
            "id, telegram_id, name, username, status, total_purchases"
        ).ilike("name", f"%{q}%").limit(5).execute()
        # Search by username
        res_user = sb.table("users").select(
            "id, telegram_id, name, username, status, total_purchases"
        ).ilike("username", f"%{q}%").limit(5).execute()

        found = {r["id"]: r for r in (res_name.data or [])}
        for r in (res_user.data or []):
            found[r["id"]] = r
        return list(found.values())
    except Exception as e:
        logger.error(f"_search_client error: {e}")
        return []


async def _get_user_subs(user_id: str) -> list[dict]:
    try:
        from database.subscriptions import get_user_active_subscriptions
        return await get_user_active_subscriptions(user_id)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────
# ACTION HANDLERS
# ─────────────────────────────────────────────────────────────────

async def _handle_pending(message) -> None:
    from database.subscriptions import get_pending_subscriptions
    from database.platforms import get_platform_by_id
    from database.users import get_user_by_telegram_id
    from bot.keyboards import pending_payment_keyboard
    from utils.helpers import short_id

    subs = await get_pending_subscriptions()
    if not subs:
        await message.reply_text("✅ No hay pagos pendientes por aprobar.")
        return

    await message.reply_text(f"⏳ <b>{len(subs)} pago(s) pendiente(s):</b>", parse_mode="HTML")
    for sub in subs[:10]:
        sub_id = str(sub.get("id", ""))
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        name = user.get("name") or user.get("username") or str(user.get("telegram_id", "?"))
        plat_name = f"{platform.get('icon_emoji','')} {platform.get('name','?')}"
        plan = sub.get("plan_type", "?")
        price = sub.get("price_usd", 0)
        txt = (
            f"👤 <b>{name}</b>\n"
            f"📺 {plat_name} — {plan}\n"
            f"💵 ${price:.2f} | #{short_id(sub_id)}"
        )
        await message.reply_text(txt, parse_mode="HTML", reply_markup=pending_payment_keyboard(sub_id))


async def _handle_income(message, period: str) -> None:
    from database.analytics import get_income_report
    report = await get_income_report(period)
    period_label = {"today": "Hoy", "week": "Esta semana", "month": "Este mes"}.get(period, period)
    by_plat = "\n".join([f"  {k}: ${v:.2f}" for k, v in (report.get("by_platform") or {}).items()])
    text = (
        f"💰 <b>Ingresos — {period_label}</b>\n\n"
        f"💵 Total USD: <b>${report.get('total_usd', 0):.2f}</b>\n"
        f"🔢 Transacciones: <b>{report.get('transaction_count', 0)}</b>\n\n"
        f"📊 Por plataforma:\n{by_plat or '  Sin datos'}"
    )
    await message.reply_text(text, parse_mode="HTML")


async def _handle_stock(message) -> None:
    from database.analytics import get_platform_availability
    avail = await get_platform_availability()
    if not avail:
        await message.reply_text("No hay datos de stock.")
        return
    lines = ["📦 <b>Stock disponible:</b>\n"]
    for p in avail:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        m = p.get("monthly_available", 0)
        e = p.get("express_available", 0)
        status = "✅" if (m + e) > 0 else "❌"
        lines.append(f"{status} {icon} <b>{name}</b>: {m} mensual | {e} express")
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def _handle_dashboard(message) -> None:
    from database.analytics import get_dashboard_stats
    stats = await get_dashboard_stats()
    avail = stats.get("platform_availability", [])
    avail_text = "\n".join([
        f"  {p.get('icon_emoji','')} {p.get('name','')}: {p.get('monthly_available',0)}M | {p.get('express_available',0)}E"
        for p in avail
    ])
    text = (
        f"📊 <b>Dashboard SmartFlixVe</b>\n\n"
        f"👥 Usuarios totales: <b>{stats.get('total_users', 0)}</b>\n"
        f"🆕 Nuevos hoy: <b>{stats.get('new_users_today', 0)}</b>\n"
        f"✅ Suscripciones activas: <b>{stats.get('active_subscriptions', 0)}</b>\n"
        f"⏳ Pagos pendientes: <b>{stats.get('pending_payments', 0)}</b>\n\n"
        f"📦 Stock:\n{avail_text or '  Sin datos'}"
    )
    await message.reply_text(text, parse_mode="HTML")


async def _handle_show_rate(message) -> None:
    from services.exchange_service import get_current_rate
    rate = await get_current_rate()
    val = (rate or {}).get("usd_binance", "N/A")
    await message.reply_text(f"💱 <b>Tasa actual:</b> Bs {val} / USD", parse_mode="HTML")


async def _handle_rate_update(message, rate_val: float, admin_id: int) -> None:
    from services.exchange_service import update_rate
    from database.users import log_admin_action
    success = await update_rate(rate_val, admin_id)
    if success:
        await log_admin_action(admin_id, "update_rate_nl", {"rate": rate_val})
        await message.reply_text(f"✅ Tasa actualizada: <b>Bs {rate_val:.2f} / USD</b>", parse_mode="HTML")
    else:
        await message.reply_text("❌ Error al actualizar la tasa.")


async def _handle_list_clients(message) -> None:
    from database.analytics import get_clients_list
    data = await get_clients_list(page=1, per_page=20)
    clients = data.get("clients", [])
    total = data.get("total", 0)
    if not clients:
        await message.reply_text("No hay clientes registrados.")
        return
    lines = [f"👥 <b>{total} clientes registrados</b> (mostrando {len(clients)}):\n"]
    buttons = []
    for c in clients:
        tid = c.get("telegram_id")
        name = c.get("name") or "Sin nombre"
        username = f"@{c['username']}" if c.get("username") else ""
        status_icon = "✅" if c.get("status") == "active" else "🚫"
        purchases = c.get("total_purchases") or 0
        lines.append(f"{status_icon} <b>{name}</b> {username} — {purchases} compra(s)")
        if tid:
            buttons.append([InlineKeyboardButton(
                f"{name}", callback_data=f"admin:client_detail:{tid}"
            )])
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=markup)


async def _handle_find_client(message, name_query: str) -> None:
    if not name_query:
        await message.reply_text("¿Cuál es el nombre o username del cliente que buscas?")
        return
    clients = await _search_client(name_query)
    if not clients:
        await message.reply_text(f"🔍 No encontré clientes con el nombre <b>{name_query}</b>.", parse_mode="HTML")
        return
    lines = [f"🔍 <b>Resultados para \"{name_query}\":</b>\n"]
    buttons = []
    for c in clients:
        tid = c.get("telegram_id")
        name = c.get("name") or "Sin nombre"
        username = f"@{c['username']}" if c.get("username") else ""
        status = "✅" if c.get("status") == "active" else "🚫"
        purchases = c.get("total_purchases", 0)
        tid_display = f"<code>{tid}</code>" if tid else "<i>sin ID Telegram</i>"
        lines.append(f"{status} <b>{name}</b> {username}\n   ID: {tid_display} | Compras: {purchases}")
        if tid:
            buttons.append([InlineKeyboardButton(
                f"Ver {name}",
                callback_data=f"admin:client_detail:{tid}"
            )])
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await message.reply_text("\n\n".join(lines), parse_mode="HTML", reply_markup=markup)


async def _handle_block_unblock(message, name_query: str, action: str, admin_id: int) -> None:
    if not name_query:
        verb = "bloquear" if action == "block_user" else "desbloquear"
        await message.reply_text(f"¿A quién quieres {verb}?")
        return
    clients = await _search_client(name_query)
    if not clients:
        await message.reply_text(f"No encontré cliente con nombre <b>{name_query}</b>.", parse_mode="HTML")
        return
    verb = "Bloquear" if action == "block_user" else "Desbloquear"
    cb_action = "block" if action == "block_user" else "unblock"
    lines = [f"⚠️ ¿Confirmas <b>{verb}</b> a este cliente?\n"]
    buttons = []
    for c in clients[:3]:
        tid = c.get("telegram_id")
        name = c.get("name") or f"ID {tid}"
        username = f"@{c['username']}" if c.get("username") else ""
        lines.append(f"👤 <b>{name}</b> {username} (<code>{tid}</code>)")
        buttons.append([
            InlineKeyboardButton(f"✅ {verb} a {name}", callback_data=f"admin:{cb_action}:{tid}"),
        ])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="admin:back")])
    await message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _handle_expired_clients(message) -> None:
    from database.subscriptions import get_expired_subscriptions
    subs = await get_expired_subscriptions(limit=30)
    if not subs:
        await message.reply_text("✅ No hay suscripciones vencidas registradas.")
        return

    # Group by user_id (UUID) — use telegram_id when available for buttons
    seen: dict[str, dict] = {}
    for s in subs:
        user = s.get("users") or {}
        # Prefer telegram_id; fall back to user_id UUID from the subscription row
        tid = user.get("telegram_id")
        uid = str(s.get("user_id") or "")
        key = str(tid) if tid else uid
        if not key:
            continue
        platform = s.get("platforms") or {}
        entry = seen.setdefault(key, {
            "name": user.get("name") or user.get("username") or f"uid {uid[:8]}",
            "telegram_id": tid,
            "subs": [],
        })
        _ed = (s.get("end_date") or "")[:10]
        end = f"{_ed[8:10]}/{_ed[5:7]}/{_ed[0:4]}" if len(_ed) == 10 else _ed
        icon = platform.get("icon_emoji", "📺")
        plat_name = platform.get("name", "?")
        plan = s.get("plan_type", "?")
        status = s.get("status", "")
        entry["subs"].append(f"{icon} {plat_name} ({plan}) — venció {end} [{status}]")

    lines = [f"📋 <b>{len(seen)} cliente(s) con suscripción vencida:</b>\n"]
    buttons = []
    for key, data in list(seen.items())[:15]:
        name = data["name"]
        tid = data["telegram_id"]
        sub_lines = "\n   ".join(data["subs"][:3])
        lines.append(f"👤 <b>{name}</b>" + (f" (<code>{tid}</code>)" if tid else "") + f"\n   {sub_lines}")
        if tid:
            buttons.append([InlineKeyboardButton(
                f"Ver {name}", callback_data=f"admin:client_detail:{tid}"
            )])

    # Split into chunks if too long
    full_text = "\n\n".join(lines)
    if len(full_text) > 3800:
        full_text = "\n\n".join(lines[:10]) + f"\n\n… y {len(seen)-9} más."

    await message.reply_text(full_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def _handle_cancel_sub(message, name_query: str, admin_id: int) -> None:
    if not name_query:
        await message.reply_text("¿De qué cliente quieres cancelar la suscripción?")
        return
    clients = await _search_client(name_query)
    if not clients:
        await message.reply_text(f"No encontré cliente con nombre <b>{name_query}</b>.", parse_mode="HTML")
        return

    # Find active subs for each matching client
    all_subs = []
    for c in clients[:3]:
        subs = await _get_user_subs(str(c["id"]))
        active = [s for s in subs if s.get("status") in ("active", "pending_payment")]
        for s in active:
            s["_client_name"] = c.get("name") or f"ID {c.get('telegram_id')}"
            all_subs.append(s)

    if not all_subs:
        await message.reply_text(
            f"El cliente <b>{name_query}</b> no tiene suscripciones activas.",
            parse_mode="HTML",
        )
        return

    lines = [f"⚠️ ¿Cuál suscripción quieres <b>cancelar</b>?\n"]
    buttons = []
    for s in all_subs[:5]:
        sub_id = str(s.get("id", ""))
        plat = (s.get("platforms") or {}).get("name", "?")
        icon = (s.get("platforms") or {}).get("icon_emoji", "📺")
        plan = s.get("plan_type", "?")
        status = s.get("status", "?")
        client_name = s.get("_client_name", "?")
        lines.append(f"{icon} <b>{plat}</b> ({plan}) — {status}\n   Cliente: {client_name}")
        buttons.append([InlineKeyboardButton(
            f"❌ Cancelar {plat} ({plan}) de {client_name}",
            callback_data=f"admin_nl:cancel_sub:{sub_id}",
        )])
    buttons.append([InlineKeyboardButton("🔙 No hacer nada", callback_data="admin:back")])
    await message.reply_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────────────────────────
# CONFIRM CANCEL SUB CALLBACK
# ─────────────────────────────────────────────────────────────────

async def handle_admin_nl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_nl:* callbacks (e.g. cancel_sub confirmations)."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    if not _is_admin(update.effective_user.id):
        await query.answer("Sin permisos", show_alert=True)
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    action = parts[1]

    if action == "cancel_sub":
        sub_id = parts[2]
        try:
            from database.subscriptions import cancel_subscription
            from database.users import log_admin_action
            ok = await cancel_subscription(sub_id)
            if ok:
                await log_admin_action(update.effective_user.id, "nl_cancel_sub", {"sub_id": sub_id})
                await query.edit_message_text(f"✅ Suscripción <code>{sub_id[:8]}</code> cancelada.", parse_mode="HTML")
            else:
                await query.edit_message_text("❌ No se pudo cancelar la suscripción.")
        except Exception as e:
            logger.error(f"cancel_sub callback error: {e}")
            await query.edit_message_text("❌ Error al cancelar.")


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

async def handle_admin_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main admin natural-language handler."""
    if not update.message or not update.effective_user:
        return

    admin_id = update.effective_user.id
    text = update.message.text.strip()

    # 1. Keyword detection
    intent = _detect_intent(text)

    # 2. LLM fallback for ambiguous messages
    if intent is None:
        intent = await _llm_intent(text)

    action = intent.get("action", "other")
    name = intent.get("name") or ""
    period = intent.get("period") or "month"
    rate_val = intent.get("rate") or 0

    logger.info(f"Admin NL intent [{admin_id}]: {action} | name={name} | period={period}")

    try:
        if action == "list_clients":
            await _handle_list_clients(update.message)

        elif action == "pending":
            await _handle_pending(update.message)

        elif action == "income":
            await _handle_income(update.message, period)

        elif action == "stock":
            await _handle_stock(update.message)

        elif action == "dashboard":
            await _handle_dashboard(update.message)

        elif action == "show_rate":
            await _handle_show_rate(update.message)

        elif action == "rate_update" and rate_val > 0:
            await _handle_rate_update(update.message, float(rate_val), admin_id)

        elif action == "expired_clients":
            await _handle_expired_clients(update.message)

        elif action == "find_client":
            await _handle_find_client(update.message, name)

        elif action in ("block_user", "unblock_user"):
            await _handle_block_unblock(update.message, name, action, admin_id)

        elif action == "cancel_sub":
            await _handle_cancel_sub(update.message, name, admin_id)

        else:
            # Conversational fallback for admin
            from services.gemini_service import _call
            response = await _call(
                messages=[
                    {"role": "system", "content": (
                        "Eres el asistente del administrador de SmartFlixVe. "
                        "Responde de forma concisa en español. "
                        "Comandos disponibles: ver pendientes, ingresos, stock, tasa, "
                        "buscar cliente, bloquear/desbloquear cliente, cancelar suscripción."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.4,
                max_tokens=200,
            )
            await update.message.reply_text(response, parse_mode="HTML")

    except Exception as e:
        logger.error(f"handle_admin_free_text error: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Error procesando el comando. Intenta de nuevo o usa los comandos slash (/admin, /pendientes, etc.)"
        )
