"""Etiquetas humanas de estados (O02 Lote 5). Puro: sin base ni Telegram.

El estado crudo (`pending_payment`, `expired_payment`…) no se muestra al admin: se traduce, y la mora
se deriva de la fecha (no es un estado): una suscripción `active` o `expired` (legado) con
`end_date` anterior a ahora aparece «En mora».
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

SUBSCRIPTION_LABELS = {
    "active": "Activa",
    "expired": "Vencida",
    "expired_payment": "Pedido caducado sin pago",
    "pending_payment": "Pendiente de pago",
    "cancelled": "Cancelada",
    "voided": "Anulada",
}
ORDER_LABELS = {
    "pending": "Pendiente",
    "approved": "Aprobada",
    "rejected": "Rechazada",
    "expired": "Caducada",
    "cancelled": "Cancelada por el cliente",
    "voided": "Anulada",
}
# Clase CSS de insignia del panel (las mismas que ya usan las plantillas).
_BADGE = {"active": "badge-active", "pending_payment": "badge-pending", "pending": "badge-pending",
          "approved": "badge-active", "cancelled": "badge-cancelled", "voided": "badge-cancelled",
          "rejected": "badge-cancelled"}


def subscription_label(status: Optional[str]) -> str:
    return SUBSCRIPTION_LABELS.get(status or "", status or "Sin estado")


def order_label(status: Optional[str]) -> str:
    return ORDER_LABELS.get(status or "", status or "Sin estado")


def badge_class(status: Optional[str]) -> str:
    return _BADGE.get(status or "", "badge-expired")


def subscription_display(sub: dict, now: Optional[datetime] = None) -> tuple[str, str]:
    """(etiqueta, clase CSS) de una suscripción, mostrando la mora derivada."""
    from services.checkout_service import days_overdue, is_overdue
    from utils.dates import now_caracas
    now = now or now_caracas()
    if is_overdue(sub, now):
        n = days_overdue(sub, now)
        return f"En mora ({n} día{'s' if n != 1 else ''})", "badge-expired"
    status = sub.get("status")
    return subscription_label(status), badge_class(status)
