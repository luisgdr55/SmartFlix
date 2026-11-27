"""Aprobar y rechazar órdenes con aviso al cliente (O02 Lote 5). Único punto de entrada para
Telegram y el panel (un solo motor): llama a `checkout_service.approve` y a `orders.decide_order`.
"""
from __future__ import annotations

import logging
from typing import Optional

from database import orders
from services import checkout_service as cs
from services import order_notifications as notes
from utils.helpers import short_id

logger = logging.getLogger(__name__)


async def _names(order: dict) -> dict[str, str]:
    from database.platforms import get_platform_by_id
    names: dict[str, str] = {}
    for item in (order.get("manifest") or {}).get("items", []):
        pid = str(item.get("platform_id"))
        if pid not in names:
            row = await get_platform_by_id(pid)
            names[pid] = f"{(row or {}).get('icon_emoji') or '📺'} {(row or {}).get('name', '?')}"
    return names


async def _deliver_access(order: dict, result: dict, summary: str) -> tuple[str, bool, bool]:
    """Avisa al cliente (confirmación + acceso por ítem) y suma su compra. Devuelve
    (resumen del admin con el aviso de fallo si aplica, notificado, tiene Telegram)."""
    tid = (order.get("users") or {}).get("telegram_id")
    notified = False
    subs = await orders.get_subscriptions_with_access([i.get("subscription_id") for i in result.get("items", [])])
    messages = notes.client_item_messages(result.get("items", []), subs)
    if tid:
        from services.notification_service import send_to_user
        sent = [await send_to_user(tid, m) for m in messages]
        notified = bool(sent) and all(sent)
        try:
            from database.users import increment_user_purchases
            await increment_user_purchases(tid)
        except Exception as e:
            logger.warning(f"approve_and_notify: no se pudo sumar la compra del cliente {tid}: {e}")
    if not notified:
        summary += ("\n\n⚠️ <b>No se pudo avisar al cliente por Telegram</b>"
                    + (" (no tiene Telegram vinculado)." if not tid else ".")
                    + " Envíale sus datos de acceso por otro medio desde la ficha del cliente.")
    return summary, notified, bool(tid)


async def approve_and_notify(order_id: str, actor: str, reason: Optional[str] = None,
                             payment_reference: Optional[str] = None) -> dict:
    """Aprueba la orden y avisa al cliente. Lanza `ApprovalError` (mensaje para el admin) si falla;
    en ese caso no se cambió nada.

    Devuelve {"order", "result", "notices", "summary", "client_notified", "client_has_telegram"}.
    Si el cliente no tiene Telegram (cliente externo) o el envío falla, el resumen para el admin lo
    dice, para que entregue los datos por otro medio.
    """
    order = await orders.get_order(order_id)
    if not order:
        raise cs.ApprovalError("order_not_found")
    result = await cs.approve(order_id, actor, reason, payment_reference)
    names = await _names(order)
    notices = cs.approval_notices(result)
    summary = notes.admin_approval_summary(order, result, notices, names)
    notified, has_tg = False, bool((order.get("users") or {}).get("telegram_id"))
    if not result.get("already_applied"):
        summary, notified, has_tg = await _deliver_access(order, result, summary)
    return {"order": order, "result": result, "notices": notices, "summary": summary,
            "client_notified": notified, "client_has_telegram": has_tg}


async def approve_partial_and_notify(order_id: str, actor: str, keep: list[int], reason: Optional[str] = None,
                                     payment_reference: Optional[str] = None) -> dict:
    """Aprueba solo los ítems `keep` y deja el resto en una orden nueva pendiente. Avisa al cliente de lo
    aprobado (con su acceso) y de lo que queda pendiente. Lanza `ApprovalError` si falla (nada cambia)."""
    if not await orders.get_order(order_id):
        raise cs.ApprovalError("order_not_found")
    result = await cs.approve_partial(order_id, actor, keep, reason, payment_reference)
    order = await orders.get_order(order_id) or {}          # el manifiesto ya quedó solo con lo aprobado
    names = await _names(order)
    notices = cs.approval_notices(result)
    summary = notes.admin_approval_summary(order, result, notices, names)
    notified, has_tg = False, bool((order.get("users") or {}).get("telegram_id"))
    child_id = result.get("remainder_order_id")
    if child_id:
        summary += (f"\n\n✂️ <b>Orden dividida:</b> el resto ({result.get('remainder_items')} ítem(s), "
                    f"${result.get('remainder_total_usd')} / Bs {result.get('remainder_total_bs')}) quedó en la orden "
                    f"#{short_id(child_id)}, pendiente.")
    if not result.get("already_applied"):
        summary, notified, has_tg = await _deliver_access(order, result, summary)
        tid = (order.get("users") or {}).get("telegram_id")
        if tid and child_id:
            child = await orders.get_order(str(child_id)) or {}
            from services import checkout_flow as flow
            from services.notification_service import send_to_user
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            items_text = flow.items_block(child.get("manifest") or {"items": []}, await _names(child))
            await send_to_user(tid, notes.remainder_client_text(items_text, str(child_id), str(result.get("remainder_total_bs"))),
                               keyboard=InlineKeyboardMarkup([
                                   [InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")],
                                   [InlineKeyboardButton("🆘 Contactar soporte", callback_data="support:contact_admin")]]))
    return {"order": order, "result": result, "notices": notices, "summary": summary,
            "client_notified": notified, "client_has_telegram": has_tg, "remainder_order_id": child_id}


async def void_and_notify(order_id: str, actor: str, reason: str) -> dict:
    """Anula una orden aprobada con asiento contrario y avisa al cliente (texto genérico, sin el motivo
    interno). Lanza `ApprovalError` si no se puede (por ejemplo, `manual_reversal_required`): no se cambia nada."""
    order = await orders.get_order(order_id)
    if not order:
        raise cs.ApprovalError("order_not_found")
    result = await cs.void_order(order_id, actor, reason)
    names = await _names(order)
    summary = notes.admin_void_summary(order, result, names)
    notified = False
    if not result.get("already_applied"):
        tid = (order.get("users") or {}).get("telegram_id")
        if tid:
            from services.notification_service import send_to_user
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            notified = bool(await send_to_user(tid, notes.void_client_text(order_id), keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆘 Contactar soporte", callback_data="support:contact_admin")],
                [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")]])))
        if not notified:
            summary += "\n\n⚠️ No se pudo avisar al cliente por Telegram: avísale por otro medio."
    return {"order": order, "result": result, "summary": summary, "client_notified": notified}


async def reject_and_notify(order_id: str, actor: str, reason_code: str, detail: Optional[str] = None) -> dict:
    """Rechaza una orden pendiente con motivo y avisa al cliente con una acción (nuevo pedido).
    Lanza `CheckoutError` si el motivo no es válido o la transición no se permite. Devuelve
    {"ok", "reason", "order", "client_notified"}; `ok` False si otro proceso la decidió primero."""
    reason = notes.reject_reason_text(reason_code, detail)
    order = await orders.get_order(order_id)
    if not order:
        raise cs.CheckoutError("La orden no existe")
    ok = await orders.decide_order(order_id, "rejected", actor, reason)
    notified = False
    if ok:
        tid = (order.get("users") or {}).get("telegram_id")
        if tid:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from services.notification_service import send_to_user
            names = list((await _names(order)).values())
            notified = bool(await send_to_user(
                tid, notes.rejection_client_text(names, reason, order_id),
                keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Iniciar nuevo pedido", callback_data="menu:subscribe")]])))
    return {"ok": ok, "reason": reason, "order": order, "client_notified": notified}
