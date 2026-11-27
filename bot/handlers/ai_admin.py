"""
Admin natural-language handler.
Handles free-text admin commands with keyword + LLM intent detection.
Toda acción con efecto exige confirmación con botón: cancelar suscripción, bloquear y
desbloquear, y el cambio de tasa (orden explícita + número plausible + borrador de un
solo uso que vence). El texto libre o la IA nunca cambian la tasa por sí solos.
"""
from __future__ import annotations

import logging
import math
import re
import secrets
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import settings
from utils.validators import is_admin

logger = logging.getLogger(__name__)


def _is_admin(tid: int) -> bool:
    return is_admin(tid, settings.ADMIN_TELEGRAM_IDS)


# ─────────────────────────────────────────────────────────────────
# CAMBIO DE TASA: orden explícita, valor plausible, confirmación
# ─────────────────────────────────────────────────────────────────

_RATE_SET_VERBS = (
    "actualiza", "actualizar", "cambia", "cambiar", "pon", "poner", "fija", "fijar",
    "establece", "establecer", "ajusta", "ajustar", "setea",
)
_QUESTION_RE = re.compile(r"[?¿]|\b(cu[aá]nto|cu[aá]l|qu[eé]|c[oó]mo)\b")
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")
RATE_MAX = 100000.0          # tope absoluto (Bs por USD)
RATE_BAND = 0.5              # se rechaza lo que se aleje más de ±50 % de la tasa vigente
RATE_DRAFT_TTL_S = 300       # la confirmación vence a los 5 minutos


def parse_rate_number(raw: str) -> float | None:
    """Número de una tasa: acepta 1050, 105.5, 105,5, 1.050 y 1.050,50.
    Un separador seguido de exactamente 3 dígitos es de miles; si no, decimal."""
    s = raw.strip().strip(".,")
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            s = "".join(parts)                  # separador de miles
        else:
            s = parts[0] + "." + parts[1]       # decimal
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def extract_rate_command(text: str) -> tuple[str, float | None]:
    """Interpreta el texto del admin.

    ('set', valor)   orden explícita («actualiza la tasa a 1050») con un único número.
    ('ambiguous', None)  hay verbo de cambio pero ningún número o varios.
    ('none', None)   no es una orden (pregunta, comentario, solo «tasa 1050»...).
    """
    t = text.lower().strip()
    if _QUESTION_RE.search(t):
        return "none", None
    if not any(re.search(rf"\b{v}\b", t) for v in _RATE_SET_VERBS):
        return "none", None
    kw = re.search(r"tasa|binance|d[oó]lar|cambio", t)
    nums = [parse_rate_number(n) for n in _NUM_RE.findall(t[kw.start():] if kw else t)]
    nums = [n for n in nums if n is not None]
    if len(nums) == 1:
        return "set", nums[0]
    return "ambiguous", None


def rate_is_plausible(new: float, current: float | None) -> tuple[bool, str]:
    """Rechaza valores no finitos, <= 0, mayores al tope o muy lejanos a la tasa vigente."""
    if not isinstance(new, (int, float)) or not math.isfinite(new) or new <= 0:
        return False, "el valor no es un número válido."
    if new > RATE_MAX:
        return False, f"el valor supera el máximo permitido (Bs {RATE_MAX:,.0f})."
    if current and current > 0:
        lo, hi = current * (1 - RATE_BAND), current * (1 + RATE_BAND)
        if not (lo <= new <= hi):
            return False, (f"Bs {new:,.2f} se aleja demasiado de la tasa actual (Bs {current:,.2f}); "
                           "parece un error de escritura.")
    elif new <= 1:
        return False, "el valor es demasiado bajo para una tasa."
    return True, ""


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menú admin", callback_data="admin:back")]])


