"""Textos de la aprobación y el rechazo de órdenes (O02 Lote 5). Puro: sin Telegram ni base.

Los usan por igual Telegram y el panel (un solo motor, un solo texto). Todo valor dinámico se
escapa para HTML.
"""
from __future__ import annotations

import html
from typing import Optional

from bot.messages import ACCESS_DELIVERED, ACCESS_INSTRUCTIONS, PIN_LINE
from utils.dates import caracas_date, parse_db_datetime
from utils.helpers import format_datetime_vzla, short_id

# Motivos de rechazo con botón (el motivo exacto queda en la orden y se le explica al cliente).
REJECT_REASONS = {
    "monto": "El monto del comprobante no coincide con el total del pedido.",
    "ilegible": "El comprobante no se lee bien o está recortado.",
    "referencia": "La referencia de ese pago ya fue registrada anteriormente.",
    "destino": "El pago no fue hecho a los datos de cobro indicados.",
    "otro": "El comprobante no pudo ser verificado por el equipo.",
}


def reject_reason_text(code: str, detail: Optional[str] = None) -> str:
    base = REJECT_REASONS.get(code)
    if base is None:
        raise ValueError("Motivo de rechazo no válido")
    detail = (detail or "").strip()
    return f"{base} {detail}".strip() if detail else base


def rejection_client_text(names: list[str], reason: str, order_id: str) -> str:
    plats = ", ".join(html.escape(n) for n in names) or "tu pedido"
    return (f"❌ <b>Comprobante no aprobado</b>\n\n"
            f"Tu pago de <b>{plats}</b> (pedido #{short_id(order_id)}) no pudo ser aprobado.\n\n"
            f"<b>Motivo:</b> {html.escape(reason)}\n\n"
            "Puedes iniciar un nuevo pedido y enviar el comprobante correcto. "
            "Si crees que es un error, contáctanos. 📞")


def _access_text(sub: dict, platform_label: str) -> str:
    prof = sub.get("profiles") or {}
    account = prof.get("accounts") or {}
    slug = (sub.get("platforms") or {}).get("slug", "netflix")
    pin = prof.get("pin")
    return ACCESS_DELIVERED.format(
        platform=platform_label, profile_name=html.escape(str(prof.get("profile_name", ""))),
        email=html.escape(str(account.get("email", ""))), password=html.escape(str(account.get("password", ""))),
        pin_line=PIN_LINE.format(pin=html.escape(str(pin))) if pin else "",
        instructions=ACCESS_INSTRUCTIONS.get(slug, "").format(profile_name=html.escape(str(prof.get("profile_name", "")))),
    )


def client_item_messages(result_items: list[dict], subs_by_id: dict[str, dict]) -> list[str]:
    """Mensajes para el cliente tras aprobar: por ítem, confirmación + acceso (credenciales)."""
    messages: list[str] = []
    for item in result_items:
        sub = subs_by_id.get(str(item.get("subscription_id")))
        if not sub:
            continue
        plat = sub.get("platforms") or {}
        label = html.escape(f"{plat.get('icon_emoji', '')} {plat.get('name', '')}".strip())
        end = parse_db_datetime(item.get("end_date") or sub.get("end_date"))
        end_txt = format_datetime_vzla(end)
        tipo = item.get("tipo")
        if tipo == "renovacion":
            head = (f"✅ <b>¡Renovación confirmada!</b>\n\nTu suscripción de <b>{label}</b> ha sido renovada.\n\n"
                    f"📅 Nueva fecha de corte: <b>{end_txt}</b>")
            if item.get("profile_changed"):
                head += "\n\n⚠️ Tu perfil cambió: usa los datos que te enviamos a continuación."
        elif tipo == "cambio":
            head = (f"✅ <b>¡Cambio de plataforma confirmado!</b>\n\nYa tienes <b>{label}</b>.\n\n"
                    f"📅 Fecha de corte: <b>{end_txt}</b>")
        else:
            head = (f"✅ <b>¡Pago confirmado!</b>\n\nTu suscripción de <b>{label}</b> está activa.\n\n"
                    f"📅 Fecha de corte: <b>{end_txt}</b>")
        messages.append(head + "\n\nA continuación tus datos de acceso 👇")
        messages.append(_access_text(sub, label))
    return messages


