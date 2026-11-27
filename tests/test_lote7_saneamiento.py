"""O02 Lote 7: clasificación de saneamiento, aprobación fila por fila y script con --dry-run por defecto."""
from tests import support  # noqa: F401

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from services import saneamiento as sn
from tests.test_checkout_service import caracas
from tests.test_lote6_scheduler import FakeSB, sub

NOW = caracas(2026, 12, 10, 9)
spec = importlib.util.spec_from_file_location("o02_saneamiento", os.path.join(os.path.dirname(__file__), "..", "scripts", "o02_saneamiento.py"))
script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)


def plan_rows():
    subs = [
        sub("stale1", None, expiry_notified=True), sub("stale2", None, debt_reminder_count=3),
        sub("stale3", None, reminder_sent=True, end_date=(NOW + timedelta(days=20)).isoformat()),
        sub("okd3", None, reminder_sent=True, end_date=(NOW + timedelta(days=2)).isoformat()),      # D-3 legítimo
        sub("clean", None),
        sub("exfree", 20, status="expired", profile_id="pFree"), sub("exnone", 20, status="expired", profile_id=None),
        sub("exshared", 20, status="expired", profile_id="pShared"), sub("exocc", 20, status="expired", profile_id="pOcc"),
        sub("expr", 2, plan="express", profile_id="pExp", end_date=(NOW - timedelta(hours=30)).isoformat()),
        sub("expnew", 0, plan="express", profile_id="pExp2", end_date=(NOW - timedelta(hours=5)).isoformat()),
        sub("mora", 5, profile_id="pM"),
    ]
    profiles = {"pFree": {"status": "available"}, "pShared": {"status": "occupied"}, "pOcc": {"status": "occupied"},
                "pExp": {"status": "occupied"}, "pExp2": {"status": "occupied"}, "pM": {"status": "occupied"}}
    active = {"pShared": {"other-sub"}, "pM": {"mora"}}
    return sn.classify(subs, profiles, NOW, active)


class TestClassify(unittest.TestCase):
    def test_categories(self):
        p = plan_rows()
        ids = lambda c: {r["id"] for r in p[c]}
        self.assertEqual(ids("reset_avisos"), {"stale1", "stale2", "stale3"})
        self.assertEqual(ids("expired_libre"), {"exfree", "exnone", "exshared"})
        self.assertEqual(ids("expired_ocupado"), {"exocc"})
        self.assertEqual(ids("express_fuga"), {"expr"})          # el express de 5 h y la mora normal no se tocan

    def test_report_has_no_personal_data_or_full_ids(self):
        s = [dict(sub("x", None, expiry_notified=True), id="12345678-aaaa-bbbb-cccc-1234567890ab",
                  users={"name": "Ana Pérez", "telegram_id": 99})]
        text = sn.report(sn.classify(s, {}, NOW))
        self.assertIn("12345678", text)
        self.assertNotIn("aaaa-bbbb", text); self.assertNotIn("Ana", text); self.assertNotIn("99", text)
        self.assertIn("SOLO PROPUESTA", text)

    def test_approval_is_row_by_row_and_strict(self):
        p = plan_rows()
        sel, err = sn.select_approved(p, {"reset_avisos": ["stale1"]})
        self.assertEqual([r["id"] for r in sel["reset_avisos"]], ["stale1"]); self.assertEqual(err, [])
        _, err = sn.select_approved(p, {"expired_ocupado": ["exocc"]}); self.assertTrue(err)       # no aplicable
        _, err = sn.select_approved(p, {"expired_libre": ["zzzzzzzz"]}); self.assertTrue(err)      # desconocido
        dup = {"expired_libre": [{"id": "abcdefgh-1", "short": "abcdefgh"}, {"id": "abcdefgh-2", "short": "abcdefgh"}]}
        _, err = sn.select_approved(dup, {"expired_libre": ["abcdefgh"]}); self.assertIn("ambiguo", err[0])


