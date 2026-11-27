"""Pruebas del motor de aprobación de O02 (Lote 3): `database/orders.py`, `checkout_service.approve`
y `void_subscription`, con una base y un `rpc` falsos (datos ficticios)."""
from tests import support  # noqa: F401

import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import httpx
from postgrest.exceptions import APIError

from database import orders, subscriptions as subs_mod
from services import checkout_service as cs
from utils.dates import parse_db_datetime
from tests.test_checkout_service import caracas, sub as mk_sub

USER = "11111111-1111-1111-1111-111111111111"
OID = "22222222-2222-2222-2222-222222222222"


class FakeQ:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self._limit, self._order = "select", None, [], None, None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def eq(self, c, v): self.filters.append(("eq", c, v)); return self
    def lte(self, c, v): self.filters.append(("lte", c, v)); return self
    def limit(self, n): self._limit = n; return self
    def order(self, c, desc=False): self._order = (c, desc); return self

    def _match(self, r):
        for op, c, v in self.filters:
            if op == "eq" and r.get(c) != v:
                return False
            if op == "lte" and not (r.get(c) is not None and r.get(c) <= v):
                return False
        return True

    def execute(self):
        if self.db.fail:
            raise RuntimeError("db caída")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            row = dict(self.payload); row.setdefault("id", f"id-{len(rows)+1}"); row.setdefault("created_at", f"t{len(rows)+1:04d}")
            rows.append(row)
            return SimpleNamespace(data=[dict(row)])
        hit = [r for r in rows if self._match(r)]
        if self.op == "update":
            if self.db.lose_update:
                return SimpleNamespace(data=[])
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self._order:
            hit.sort(key=lambda r: r.get(self._order[0]) or "", reverse=self._order[1])
        if self._limit:
            hit = hit[: self._limit]
        return SimpleNamespace(data=[dict(r) for r in hit])


class FakeSB:
    def __init__(self, tables=None, fail=False, lose_update=False, rpc_result=None, rpc_exc=None):
        self.tables = tables or {}
        self.fail, self.lose_update = fail, lose_update
        self.rpc_result, self.rpc_exc, self.rpc_calls = rpc_result, rpc_exc, []

    def table(self, name): return FakeQ(self, name)

    def rpc(self, fn, params):
        self.rpc_calls.append((fn, params))
        outer = self

        class _R:
            def execute(self_inner):
                if outer.rpc_exc:
                    raise outer.rpc_exc
                return SimpleNamespace(data=outer.rpc_result)
        return _R()


def manifest():
    return cs.build_manifest([cs.build_renewal_item(mk_sub("sub-A"), 1, "5"), cs.build_new_item("pl", "monthly", "4")], "1000")


def pending_order(oid=OID, status="pending", **extra):
    return {"id": oid, "user_id": USER, "status": status, "version": 1, **extra}


def api_error(message, details=None, code="P0001"):
    return APIError({"message": message, "code": code, "details": details, "hint": None})


