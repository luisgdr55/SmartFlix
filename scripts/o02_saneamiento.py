"""O02 Lote 7 — Saneamiento de datos existentes. **Por defecto NO escribe** (--dry-run implícito).

Lee `subscriptions`/`profiles` de la base a la que apunten las variables de entorno. Ejecutarlo contra producción
requiere autorización expresa del Orquestador para esa ejecución (CLAUDE.md). Uso:

  python scripts/o02_saneamiento.py --out /ruta/fuera/del/repo/plan.json          # propone; no escribe
  python scripts/o02_saneamiento.py --apply --plan plan.json --approved ok.json \
        --confirm EJECUTAR --admin-id <telegram_id>                               # aplica SOLO lo aprobado

`ok.json`: {"reset_avisos": ["ab12cd34"], "expired_libre": [...], "express_fuga": [...]} (ids cortos aprobados).
Cada UPDATE es condicional (solo si la fila sigue como se propuso), se omiten clientes con pago pendiente y se guarda
un archivo de deshacer junto al plan. El plan y el deshacer contienen UUID: NO los subas al repositorio.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import saneamiento as sn  # noqa: E402
from utils.dates import now_caracas  # noqa: E402


def _fetch_all(sb, table, select, **eq):
    rows, start = [], 0
    while True:
        q = sb.table(table).select(select)
        for k, v in eq.items():
            q = q.eq(k, v)
        data = q.range(start, start + 999).execute().data or []
        rows += data
        if len(data) < 1000:
            return rows
        start += 1000


def build_plan(sb, now) -> dict:
    subs = [s for s in _fetch_all(sb, "subscriptions", "id,status,plan_type,end_date,profile_id,user_id,reminder_sent,expiry_notified,debt_reminder_count,platforms(slug)")
            if s.get("status") in ("active", "expired")]
    profiles = {p["id"]: p for p in _fetch_all(sb, "profiles", "id,status")}
    active_by_profile: dict = {}
    for s in subs:
        if s["status"] == "active" and s.get("profile_id"):
            active_by_profile.setdefault(s["profile_id"], set()).add(s["id"])
    return sn.classify(subs, profiles, now, active_by_profile)


async def apply_plan(sb, selected: dict, admin_id: int, undo_path: str) -> dict:
    from database.orders import pending_for_user
    from database.profiles import release_profile_for_cut
    from database.users import log_admin_action
    done, skipped, undo = [], [], []
    for cat, rows in selected.items():
        for r in rows:
            sid = r["id"]
            if cat != "reset_avisos":
                pend = await pending_for_user(str(r.get("user_id")))
                if pend["orders"] or pend["legacy"]:
                    skipped.append((r["short"], "pago pendiente"))
                    continue
            if cat == "reset_avisos":
                res = (sb.table("subscriptions").update({"reminder_sent": False, "expiry_notified": False, "debt_reminder_count": 0})
                       .eq("id", sid).eq("status", "active").execute())
                undo.append({"id": sid, "restore": {k: r[k] for k in ("reminder_sent", "expiry_notified", "debt_reminder_count")}})
            elif cat == "expired_libre":
                res = sb.table("subscriptions").update({"status": "cancelled"}).eq("id", sid).eq("status", "expired").execute()
                undo.append({"id": sid, "restore": {"status": "expired"}})
            else:  # express_fuga
                info = await release_profile_for_cut(r["profile_id"], exclude_sub_id=sid) if r.get("profile_id") else None
                if not info or not info["released"]:
                    skipped.append((r["short"], "perfil no liberado"))
                    continue
                res = sb.table("subscriptions").update({"status": "cancelled"}).eq("id", sid).in_("status", ["active", "expired"]).execute()
                undo.append({"id": sid, "restore": {"status": r["estado"]}, "nota": "el PIN anterior no se restaura"})
            (done if res.data else skipped).append((r["short"], cat) if res.data else (r["short"], "la fila cambió"))
            await log_admin_action(admin_id, "o02_saneamiento", {"categoria": cat, "sub": r["short"]})
    with open(undo_path, "w") as f:
        json.dump(undo, f)
    return {"aplicadas": done, "omitidas": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="dónde guardar la propuesta (JSON con UUID; fuera del repo)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan")
    ap.add_argument("--approved")
    ap.add_argument("--confirm")
    ap.add_argument("--admin-id", type=int)
    a = ap.parse_args(argv)
    from database import get_supabase
    sb = get_supabase()
    if not a.apply:
        plan = build_plan(sb, now_caracas())
        print(sn.report(plan))
        if a.out:
            with open(a.out, "w") as f:
                json.dump(plan, f)
            print(f"\nPropuesta guardada en {a.out} (contiene UUID: no la subas al repositorio).")
        return 0
    if a.confirm != "EJECUTAR" or not (a.plan and a.approved and a.admin_id):
        print("Aplicar exige --plan, --approved, --admin-id y --confirm EJECUTAR. No se hizo nada.")
        return 2
    with open(a.plan) as f:
        plan = json.load(f)
    with open(a.approved) as f:
        approved = json.load(f)
    selected, errors = sn.select_approved(plan, approved)
    if errors:
        print("Aprobación inválida; no se hizo nada:\n  " + "\n  ".join(errors))
        return 2
    result = asyncio.run(apply_plan(sb, selected, a.admin_id, a.plan + ".deshacer.json"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