def admin_approval_summary(order: dict, result: dict, notices: list[dict], names: dict[str, str]) -> str:
    """Resumen para el admin: qué se aplicó y qué debe atender (PIN a cambiar, perfil cambiado…)."""
    head = f"✅ <b>Orden #{short_id(order['id'])} aprobada</b>"
    if result.get("already_applied"):
        return (f"ℹ️ <b>Orden #{short_id(order['id'])}</b> ya estaba aprobada. "
                "No se aplicó nada nuevo ni se avisó otra vez al cliente.")
    lines = [head, ""]
    manifest = order.get("manifest") or {}
    for it in (result.get("items") or []):
        mi = (manifest.get("items") or [{}] * (int(it.get("index", 0)) + 1))[int(it.get("index", 0))]
        name = html.escape(names.get(mi.get("platform_id"), "Servicio"))
        end = caracas_date(it["end_date"]).strftime("%d/%m/%Y") if it.get("end_date") else "—"
        verb = {"renovacion": "Renovada", "nueva": "Activada", "cambio": "Cambiada"}.get(it.get("tipo"), "Aplicada")
        lines.append(f"• {name}: {verb} hasta el {end}")
    for n in notices:
        lines.append("")
        if n["kind"] == "profile_changed":
            lines.append("⚠️ <b>Se asignó otro perfil</b> a una renovación (el anterior ya lo usaba otro cliente o no estaba disponible). "
                         "El cliente recibió credenciales nuevas.")
        elif n["kind"] == "pin_rotated":
            lines.append(f"🔄 <b>Cambio de plataforma:</b> libera el perfil <b>{html.escape(str(n.get('profile_name')))}</b> "
                         f"({html.escape(str(n.get('account_email')))}). Entra con el PIN anterior "
                         f"<code>{html.escape(str(n.get('old_pin')))}</code> y cámbialo a "
                         f"<code>{html.escape(str(n.get('new_pin')))}</code>.")
        elif n["kind"] == "profile_not_released":
            lines.append(f"⚠️ El perfil de origen <b>{html.escape(str(n.get('profile_name')))}</b> NO se liberó: "
                         "lo usa otra suscripción activa. Revísalo en «Anomalías».")
    return "\n".join(lines)


def order_view(order: dict, names: dict[str, str]) -> dict:
    """Datos planos de una orden para las plantillas del panel (que ya escapan el HTML)."""
    from decimal import Decimal
    from services import checkout_flow as flow
    from services import ocr_signals
    from services.labels import badge_class, order_label
    m = order.get("manifest") or {}
    user = order.get("users") or {}
    flags = order.get("ocr_flags") or {}
    return {
        "id": str(order["id"]), "short": short_id(order["id"]),
        "status": order.get("status"), "status_label": order_label(order.get("status")),
        "badge": badge_class(order.get("status")),
        "client": user.get("name") or user.get("username") or "Sin nombre", "telegram_id": user.get("telegram_id"),
        "total_usd": m.get("total_usd"), "total_bs": f"{Decimal(m['total_bs']):,.2f}" if m.get("total_bs") else None,
        "rate": m.get("rate_used"), "created_at": order.get("created_at"), "expires_at": order.get("expires_at"),
        "reference": order.get("payment_reference"), "has_receipt": flow.order_has_receipt(order),
        "decided_by": order.get("decided_by"), "decision_reason": order.get("decision_reason"),
        "items": [{"label": flow.item_label(i, names, escape=False), "usd": i.get("subtotal_usd"),
                   "bs": f"{Decimal(i['amount_bs']):,.2f}" if i.get("amount_bs") else None} for i in m.get("items", [])],
        "signals": ocr_signals.summary_lines(flags) if flags else [],
        "alerts": flags.get("alertas", []) if flags else [],
    }