class TestOrdersRepo(unittest.IsolatedAsyncioTestCase):
    def use(self, db):
        p = mock.patch.object(orders, "get_supabase", lambda: db)
        p.start(); self.addCleanup(p.stop)

    async def test_create_order_validates_and_sets_24h_ttl(self):
        db = FakeSB(); self.use(db)
        created = caracas(2026, 12, 1, 10)
        m = manifest()
        row = await orders.create_order(USER, m, payment_reference="REF1", ocr_flags={"monto": "ok"}, now=created)
        self.assertEqual(row["status"], "pending")
        self.assertEqual((row["total_usd"], row["total_bs"], row["rate_used"]), (m["total_usd"], m["total_bs"], m["rate_used"]))
        self.assertEqual(parse_db_datetime(row["expires_at"]) - created, timedelta(hours=24))

    async def test_create_order_rejects_tampered_manifest_without_writing(self):
        db = FakeSB(); self.use(db)
        m = manifest(); m["total_bs"] = "1.00"
        with self.assertRaises(cs.CheckoutError):
            await orders.create_order(USER, m)
        self.assertEqual(db.tables.get("checkout_orders", []), [])

    async def test_create_order_db_error_returns_none(self):
        self.use(FakeSB(fail=True))
        self.assertIsNone(await orders.create_order(USER, manifest()))

    async def test_pending_order_for_user_is_the_latest(self):
        db = FakeSB({"checkout_orders": [
            {**pending_order("a"), "created_at": "t1"}, {**pending_order("b"), "created_at": "t3"},
            {**pending_order("c", status="approved"), "created_at": "t9"},
            {**pending_order("d"), "user_id": "otro", "created_at": "t8"}]})
        self.use(db)
        self.assertEqual((await orders.get_pending_order_for_user(USER))["id"], "b")
        self.assertEqual([o["id"] for o in await orders.list_pending_orders()], ["a", "b", "d"])

    async def test_reads_return_empty_on_db_error(self):
        self.use(FakeSB(fail=True))
        self.assertIsNone(await orders.get_order(OID))
        self.assertIsNone(await orders.get_pending_order_for_user(USER))
        self.assertEqual(await orders.list_pending_orders(), [])

    async def test_attach_receipt_only_while_pending(self):
        db = FakeSB({"checkout_orders": [pending_order(), pending_order("done", status="approved")]}); self.use(db)
        self.assertTrue(await orders.attach_receipt(OID, "REF9", "texto OCR", {"dup": False}))
        self.assertEqual(db.tables["checkout_orders"][0]["payment_reference"], "REF9")
        self.assertFalse(await orders.attach_receipt("done", "REF9", "x"))
        self.assertIsNone(db.tables["checkout_orders"][1].get("payment_reference"))

    async def test_decide_reject_needs_reason_and_records_actor(self):
        db = FakeSB({"checkout_orders": [pending_order()]}); self.use(db)
        with self.assertRaises(cs.InvalidTransition):
            await orders.decide_order(OID, "rejected", "admin:1")
        self.assertEqual(db.tables["checkout_orders"][0]["status"], "pending")
        self.assertTrue(await orders.decide_order(OID, "rejected", "admin:1", "monto distinto"))
        o = db.tables["checkout_orders"][0]
        self.assertEqual((o["status"], o["decided_by"], o["decision_reason"], o["version"]), ("rejected", "admin:1", "monto distinto", 2))

    async def test_decide_cancel_without_reason_ok(self):
        db = FakeSB({"checkout_orders": [pending_order()]}); self.use(db)
        self.assertTrue(await orders.decide_order(OID, "cancelled", "cliente:5"))

    async def test_decide_invalid_targets_and_states(self):
        db = FakeSB({"checkout_orders": [pending_order(), pending_order("ap", status="approved")]}); self.use(db)
        with self.assertRaises(cs.InvalidTransition):
            await orders.decide_order(OID, "approved", "a", "x")        # aprobar no va por aquí
        with self.assertRaises(cs.InvalidTransition):
            await orders.decide_order("ap", "rejected", "a", "x")        # aprobada es inmutable
        with self.assertRaises(cs.CheckoutError):
            await orders.decide_order("nope", "cancelled", "a")

    async def test_decide_loses_race_returns_false(self):
        db = FakeSB({"checkout_orders": [pending_order()]}, lose_update=True); self.use(db)
        self.assertFalse(await orders.decide_order(OID, "cancelled", "a"))

    async def test_expire_stale_only_pending_and_due(self):
        now = caracas(2026, 12, 2, 12)
        from utils.dates import to_db
        db = FakeSB({"checkout_orders": [
            pending_order("old", expires_at=to_db(now - timedelta(hours=1))),
            pending_order("new", expires_at=to_db(now + timedelta(hours=1))),
            pending_order("ap", status="approved", expires_at=to_db(now - timedelta(hours=5)))]}); self.use(db)
        self.assertEqual(await orders.expire_stale_orders(now), 1)
        st = {o["id"]: o["status"] for o in db.tables["checkout_orders"]}
        self.assertEqual(st, {"old": "expired", "new": "pending", "ap": "approved"})
        self.assertEqual(await orders.expire_stale_orders(now), 0)


