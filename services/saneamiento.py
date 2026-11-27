"""O02 Lote 7 — Clasificación de saneamiento de datos existentes (lógica pura, sin acceso a la base).

Propone, no aplica. Categorías:
  reset_avisos     active vigente con avisos de un ciclo anterior (reminder_sent / expiry_notified / debt_reminder_count)
  expired_libre    `expired` heredado cuyo perfil ya está libre (o lo usa otro cliente) → cancelled
  expired_ocupado  `expired` con el perfil aún ocupado: se deja como mora (solo se informa)
  express_fuga     express vencido hace más de 24 h con el perfil ocupado → liberar y cancelar
Solo se aplican reset_avisos, expired_libre y express_fuga, fila por fila y con aprobación expresa.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Optional

from services.checkout_service import is_overdue
from utils.dates import parse_db_datetime

CATEGORIES = ("reset_avisos", "expired_libre", "expired_ocupado", "express_fuga")
APPLICABLE = ("reset_avisos", "expired_libre", "express_fuga")


def short(i) -> str:
    return str(i)[:8]


def classify(subs: Iterable[dict], profiles: dict[str, dict], now, active_by_profile: Optional[dict[str, set]] = None) -> dict:
    """`profiles`: {profile_id: {"status": ...}}. `active_by_profile`: {profile_id: {ids de subs active}} para saber
    si otro cliente usa el perfil. Devuelve {categoria: [{id, short, plataforma, estado, plan, end_date, ...}]}."""
    active_by_profile = active_by_profile or {}
    out = {c: [] for c in CATEGORIES}

    def occupied_by_this(sub) -> bool:
        pid = sub.get("profile_id")
        if not pid or (profiles.get(pid) or {}).get("status") != "occupied":
            return False
        others = set(active_by_profile.get(pid, set())) - {sub["id"]}
        return not others

    def row(sub, **extra):
        return {"id": sub["id"], "short": short(sub["id"]), "plataforma": (sub.get("platforms") or {}).get("slug") or "—",
                "estado": sub.get("status"), "plan": sub.get("plan_type"), "end_date": str(sub.get("end_date") or "")[:10],
                "user_id": sub.get("user_id"), "profile_id": sub.get("profile_id"), **extra}

    for s in subs:
        status, end = s.get("status"), s.get("end_date")
        if not end:
            continue
        end_dt = parse_db_datetime(end)
        if status == "active" and not is_overdue(s, now):
            stale = bool(s.get("expiry_notified")) or (s.get("debt_reminder_count") or 0) > 0 \
                or (bool(s.get("reminder_sent")) and end_dt > now + timedelta(days=4))
            if stale:
                out["reset_avisos"].append(row(s, reminder_sent=bool(s.get("reminder_sent")), expiry_notified=bool(s.get("expiry_notified")),
                                               debt_reminder_count=s.get("debt_reminder_count") or 0))
            continue
        if status == "expired":
            (out["expired_ocupado"] if occupied_by_this(s) else out["expired_libre"]).append(row(s))
            continue
        if s.get("plan_type") == "express" and status in ("active", "expired") and end_dt < now - timedelta(hours=24) and occupied_by_this(s):
            out["express_fuga"].append(row(s))
    return out


def report(plan: dict) -> str:
    """Texto sin datos personales: solo ids cortos, plataforma, estado y fecha."""
    lines = ["Saneamiento O02 (SOLO PROPUESTA; nada se ha aplicado)"]
    notes = {"reset_avisos": "reiniciar avisos (reminder_sent/expiry_notified=false, debt_reminder_count=0)",
             "expired_libre": "pasar a cancelled (perfil ya libre o usado por otro)",
             "expired_ocupado": "NO se toca: queda como mora (solo informativo)",
             "express_fuga": "liberar perfil (PIN nuevo) y pasar a cancelled"}
    for c in CATEGORIES:
        rows = plan.get(c, [])
        lines.append(f"\n[{c}] {len(rows)} fila(s) — {notes[c]}")
        for r in rows:
            lines.append(f"  {r['short']}  {r['plataforma']:<12} {r['estado']:<8} {r['plan'] or '—':<8} fin {r['end_date']}")
    return "\n".join(lines)


def select_approved(plan: dict, approved: dict) -> dict:
    """Filtra el plan a lo aprobado fila por fila (ids cortos). Rechaza categorías no aplicables y ids ambiguos o
    desconocidos: devuelve (seleccion, errores)."""
    selected, errors = {}, []
    for cat, shorts in (approved or {}).items():
        if cat not in APPLICABLE:
            errors.append(f"categoría no aplicable: {cat}")
            continue
        by_short: dict[str, list] = {}
        for r in plan.get(cat, []):
            by_short.setdefault(r["short"], []).append(r)
        for sh in shorts:
            hits = by_short.get(sh, [])
            if len(hits) != 1:
                errors.append(f"{cat}/{sh}: {'ambiguo' if hits else 'no está en la propuesta'}")
                continue
            selected.setdefault(cat, []).append(hits[0])
    return selected, errors