async def _current_rate_value() -> float | None:
    from services.exchange_service import get_current_rate
    row = await get_current_rate()
    try:
        v = float((row or {}).get("usd_binance") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


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
        # Solo una ORDEN explícita propone un cambio; una pregunta o un comentario con
        # un número («¿cuánto cambió la tasa en 2 días?») solo muestra la tasa.
        kind, _ = extract_rate_command(t)
        if kind != "none":
            return {"action": "rate_update"}
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

    # ── client detail (full) ──────────────────────────────────────
    detail_kws = ["detalle de", "detalles de", "info de", "información de",
                  "informacion de", "datos de", "ver cliente"]
    if any(k in t for k in detail_kws):
        name = _extract_name_after_de(t) or _extract_name(t, detail_kws)
        return {"action": "client_detail_nl", "name": name}

    # ── find client ───────────────────────────────────────────────
    find_kws = ["busca", "buscar", "perfil de", "quién es", "quien es"]
    if any(k in t for k in find_kws):
        name = _extract_name(t, find_kws)
        return {"action": "find_client", "name": name}

    # ── test imap ─────────────────────────────────────────────────
    imap_kws = ["probar imap", "test imap", "probar correo", "verificar correo",
                "probar verificacion", "probar verificación", "testverif"]
    if any(k in t for k in imap_kws):
        slug = "netflix"
        for p in ["netflix", "disney", "max", "prime", "paramount"]:
            if p in t:
                slug = p
                break
        return {"action": "test_imap", "platform": slug}

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
                    "- client_detail_nl: ver detalle completo de un cliente por nombre (parámetro name)\n"
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
    await message.reply_text(
        f"💱 <b>Tasa actual:</b> Bs {val} / USD\n\n"
        "Para cambiarla escribe, por ejemplo: <i>«actualiza la tasa a 1050»</i>. "
        "Te pediré confirmación antes de aplicarla.",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


async def _propose_rate_change(message, context, text: str, admin_id: int) -> None:
    """Valida la orden del admin y muestra una tarjeta de confirmación. NO cambia la tasa."""
    kind, value = extract_rate_command(text)   # el número sale del texto, nunca de la IA
    if kind != "set" or value is None:
        await message.reply_text(
            "No pude identificar el valor de la tasa. Escríbelo con un solo número, por ejemplo: "
            "<i>«actualiza la tasa a 1050»</i>.",
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )
        return
    current = await _current_rate_value()
    ok, reason = rate_is_plausible(value, current)
    if not ok:
        await message.reply_text(
            f"⚠️ No cambié la tasa: {reason}\n\n"
            "Revisa el número y vuelve a escribirlo. Si de verdad es ese valor, cámbialo desde el panel web "
            "(Configuración → Tasa de cambio).",
            reply_markup=_back_keyboard(),
        )
        return
    nonce = secrets.token_hex(4)
    context.user_data["rate_draft"] = {
        "nonce": nonce, "value": value, "expires": time.time() + RATE_DRAFT_TTL_S,
    }
    delta = f" ({(value / current - 1) * 100:+.1f} %)" if current else ""
    actual = f"Bs {current:,.2f}" if current else "sin dato"
    await message.reply_text(
        "💱 <b>Cambiar la tasa de cambio</b>\n\n"
        f"Actual: {actual}\n"
        f"Nueva: <b>Bs {value:,.2f}</b>{delta}\n\n"
        "Esto cambia los precios en bolívares que ven todos los clientes. ¿Confirmas?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirmar", callback_data=f"admin_nl:rate_ok:{nonce}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"admin_nl:rate_no:{nonce}"),
        ]]),
    )


async def _handle_rate_confirmation(query, context, admin_id: int, action: str, nonce: str) -> None:
    """Botón de la tarjeta: aplica o descarta el borrador (un solo uso, vence a los 5 min)."""
    draft = context.user_data.get("rate_draft")
    if not draft or draft.get("nonce") != nonce or time.time() > draft.get("expires", 0):
        if draft and time.time() > draft.get("expires", 0):
            context.user_data.pop("rate_draft", None)
        await query.edit_message_text(
            "Esta confirmación venció o ya se usó. Escríbeme de nuevo el cambio de tasa.",
            reply_markup=_back_keyboard(),
        )
        return
    context.user_data.pop("rate_draft", None)          # un solo uso
    if action == "rate_no":
        await query.edit_message_text("Cancelado. La tasa no cambió.", reply_markup=_back_keyboard())
        return

    value = float(draft["value"])
    try:
        ok, reason = rate_is_plausible(value, await _current_rate_value())   # la tasa pudo cambiar mientras tanto
        if not ok:
            await query.edit_message_text(f"⚠️ No cambié la tasa: {reason}", reply_markup=_back_keyboard())
            return
        from services.exchange_service import update_rate
        applied = await update_rate(value, admin_id)
    except Exception as e:
        logger.error(f"rate confirmation error: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ No pude completar el cambio de tasa. La tasa no se modificó; escríbeme la orden de nuevo "
            "o cámbiala desde el panel web (Configuración).",
            reply_markup=_back_keyboard(),
        )
        return

    if applied:
        try:
            from database.users import log_admin_action
            await log_admin_action(admin_id, "update_rate_nl", {"rate": value})
        except Exception as e:  # la tasa ya cambió: el registro no debe ocultarlo
            logger.error(f"update_rate_nl audit error: {e}")
        await query.edit_message_text(
            f"✅ Tasa actualizada: <b>Bs {value:,.2f} / USD</b>", parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )
    else:
        await query.edit_message_text(
            "❌ No se pudo actualizar la tasa. Intenta de nuevo o cámbiala desde el panel web (Configuración).",
            reply_markup=_back_keyboard(),
        )