class TestApproveOrderRpc(unittest.IsolatedAsyncioTestCase):
    def use(self, db):
        p = mock.patch.object(orders, "get_supabase", lambda: db)
        p.start(); self.addCleanup(p.stop)

    async def test_calls_the_atomic_function_with_exact_params(self):
        ok = {"ok": True, "already_applied": False, "order_id": OID, "items": []}
        db = FakeSB(rpc_result=ok); self.use(db)
        res = await orders.approve_order(OID, "admin:1", "motivo", "REF-1")
        self.assertEqual(res, ok)
        self.assertEqual(db.rpc_calls, [("approve_checkout_order", {
            "p_order_id": OID, "p_actor": "admin:1", "p_reason": "motivo", "p_payment_reference": "REF-1"})])

    async def test_every_function_error_code_is_translated(self):
        for code in cs.APPROVAL_MESSAGES:
            if code in ("unavailable", "unknown"):
                continue
            self.use(FakeSB(rpc_exc=api_error(code, json.dumps({"item": 1}))))
            with self.assertRaises(cs.ApprovalError, msg=code) as cm:
                await orders.approve_order(OID, "a")
            self.assertEqual(cm.exception.code, code)
            self.assertEqual(cm.exception.detail, {"item": 1})
            self.assertEqual(cm.exception.user_message, cs.APPROVAL_MESSAGES[code])

    async def test_unknown_and_missing_function_are_generic(self):
        for exc in (api_error("algo raro"), api_error("Could not find the function public.approve_checkout_order", None, "PGRST202")):
            self.use(FakeSB(rpc_exc=exc))
            with self.assertRaises(cs.ApprovalError) as cm:
                await orders.approve_order(OID, "a")
            self.assertEqual(cm.exception.code, "unknown")

    async def test_bad_detail_json_is_tolerated(self):
        self.use(FakeSB(rpc_exc=api_error("profile_unavailable", "no es json")))
        with self.assertRaises(cs.ApprovalError) as cm:
            await orders.approve_order(OID, "a")
        self.assertEqual((cm.exception.code, cm.exception.detail), ("profile_unavailable", {}))

    async def test_connection_failure_says_retry_is_safe(self):
        self.use(FakeSB(rpc_exc=httpx.ConnectError("sin red")))
        with self.assertRaises(cs.ApprovalError) as cm:
            await orders.approve_order(OID, "a")
        self.assertEqual(cm.exception.code, "unavailable")
        self.assertIn("seguro", cm.exception.user_message)

    async def test_every_message_gives_the_admin_a_way_forward(self):
        for code, msg in cs.APPROVAL_MESSAGES.items():
            self.assertTrue(msg.endswith(".") and len(msg) > 30, code)


class TestServiceApprove(unittest.IsolatedAsyncioTestCase):
    def repo(self, result=None, exc=None):
        r = SimpleNamespace(approve_order=mock.AsyncMock(return_value=result, side_effect=exc))
        return r

    async def test_success_passes_arguments_through(self):
        res = {"ok": True, "already_applied": False, "order_id": OID, "items": []}
        repo = self.repo(res)
        out = await cs.approve(OID, " admin:7 ", "motivo", "REF", repo=repo)
        self.assertEqual(out, res)
        repo.approve_order.assert_awaited_once_with(OID, "admin:7", "motivo", "REF")

    async def test_second_call_is_already_applied(self):
        res = {"ok": True, "already_applied": True, "order_id": OID, "items": []}
        out = await cs.approve(OID, "admin:7", repo=self.repo(res))
        self.assertTrue(out["already_applied"])

    async def test_invalid_input_never_reaches_the_repo(self):
        repo = self.repo({"ok": True})
        with self.assertRaises(cs.CheckoutError):
            await cs.approve("no-es-uuid", "admin:7", repo=repo)
        with self.assertRaises(cs.ApprovalError) as cm:
            await cs.approve(OID, "  ", repo=repo)
        self.assertEqual(cm.exception.code, "actor_required")
        repo.approve_order.assert_not_awaited()

    async def test_repo_errors_propagate_and_odd_results_are_rejected(self):
        with self.assertRaises(cs.ApprovalError) as cm:
            await cs.approve(OID, "a", repo=self.repo(exc=cs.ApprovalError("profile_unavailable")))
        self.assertEqual(cm.exception.code, "profile_unavailable")
        for bad in (None, [], "x", {"ok": False}):
            with self.assertRaises(cs.ApprovalError, msg=repr(bad)) as cm:
                await cs.approve(OID, "a", repo=self.repo(bad))
            self.assertEqual(cm.exception.code, "unknown")

    def test_approval_notices(self):
        result = {"items": [
            {"index": 0, "tipo": "renovacion", "subscription_id": "s1", "profile_id": "p2", "profile_changed": True, "previous_profile_id": "p1"},
            {"index": 1, "tipo": "renovacion", "subscription_id": "s2", "profile_id": "p3", "profile_changed": False},
            {"index": 2, "tipo": "cambio", "profile_changed": True, "released_profile": {"released": True, "profile_name": "P", "account_email": "c@ejemplo.test", "old_pin": "1", "new_pin": "2"}},
            {"index": 3, "tipo": "cambio", "profile_changed": True, "previous_profile_id": "p9", "released_profile": {"released": False, "reason": "otra_suscripcion_activa", "profile_name": "Q"}},
            {"index": 4, "tipo": "nueva", "profile_changed": False}]}
        kinds = [(n["kind"], n["index"]) for n in cs.approval_notices(result)]
        self.assertEqual(kinds, [("profile_changed", 0), ("pin_rotated", 2), ("profile_not_released", 3)])
        self.assertEqual(cs.approval_notices({}), [])


