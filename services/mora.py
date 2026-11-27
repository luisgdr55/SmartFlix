"""Mora de clientes y resumen diario para el admin (O02 Lote 6, opción B: sin corte automático).

Puro: recibe filas ya leídas. La mora es un dato derivado (`checkout_service.is_overdue`), no un estado.
El resumen existe para que el corte manual no dependa de la memoria del admin: lista a los clientes con
7 días de mora o más, su deuda y un botón por cliente (con Telegram) para abrir su ficha.
"""
from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal
from typing import Optional

from services.checkout_service import days_overdue, is_overdue, money, periods_to_be_current, to_decimal
from utils.dates import now_caracas

MIN_DAYS = 7
SUMMARY_CAP = 15


def debt_usd(sub: dict, now: Optional[datetime] = None) -> Decimal:
    """Deuda estimada en USD: precio de la suscripción × periodos mínimos para quedar al día."""
    price = money(to_decimal(sub.get("price_usd") or 0, "El precio"))
    if price <= 0 or not sub.get("end_date"):
        return Decimal("0.00")
    return price * periods_to_be_current(sub["end_date"], now)


def mora_rows(subs: list[dict], now: Optional[datetime] = None, min_days: int = MIN_DAYS) -> list[dict]:
    """Clientes en mora de `min_days` o más, de mayor a menor deuda. Una fila por suscripción."""
    now = now or now_caracas()
    rows = []
    for s in subs:
        if not is_overdue(s, now):
            continue
        n = days_overdue(s, now)
        if n < min_days:
            continue
        user, plat = s.get("users") or {}, s.get("platforms") or {}
        rows.append({
            "sub_id": str(s.get("id")), "days": n, "debt": debt_usd(s, now),
            "client": user.get("name") or user.get("username") or "Sin nombre", "telegram_id": user.get("telegram_id"),
            "platform": f"{plat.get('icon_emoji', '')} {plat.get('name', '')}".strip() or "?",
            "has_profile": bool(s.get("profile_id")),
        })
    rows.sort(key=lambda r: (r["debt"], r["days"]), reverse=True)
    return rows


def summary_text(rows: list[dict], panel_url: str = "", cap: int = SUMMARY_CAP) -> str:
    """Mensaje del resumen diario (HTML). Se corta en `cap` filas y remite al panel para el resto."""
    total_debt = sum((r["debt"] for r in rows), Decimal("0"))
    lines = [f"📋 <b>Mora de {MIN_DAYS} días o más</b>", f"{len(rows)} servicio(s) · deuda estimada ${total_debt:.2f} USD", ""]
    for r in rows[:cap]:
        lines.append(f"• <b>{html.escape(r['client'])}</b> — {html.escape(r['platform'])}: {r['days']} días, ${r['debt']:.2f}"
                     + ("" if r["telegram_id"] else " (sin Telegram)"))
    if len(rows) > cap:
        lines.append(f"… y {len(rows) - cap} más")
    lines += ["", "El servicio de estos clientes sigue activo: el corte es manual, desde su ficha."]
    if panel_url:
        lines.append(f"Ver todos: {html.escape(panel_url)}")
    return "\n".join(lines)


def client_buttons(rows: list[dict], cap: int = SUMMARY_CAP) -> list[tuple[str, str]]:
    """(texto, callback) por cliente con Telegram (sin repetir): abre su ficha de admin."""
    seen, out = set(), []
    for r in rows[:cap]:
        tid = r.get("telegram_id")
        if tid and tid not in seen:
            seen.add(tid)
            out.append((f"👤 {r['client'][:28]}", f"admin:client_detail:{tid}"))
    return out
