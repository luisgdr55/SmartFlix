"""Aprobar y rechazar órdenes con aviso al cliente (O02 Lote 5). Único punto de entrada para
Telegram y el panel (un solo motor): llama a `checkout_service.approve` y a `orders.decide_order`.
"""
from __future__ import annotations

import logging
from typing import Optional

from database import orders
from services import checkout_service as cs
from services import order_notifications as notes

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
    client = (order.get("users") or {})
    tid = client.get("telegram_id")
    notified = False
    if not result.get("already_applied"):
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
    return {"order": order, "result": result, "notices": notices, "summary": summary,
            "client_notified": notified, "client_has_telegram": bool(tid)}


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