class TestVoidSubscription(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rel = mock.AsyncMock(return_value={"released": True, "old_pin": "1111", "new_pin": "2222"})
        p = mock.patch("database.profiles.release_profile_for_cut", self.rel)
        p.start(); self.addCleanup(p.stop)

    def use(self, db):
        p = mock.patch.object(subs_mod, "get_supabase", lambda: db)
        p.start(); self.addCleanup(p.stop)

    async def test_voids_with_reason_and_releases_profile(self):
        db = FakeSB({"subscriptions": [{"id": "s1", "status": "expired", "profile_id": "p1"}]}); self.use(db)
        out = await subs_mod.void_subscription("s1", "admin:1", "duplicada")
        self.assertTrue(out["voided"] and not out["already"])
        s = db.tables["subscriptions"][0]
        self.assertEqual(s["status"], "voided")
        self.assertEqual(s["void_reason"], "[admin:1] duplicada")
        self.assertTrue(s["voided_at"])
        self.rel.assert_awaited_once_with("p1", exclude_sub_id="s1")
        self.assertEqual(out["release"]["new_pin"], "2222")

    async def test_every_state_can_be_voided_and_no_profile_means_no_release(self):
        for st in ("pending_payment", "active", "expired", "expired_payment", "cancelled"):
            db = FakeSB({"subscriptions": [{"id": "s1", "status": st, "profile_id": None}]}); self.use(db)
            out = await subs_mod.void_subscription("s1", "a", "motivo")
            self.assertTrue(out["voided"], st)
            self.assertIsNone(out["release"])
        self.rel.assert_not_awaited()

    async def test_reason_and_actor_required(self):
        self.use(FakeSB({"subscriptions": [{"id": "s1", "status": "active"}]}))
        for actor, reason in (("a", ""), ("a", "  "), ("", "x"), (None, "x")):
            with self.assertRaises(ValueError):
                await subs_mod.void_subscription("s1", actor, reason)

    async def test_idempotent_not_found_state_change_and_db_error(self):
        self.use(FakeSB({"subscriptions": [{"id": "s1", "status": "voided", "profile_id": "p1"}]}))
        out = await subs_mod.void_subscription("s1", "a", "x")
        self.assertTrue(out["voided"] and out["already"])
        self.rel.assert_not_awaited()
        self.use(FakeSB({"subscriptions": []}))
        self.assertEqual((await subs_mod.void_subscription("s9", "a", "x"))["error"], "not_found")
        self.use(FakeSB({"subscriptions": [{"id": "s1", "status": "active", "profile_id": "p1"}]}, lose_update=True))
        self.assertEqual((await subs_mod.void_subscription("s1", "a", "x"))["error"], "state_changed")
        self.rel.assert_not_awaited()
        self.use(FakeSB(fail=True))
        self.assertEqual((await subs_mod.void_subscription("s1", "a", "x"))["error"], "db_error")

    async def test_shared_profile_is_reported_not_released(self):
        self.rel.return_value = {"released": False, "reason": "otra_suscripcion_activa"}
        self.use(FakeSB({"subscriptions": [{"id": "s1", "status": "expired", "profile_id": "p1"}]}))
        out = await subs_mod.void_subscription("s1", "a", "x")
        self.assertTrue(out["voided"])
        self.assertEqual(out["release"]["reason"], "otra_suscripcion_activa")


if __name__ == "__main__":
    unittest.main()