class Rec(FakeSB):
    def __init__(self, **t): super().__init__(**t); self.writes = 0
    def table(self, n):
        q = super().table(n); orig = q.execute
        def ex(): 
            if q.op == "update": self.writes += 1
            return orig()
        q.execute = ex; return q


class TestScript(unittest.IsolatedAsyncioTestCase):
    def test_dry_run_is_the_default_and_never_writes(self):
        rows = [sub("s1", None, expiry_notified=True)]
        sb = Rec(subscriptions=rows, profiles=[])
        sb.table = lambda n, _t=sb.table: _t(n)
        with mock.patch("database.get_supabase", return_value=sb), mock.patch.object(script, "_fetch_all", side_effect=lambda s, t, *a, **k: rows if t == "subscriptions" else []), \
             mock.patch.object(script, "now_caracas", return_value=NOW), mock.patch("builtins.print"):
            self.assertEqual(script.main([]), 0)
        self.assertEqual(sb.writes, 0); self.assertTrue(rows[0]["expiry_notified"])

    def test_apply_without_confirmation_or_approval_does_nothing(self):
        with mock.patch("database.get_supabase", return_value=Rec()), mock.patch("builtins.print"):
            self.assertEqual(script.main(["--apply"]), 2)
            self.assertEqual(script.main(["--apply", "--plan", "p", "--approved", "a", "--admin-id", "1", "--confirm", "si"]), 2)

    async def test_apply_changes_only_approved_rows_with_guards_and_undo(self):
        rows = [sub("a", None, expiry_notified=True, debt_reminder_count=2), sub("b", 20, status="expired"), sub("c", 20, status="expired", user_id="U-pend")]
        sb = Rec(subscriptions=rows)
        plan = sn.classify(rows, {}, NOW)
        sel, _ = sn.select_approved(plan, {"reset_avisos": [plan["reset_avisos"][0]["short"]], "expired_libre": [r["short"] for r in plan["expired_libre"]]})
        async def pend(uid): return {"orders": ["X"], "legacy": []} if uid == "U-pend" else {"orders": [], "legacy": []}
        with tempfile.TemporaryDirectory() as d, mock.patch("database.orders.pending_for_user", pend), \
             mock.patch("database.users.log_admin_action", new=mock.AsyncMock()):
            res = await script.apply_plan(sb, sel, 1, os.path.join(d, "u.json"))
            undo = json.load(open(os.path.join(d, "u.json")))
        by = {r["id"]: r for r in rows}
        self.assertEqual((by["a"]["expiry_notified"], by["a"]["debt_reminder_count"]), (False, 0))
        self.assertEqual(by["b"]["status"], "cancelled")
        self.assertEqual(by["c"]["status"], "expired")                                   # pago pendiente: omitida
        self.assertEqual([m for _s, m in res["omitidas"]], ["pago pendiente"])
        self.assertEqual({u["id"] for u in undo}, {"a", "b"})
        self.assertEqual(next(u for u in undo if u["id"] == "a")["restore"]["debt_reminder_count"], 2)

    async def test_conditional_update_skips_a_row_that_changed(self):
        rows = [sub("b", 20, status="expired")]
        plan = sn.classify(rows, {}, NOW)
        rows[0]["status"] = "active"                                                      # la renovaron entre propuesta y aplicación
        sel, _ = sn.select_approved(plan, {"expired_libre": [plan["expired_libre"][0]["short"]]})
        with tempfile.TemporaryDirectory() as d, mock.patch("database.orders.pending_for_user", new=mock.AsyncMock(return_value={"orders": [], "legacy": []})), \
             mock.patch("database.users.log_admin_action", new=mock.AsyncMock()):
            res = await script.apply_plan(Rec(subscriptions=rows), sel, 1, os.path.join(d, "u.json"))
        self.assertEqual(rows[0]["status"], "active"); self.assertEqual(res["aplicadas"], [])


if __name__ == "__main__":
    unittest.main()
