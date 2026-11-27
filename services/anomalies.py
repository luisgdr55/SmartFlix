"""Detector de anomalías de suscripciones y perfiles (O02 Lote 5). Puro: recibe filas ya leídas.

Es la versión en Python de las consultas del Lote 0 (`docs/subfases/o02-lote0-consultas-anomalias.sql`).
No corrige nada: solo lista, con las acciones que el admin puede aplicar (anular, liberar perfil,
fusionar), cada una auditada en el panel.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from services.checkout_service import days_overdue, is_overdue
from utils.dates import now_caracas, parse_db_datetime

CATEGORIES = (
    ("shared_profiles", "Perfil compartido por más de una suscripción activa", "Es lo más grave: dos clientes con el mismo perfil."),
    ("available_with_active", "Suscripción activa con perfil marcado libre", "El perfil podría entregarse a otro cliente."),
    ("duplicates", "Duplicados por cliente y plataforma", "Puede ser legítimo (dos perfiles) o una renovación duplicada."),
    ("overdue_occupied", "Mora con el perfil todavía ocupado", "Ya no hay corte automático: el corte es manual."),
    ("orphan_profiles", "Perfiles ocupados sin suscripción activa", "Stock perdido: nadie los usa."),
    ("expired_payment", "Pedidos caducados sin pago", "Si enviaron comprobante, el pago pudo quedar sin servicio."),
    ("pending_orphans", "Pedidos pendientes antiguos", "Debieron caducar; pueden aprobarse dos veces."),
)


def _s(value) -> str:
    return str(value) if value is not None else ""


def _sub_view(s: dict, now: datetime) -> dict:
    user = s.get("users") or {}
    plat = s.get("platforms") or {}
    return {
        "id": _s(s.get("id")), "short": _s(s.get("id"))[:8], "status": s.get("status"),
        "plan_type": s.get("plan_type"), "end_date": s.get("end_date"), "profile_id": s.get("profile_id"),
        "platform": plat.get("name") or "?", "user": user.get("name") or user.get("username") or "Sin nombre",
        "user_id": _s(s.get("user_id")),
        "days_overdue": days_overdue(s, now) if is_overdue(s, now) else 0,
        "has_receipt": bool((s.get("payment_reference") or "").strip()),
        "debt_reminder_count": s.get("debt_reminder_count") or 0,
    }


def _age_days(created, now: datetime) -> Optional[int]:
    if not created:
        return None
    try:
        return max((now - parse_db_datetime(created)).days, 0)
    except ValueError:
        return None


def detect(subs: list[dict], profiles: list[dict], now: Optional[datetime] = None, pending_hours: int = 4) -> dict:
    """Devuelve {categoría: [entradas]} + `counts`. Cada entrada trae ids completos y datos de pantalla."""
    now = now or now_caracas()
    out: dict[str, list] = {key: [] for key, _t, _d in CATEGORIES}
    prof_by_id = {_s(p.get("id")): p for p in profiles}
    live = [s for s in subs if s.get("status") in ("active", "expired")]

    # Duplicados usuario + plataforma
    groups: dict[tuple, list] = defaultdict(list)
    for s in live:
        groups[(_s(s.get("user_id")), _s(s.get("platform_id")))].append(s)
    for rows in groups.values():
        if len(rows) > 1:
            pids = [r.get("profile_id") for r in rows if r.get("profile_id")]
            out["duplicates"].append({
                "subs": [_sub_view(r, now) for r in sorted(rows, key=lambda r: _s(r.get("end_date")), reverse=True)],
                "same_profile": len(pids) == len(rows) and len(set(pids)) < len(pids),
                "platform": (rows[0].get("platforms") or {}).get("name") or "?",
                "user": ((rows[0].get("users") or {}).get("name") or (rows[0].get("users") or {}).get("username") or "Sin nombre"),
            })

    # Perfil compartido y activa con perfil libre
    by_profile: dict[str, list] = defaultdict(list)
    for s in subs:
        if s.get("status") == "active" and s.get("profile_id"):
            by_profile[_s(s["profile_id"])].append(s)
    for pid, rows in by_profile.items():
        prof = prof_by_id.get(pid) or {}
        if len(rows) > 1:
            out["shared_profiles"].append({
                "profile_id": pid, "profile": prof.get("profile_name") or pid[:8],
                "platform": (prof.get("platforms") or {}).get("name") or "?",
                "subs": [_sub_view(r, now) for r in rows],
            })
        if prof.get("status") == "available":
            for r in rows:
                out["available_with_active"].append({**_sub_view(r, now), "profile": prof.get("profile_name") or pid[:8],
                                                      "profile_id": pid})

    # Mora con perfil ocupado
    for s in live:
        prof = prof_by_id.get(_s(s.get("profile_id")))
        if is_overdue(s, now) and prof and prof.get("status") == "occupied":
            out["overdue_occupied"].append({**_sub_view(s, now), "profile": prof.get("profile_name") or "?"})

    # Perfiles ocupados sin suscripción activa (y sin mora que los explique)
    has_sub: dict[str, set] = defaultdict(set)
    for s in subs:
        if s.get("profile_id"):
            has_sub[_s(s["profile_id"])].add(s.get("status"))
    for p in profiles:
        if p.get("status") != "occupied":
            continue
        statuses = has_sub.get(_s(p.get("id")), set())
        if "active" in statuses:
            continue
        kind = "vencida_expired" if "expired" in statuses else ("solo_cancelada" if "cancelled" in statuses else "sin_suscripcion")
        if kind == "vencida_expired":
            continue  # ya aparece como mora con perfil ocupado
        out["orphan_profiles"].append({"profile_id": _s(p.get("id")), "profile": p.get("profile_name") or "?",
                                       "platform": (p.get("platforms") or {}).get("name") or "?", "kind": kind})

    # Pedidos caducados y pendientes antiguos
    for s in subs:
        if s.get("status") == "expired_payment":
            v = _sub_view(s, now)
            v["age_days"] = _age_days(s.get("created_at"), now)
            out["expired_payment"].append(v)
        elif s.get("status") == "pending_payment" and s.get("created_at"):
            try:
                hours = (now - parse_db_datetime(s["created_at"])).total_seconds() / 3600
            except ValueError:
                continue
            if hours > pending_hours:
                v = _sub_view(s, now)
                v["age_hours"] = int(hours)
                out["pending_orphans"].append(v)

    out["counts"] = {key: len(out[key]) for key, _t, _d in CATEGORIES}
    out["total"] = sum(out["counts"].values())
    return out
