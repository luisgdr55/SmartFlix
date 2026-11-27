"""Aprobación y rechazo de órdenes de pago desde Telegram (O02 Lote 5).

Callbacks (registrados antes del manejador general `admin:`):
  admin:oview:<id>              ver la orden (ítems, señales del comprobante)
  admin:oapprove:<id>[:late]    aprobar (`late` = caducada, aprobada con el motivo «comprobante tardío»)
  admin:oreject:<id>            elegir el motivo del rechazo
  admin:oreason:<id>:<código>   rechazar con ese motivo
Usan el mismo motor que el panel (`services/order_admin.py`). Toda respuesta ofrece una acción.
"""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from services import checkout_flow as flow
from services import checkout_service as cs
from services import ocr_signals
from services import order_admin
from services.labels import order_label
from services.order_notifications import REJECT_REASONS
from utils.helpers import short_id

logger = logging.getLogger(__name__)

LATE_REASON = "Comprobante tardío verificado por el admin"
_EXIT = [[InlineKeyboardButton("⏳ Pendientes", callback_data="admin:pending")],
         [InlineKeyboardButton("🔙 Panel admin", callback_data="admin:back")]]


def order_keyboard(order_id: str, status: str = "pending") -> InlineKeyboardMarkup:
    if status == "expired":
        return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Aprobar (comprobante tardío)", callback_data=f"admin:oapprove:{order_id}:late")], *_EXIT])
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Aprobar", callback_data=f"admin:oapprove:{order_id}"),
                                  InlineKeyboardButton("❌ Rechazar", callback_data=f"admin:oreject:{order_id}")]])


def _is_admin(telegram_id: int) -> bool:
    from bot.handlers.admin import _check_admin
    return _check_admin(telegram_id)


async def _ticket(order: dict) -> str:
    names = await order_admin._names(order)
    user = order.get("users") or {}
    signals = ocr_signals.summary_lines(order.get("ocr_flags") or {})
    text = flow.admin_order_ticket(order, names, user.get("name") or user.get("username") or "Sin nombre",
                                   user.get("telegram_id"), signals)
    return f"{text}\n\n📌 Estado: <b>{html.escape(order_label(order.get('status')))}</b>"


async def send_pending_orders(message) -> int:
    """Responde con cada orden pendiente (con botones). Devuelve cuántas. Lo usa «Pendientes»."""
    from database import orders
    pending = await orders.list_orders(["pending"], limit=10)
    for order in pending:
        await message.reply_text(await _ticket(order), parse_mode="HTML",
                                 reply_markup=order_keyboard(str(order["id"]), "pending"))
    return len(pending)


async def handle_order_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    tid = update.effective_user.id
    if not _is_admin(tid):
        await query.answer("Sin permisos", show_alert=True)
        return
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    order_id = parts[2] if len(parts) > 2 else ""
    actor = f"admin:{tid}"
    try:
        if action == "oview":
            from database import orders
            order = await orders.get_order(order_id)
            if not order:
                await query.edit_message_text("La orden no existe.", reply_markup=InlineKeyboardMarkup(_EXIT))
                return
            await query.edit_message_text(await _ticket(order), parse_mode="HTML",
                                          reply_markup=order_keyboard(order_id, order.get("status", "pending")))
        elif action == "oapprove":
            late = len(parts) > 3 and parts[3] == "late"
            await _approve(query, order_id, actor, tid, LATE_REASON if late else None)
        elif action == "oreject":
            rows = [[InlineKeyboardButton(text.split(".")[0][:60], callback_data=f"admin:oreason:{order_id}:{code}")]
                    for code, text in REJECT_REASONS.items()]
            rows.append([InlineKeyboardButton("↩️ Volver a la orden", callback_data=f"admin:oview:{order_id}")])
            await query.edit_message_text(f"❌ <b>Rechazar la orden #{short_id(order_id)}</b>\n\nElige el motivo (se le explica al cliente):",
                                          parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        elif action == "oreason":
            code = parts[3] if len(parts) > 3 else ""
            out = await order_admin.reject_and_notify(order_id, actor, code)
            from database.users import log_admin_action
            if out["ok"]:
                await log_admin_action(tid, "reject_order", {"order_id": order_id, "reason": code})
            text = (f"❌ Orden #{short_id(order_id)} rechazada. " + ("El cliente fue avisado con la opción de iniciar un nuevo pedido."
                    if out["client_notified"] else "No se pudo avisar al cliente por Telegram: avísale por otro medio.")
                    ) if out["ok"] else "La orden ya fue decidida por otra persona. Revisa su estado."
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(_EXIT))
    except cs.ApprovalError as e:
        await query.edit_message_text(f"⚠️ {html.escape(e.user_message)}", parse_mode="HTML",
                                      reply_markup=_error_keyboard(order_id, e.code))
    except cs.CheckoutError as e:
        await query.edit_message_text(f"⚠️ {html.escape(str(e))}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(_EXIT))
    except Exception as e:
        logger.error(f"handle_order_admin_callback error: {e}", exc_info=True)
        await query.edit_message_text("Error inesperado. No se cambió nada; inténtalo de nuevo o revisa el panel.",
                                      reply_markup=InlineKeyboardMarkup(_EXIT))


def _error_keyboard(order_id: str, code: str) -> InlineKeyboardMarkup:
    rows = []
    if code == "reason_required":
        rows.append([InlineKeyboardButton("✅ Aprobar (comprobante tardío)", callback_data=f"admin:oapprove:{order_id}:late")])
    elif code in ("profile_unavailable", "unavailable", "unknown"):
        rows.append([InlineKeyboardButton("🔁 Reintentar", callback_data=f"admin:oapprove:{order_id}")])
    rows.append([InlineKeyboardButton("📄 Ver la orden", callback_data=f"admin:oview:{order_id}")])
    return InlineKeyboardMarkup(rows + _EXIT)


async def _approve(query, order_id: str, actor: str, tid: int, reason) -> None:
    out = await order_admin.approve_and_notify(order_id, actor, reason)
    from database.users import log_admin_action
    if not out["result"].get("already_applied"):
        await log_admin_action(tid, "approve_order", {"order_id": order_id, "reason": reason})
    await query.edit_message_text(out["summary"], parse_mode="HTML", reply_markup=InlineKeyboardMarkup(_EXIT))