async def _handle_list_clients(message) -> None:
    from database.analytics import get_clients_list
    data = await get_clients_list(page=1, per_page=20)  # already filters total_purchases > 0
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


async def _handle_client_detail_nl(message, name_query: str) -> None:
    """Search by name and show full client detail as a message."""
    if not name_query:
        await message.reply_text("¿De qué cliente quieres ver los detalles?")
        return
    clients = await _search_client(name_query)
    if not clients:
        await message.reply_text(f"🔍 No encontré clientes con el nombre <b>{name_query}</b>.", parse_mode="HTML")
        return
    if len(clients) > 1:
        # Multiple matches — show a selector
        lines = [f"🔍 Encontré {len(clients)} clientes. ¿Cuál quieres ver?\n"]
        buttons = []
        for c in clients:
            tid = c.get("telegram_id")
            name = c.get("name") or "Sin nombre"
            username = f" @{c['username']}" if c.get("username") else ""
            lines.append(f"• <b>{name}</b>{username}")
            if tid:
                buttons.append([InlineKeyboardButton(f"Ver {name}", callback_data=f"admin:client_detail:{tid}")])
        await message.reply_text("\n".join(lines), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
        return

    # Single match — show full detail using UUID (works even without telegram_id)
    c = clients[0]
    tid = c.get("telegram_id")
    uid = c.get("id")  # internal UUID, always present

    from database import get_supabase
    from bot.keyboards import client_detail_keyboard
    sb = get_supabase()

    # Fetch full user row by UUID
    user_res = sb.table("users").select("*").eq("id", uid).limit(1).execute()
    user = user_res.data[0] if user_res.data else c

    # Fetch all subscriptions for this user
    subs_res = (
        sb.table("subscriptions")
        .select("*, platforms(name, icon_emoji)")
        .eq("user_id", uid)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    subs = subs_res.data or []

    is_blocked = user.get("status") == "blocked"
    status_icon = "🚫 Bloqueado" if is_blocked else "✅ Activo"
    tid_display = f"<code>{tid}</code>" if tid else "<i>sin ID Telegram</i>"
    text = (
        f"👤 <b>Detalle del Cliente</b>\n\n"
        f"📝 Nombre: {user.get('name') or 'N/A'}\n"
        f"👤 Username: @{user.get('username') or 'N/A'}\n"
        f"🆔 Telegram ID: {tid_display}\n"
        f"📱 Teléfono: {user.get('phone') or 'N/A'}\n"
        f"🛒 Compras: {user.get('total_purchases', 0)}\n"
        f"📊 Estado: {status_icon}\n\n"
        f"📋 <b>Suscripciones ({len(subs)}):</b>\n"
    )
    for sub in subs[:8]:
        platform = (sub.get("platforms") or {}).get("name", "?")
        plan = sub.get("plan_type", "?")
        sub_status = sub.get("status", "?")
        _ed = (sub.get("end_date") or "")[:10]
        end_fmt = f"{_ed[8:10]}/{_ed[5:7]}/{_ed[0:4]}" if len(_ed) == 10 else "—"
        text += f"  • {platform} ({plan}) — {sub_status} — {end_fmt}\n"

    # Buttons only work if tid is available
    markup = client_detail_keyboard(tid, is_blocked) if tid else None
    await message.reply_text(text, parse_mode="HTML", reply_markup=markup)


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

async def _handle_test_imap(message, platform_slug: str = "netflix") -> None:
    """Test IMAP connectivity and scan inbox for a recent code."""
    import asyncio
    import time

    await message.reply_text(
        f"⏳ Probando IMAP — plataforma: <b>{platform_slug}</b>\n"
        f"Buscando emails de los últimos 15 minutos...",
        parse_mode="HTML",
    )
    try:
        from config import settings
        from services.imap_reader import _imap_search_once

        imap_email = getattr(settings, "IMAP_EMAIL", "")
        imap_password = getattr(settings, "IMAP_PASSWORD", "")
        imap_host = getattr(settings, "IMAP_HOST", "imap.gmail.com")
        imap_port = int(getattr(settings, "IMAP_PORT", 993))

        if not imap_email:
            await message.reply_text("❌ <b>IMAP_EMAIL</b> no está configurado en Railway.", parse_mode="HTML")
            return

        since_ts = time.time()
        code = await asyncio.to_thread(
            _imap_search_once,
            platform_slug,
            since_ts,
            imap_email,
            imap_password,
            imap_host,
            imap_port,
        )

        if code:
            await message.reply_text(
                f"✅ <b>IMAP funciona — código encontrado</b>\n\n"
                f"📺 Plataforma: {platform_slug}\n"
                f"🔑 Código: <code>{code}</code>\n\n"
                f"El sistema está listo para entregar códigos automáticamente.",
                parse_mode="HTML",
            )
        else:
            await message.reply_text(
                f"⚠️ <b>IMAP conectó pero no encontró código</b>\n\n"
                f"📺 Plataforma: {platform_slug}\n"
                f"📧 Inbox: <code>{imap_email}</code>\n\n"
                f"Posibles causas:\n"
                f"• No hay email de {platform_slug} en los últimos 15 min\n"
                f"• El reenvío no llegó aún al inbox central\n"
                f"• El dominio del remitente no coincide\n\n"
                f"Prueba pidiendo un código en Netflix y repite este test.",
                parse_mode="HTML",
            )
    except Exception as e:
        await message.reply_text(
            f"❌ <b>Error de conexión IMAP</b>\n\n"
            f"<code>{e}</code>\n\n"
            f"Verifica en Railway:\n"
            f"• IMAP_EMAIL\n• IMAP_PASSWORD\n• IMAP_HOST",
            parse_mode="HTML",
        )


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

    if action in ("rate_ok", "rate_no"):
        await _handle_rate_confirmation(query, context, update.effective_user.id, action, parts[2])
        return

    if action == "cancel_sub":
        sub_id = parts[2]
        # O02 Lote 6: el corte manual advierte si el cliente tiene un pago pendiente.
        if not (len(parts) > 3 and parts[3] == "force"):
            from database import get_supabase
            from database.orders import pending_for_user
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            owner = get_supabase().table("subscriptions").select("user_id").eq("id", sub_id).limit(1).execute().data
            pend = await pending_for_user(str(owner[0]["user_id"])) if owner else {"orders": [], "legacy": []}
            if pend["orders"] or pend["legacy"]:
                ids = ", ".join(f"#{i}" for i in pend["orders"] + pend["legacy"])
                await query.edit_message_text(
                    f"⚠️ <b>Este cliente tiene un pago pendiente</b> ({ids}). Revisa los pendientes antes de cortar.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⏳ Ver pendientes", callback_data="admin:pending")],
                        [InlineKeyboardButton("✂️ Cortar de todos modos", callback_data=f"admin_nl:cancel_sub:{sub_id}:force")],
                        [InlineKeyboardButton("↩️ No cortar", callback_data="admin:back")],
                    ]))
                return
        try:
            from database import get_supabase
            from database.subscriptions import cancel_subscription
            from database.users import log_admin_action
            from utils.helpers import venezuela_now

            sb = get_supabase()

            # Get subscription to check for assigned profile
            sub_res = sb.table("subscriptions").select("profile_id, user_id").eq("id", sub_id).limit(1).execute()
            sub_data = sub_res.data[0] if sub_res.data else {}
            profile_id = sub_data.get("profile_id")

            # Release profile if assigned
            if profile_id:
                sb.table("profiles").update({
                    "status": "available",
                    "last_released": venezuela_now().isoformat(),
                }).eq("id", profile_id).execute()

            ok = await cancel_subscription(sub_id)
            if ok:
                await log_admin_action(update.effective_user.id, "nl_cancel_sub", {"sub_id": sub_id})
                # Notify client
                try:
                    from services.notification_service import send_profile_released_notification
                    await send_profile_released_notification(sub_id)
                except Exception:
                    pass
                released_msg = " y perfil liberado" if profile_id else ""
                await query.edit_message_text(
                    f"✅ Suscripción <code>{sub_id[:8]}</code> cancelada{released_msg}.\n"
                    f"{'📴 Cliente notificado.' if profile_id else ''}",
                    parse_mode="HTML",
                )
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

        elif action == "rate_update":
            await _propose_rate_change(update.message, context, text, admin_id)

        elif action == "expired_clients":
            await _handle_expired_clients(update.message)

        elif action == "client_detail_nl":
            await _handle_client_detail_nl(update.message, name)

        elif action == "find_client":
            await _handle_find_client(update.message, name)

        elif action in ("block_user", "unblock_user"):
            await _handle_block_unblock(update.message, name, action, admin_id)

        elif action == "cancel_sub":
            await _handle_cancel_sub(update.message, name, admin_id)

        elif action == "test_imap":
            await _handle_test_imap(update.message, intent.get("platform", "netflix"))

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
