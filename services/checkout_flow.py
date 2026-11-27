"""Armado del pedido del cliente y textos del checkout v2 (O02 Lote 4). Lógica pura: sin Telegram,
base ni Redis (los datos entran por parámetros), para poder probarla con `unittest`.

Reglas (Lote 4 de la orden):
- Un carrito o compra crea UNA orden con ítems tipados; nunca suscripciones.
- Los precios salen de la plataforma en la base (no de lo que quedó en el carrito de Redis).
- Las renovaciones apuntan a una `subscription_id` del propio cliente y NO reservan perfil;
  solo los ítems `nueva` reservan perfil (30 min).
- Una renovación cubre 1 periodo (igual que hoy); el selector de varios periodos queda para
  cuando el Orquestador lo decida: `renewal_items` ya acepta `periods_by_sub`.
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from typing import Optional

from services.checkout_service import (
    CheckoutError,
    ITEM_NEW,
    ITEM_RENEWAL,
    build_new_item,
    build_renewal_item,
    money,
    to_decimal,
)
from utils.helpers import short_id

PROFILE_RESERVATION_MINUTES = 30
_PRICE_FIELD = {"monthly": "monthly_price_usd", "express": "express_price_usd"}
_PLAN_LABEL = {"monthly": "Mensual", "express": "Express 24h"}


def unit_price_usd(platform: Optional[dict], plan_type: str) -> Decimal:
    """Precio vigente de la plataforma para el plan; `CheckoutError` si no está configurado."""
    field = _PRICE_FIELD.get(plan_type)
    if not platform or not field:
        raise CheckoutError("No se encontró el precio del servicio")
    price = money(to_decimal(platform.get(field) or 0, "El precio"))
    if price <= 0:
        raise CheckoutError(f"El precio de {platform.get('name', 'este servicio')} no está configurado")
    return price


def purchase_items(cart: list[dict], platforms: dict[str, dict]) -> list[dict]:
    """Ítems `nueva` desde el carrito de compra (lista de dicts con platform_id y plan_type)."""
    items = []
    for entry in cart:
        pid = str(entry.get("platform_id") or "")
        plan = entry.get("plan_type") or "monthly"
        items.append(build_new_item(pid, plan, unit_price_usd(platforms.get(pid), plan), plan))
    return items


def renewal_items(
    selected: list[dict],
    subs_by_id: dict[str, dict],
    platforms: dict[str, dict],
    user_id: str,
    periods_by_sub: Optional[dict[str, int]] = None,
) -> list[dict]:
    """Ítems `renovacion` desde las entradas seleccionadas del carrito de renovación.

    Cada entrada se resuelve por su `sub_id` y se exige que la suscripción sea del cliente
    (el carrito vive en Redis: no se confía en él). Una por servicio: dos Netflix son dos ítems.
    """
    items = []
    seen = set()
    for entry in selected:
        sid = str(entry.get("sub_id") or "")
        sub = subs_by_id.get(sid)
        if not sub or str(sub.get("user_id")) != str(user_id):
            raise CheckoutError("Uno de los servicios del carrito ya no está disponible para renovar")
        if sid in seen:
            continue
        seen.add(sid)
        pid = str(sub.get("platform_id") or "")
        price = unit_price_usd(platforms.get(pid), sub.get("plan_type") or "monthly")
        items.append(build_renewal_item(sub, (periods_by_sub or {}).get(sid, 1), price))
    return items


def reservation_targets(items: list[dict]) -> list[tuple[str, str]]:
    """(plataforma, tipo de perfil) a reservar: SOLO los ítems `nueva` (las renovaciones no)."""
    return [(i["platform_id"], i.get("profile_type") or i.get("plan_type") or "monthly")
            for i in items if i.get("tipo") == ITEM_NEW]


def order_has_receipt(order: dict) -> bool:
    """¿El cliente ya envió comprobante de esta orden?"""
    return bool((order.get("receipt_text") or "").strip() or (order.get("payment_reference") or "").strip())


def pending_order_policy(order: Optional[dict]) -> str:
    """Qué hacer cuando el cliente confirma un pedido nuevo y ya tiene una orden pendiente:
    - "none": no hay orden pendiente → crear la nueva.
    - "supersede": hay una sin comprobante → se cancela y se crea la nueva.
    - "waiting_review": ya envió comprobante y espera al admin → NO se crea otra (no se pisa un pago en revisión).
    """
    if not order or order.get("status") != "pending":
        return "none"
    return "waiting_review" if order_has_receipt(order) else "supersede"


# ── Textos ───────────────────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(iso)


def item_label(item: dict, names: dict[str, str], escape: bool = True) -> str:
    """Una línea por ítem (HTML escapado salvo `escape=False`, para plantillas que ya escapan).
    `names` mapea platform_id → «🎬 Netflix»."""
    name = names.get(item.get("platform_id"), "Servicio")
    name = html.escape(name) if escape else name
    if item.get("tipo") == ITEM_RENEWAL:
        return f"🔄 Renovación {name} (hasta el {_fmt_date(item.get('cobertura_hasta'))})"
    if item.get("tipo") == ITEM_NEW:
        return f"🆕 {name} — {_PLAN_LABEL.get(item.get('plan_type'), item.get('plan_type'))}"
    return f"🔁 Cambio a {name}"


def items_block(manifest: dict, names: dict[str, str]) -> str:
    lines = []
    for item in manifest.get("items", []):
        lines.append(f"{item_label(item, names)}: ${item['subtotal_usd']} / Bs {Decimal(item['amount_bs']):,.2f}")
    return "\n".join(lines)


def payment_instructions_text(order: dict, names: dict[str, str], cfg: dict) -> str:
    """Instrucciones de pago de UNA orden: un solo monto exacto y un solo comprobante."""
    m = order["manifest"]
    esc = lambda k: html.escape(str(cfg.get(k) or "N/A"))  # noqa: E731
    has_new = any(i.get("tipo") == ITEM_NEW for i in m["items"])
    reserve = (f"\n🔒 Los perfiles de servicios nuevos se reservan por {PROFILE_RESERVATION_MINUTES} minutos."
               if has_new else "")
    return (
        "💳 <b>Instrucciones de Pago</b>\n\n"
        f"{items_block(m, names)}\n\n"
        f"💰 <b>Total a pagar:</b> <code>Bs {Decimal(m['total_bs']):,.2f}</code> (${m['total_usd']} USD)\n"
        f"📊 Tasa: Bs {Decimal(m['rate_used']):,.2f}/USD\n\n"
        "Realiza <b>un solo pago</b> por <b>Pago Móvil</b>:\n"
        f"🏦 <b>Banco:</b> {esc('banco')}\n"
        f"📱 <b>Teléfono:</b> {esc('telefono')}\n"
        f"🪪 <b>Cédula:</b> {esc('cedula')}\n"
        f"👤 <b>Titular:</b> {esc('titular')}\n\n"
        "📸 Luego envía <b>un solo comprobante</b> aquí (foto, o la imagen como archivo)."
        f"{reserve}\n"
        "⏰ Tu pedido queda abierto <b>24 horas</b>.\n\n"
        f"🔖 <i>Ref. pedido: #{short_id(order['id'])}</i>"
    )


def admin_order_ticket(order: dict, names: dict[str, str], client_name: str, telegram_id, signal_lines: list[str]) -> str:
    """Ticket para el admin: ítems, totales y señales del comprobante (solo marcan)."""
    m = order["manifest"]
    signals = "\n".join(html.escape(l) for l in signal_lines)
    return (
        f"💳 <b>ORDEN DE PAGO #{short_id(order['id'])}</b>\n\n"
        f"👤 <b>Cliente:</b> {html.escape(client_name)} (<code>{telegram_id}</code>)\n\n"
        f"{items_block(m, names)}\n\n"
        f"💰 <b>Total:</b> ${m['total_usd']} USD / Bs {Decimal(m['total_bs']):,.2f} "
        f"(tasa {Decimal(m['rate_used']):,.2f})\n\n"
        f"🔍 <b>Señales del comprobante</b> (solo orientan; decides tú):\n{signals}"
    )
