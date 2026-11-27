"""Guardas de pagos y perfiles (O02-H). Lógica pura, sin base de datos ni Telegram.

Las usan los handlers de aprobar/rechazar, `cancel_and_buy` y el job de express.
Se mantiene aparte para poder probarla con `unittest` sin dependencias externas.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# Solo un pedido pendiente se puede aprobar o rechazar (O02-H, H1 y H2).
DECIDABLE_STATUS = "pending_payment"

# Una express `expired` solo se reprocesa si venció hace poco (O02-H, H4).
EXPRESS_EXPIRED_WINDOW = timedelta(hours=24)

STATUS_LABELS = {
    "pending_payment": "pendiente de pago",
    "active": "activa",
    "expired": "vencida",
    "expired_payment": "pedido caducado sin pago",
    "cancelled": "cancelada",
}


def can_decide_payment(status: Optional[str]) -> bool:
    """True solo si el pedido sigue pendiente de decisión del admin."""
    return status == DECIDABLE_STATUS


def status_label(status: Optional[str]) -> str:
    """Etiqueta legible del estado para mostrar al admin."""
    return STATUS_LABELS.get(status or "", status or "desconocido")


def profile_is_safe_to_release(profile_status: Optional[str], other_active_count: int) -> bool:
    """Un perfil se libera (y se rota su PIN) solo si está ocupado y ninguna otra
    suscripción activa lo usa: las suscripciones vencidas conservan su `profile_id`
    y el perfil pudo haberse entregado a otro cliente."""
    return profile_status == "occupied" and other_active_count == 0


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def express_release_candidates(rows: list[dict], now: datetime) -> list[dict]:
    """Filtra las express a liberar.

    - `active` con `end_date` vencida: flujo de siempre (se incluyen).
    - `expired`: solo si venció en las últimas 24 h y su perfil sigue `occupied`
      (la carrera con `auto_expire` las dejó sin liberar). Las más viejas son fuga
      histórica y se sanean aparte, con autorización.
    Cualquier otro estado se ignora.
    """
    out: list[dict] = []
    for row in rows:
        end = _parse_dt(row.get("end_date"))
        if end is None:
            continue
        if end.tzinfo is None:
            end = end.replace(tzinfo=now.tzinfo)
        if end > now:
            continue
        status = row.get("status")
        if status == "active":
            out.append(row)
        elif status == "expired":
            profile = row.get("profiles") or {}
            if now - end <= EXPRESS_EXPIRED_WINDOW and profile.get("id") and profile.get("status") == "occupied":
                out.append(row)
    return out
