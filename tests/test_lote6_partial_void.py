"""Pruebas de la aprobación parcial (dividir la orden) y de anular una orden aprobada con asiento
contrario (recomendaciones del Lote 5 aplicadas): motor, orquestación, textos, panel y plantillas."""
from tests import support  # noqa: F401

import json
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from services import checkout_service as cs
from services import order_admin
from services import order_notifications as notes
from tests.support import HOSTILE, attribute_names, fake_request
from tests.test_checkout_engine import FakeSB, api_error
from tests.test_lote5_services import item_result, sub_row

OID = "aaaaaaaa-0000-0000-0000-000000000001"
CHILD = "bbbbbbbb-0000-0000-0000-000000000002"


class TestEngine(unittest.IsolatedAsyncioTestCase):
    def repo(self, result=None, exc=None):
        return SimpleNamespace(approve_order_partial=mock.AsyncMock(return_value=result, side_effect=exc),
                               void_order=mock.AsyncMock(return_value=result, side_effect=exc))

    async def test_partial_validates_the_selection_before_calling_the_function(self):
        repo = self.repo({"ok": True})
        for bad in ([], None, [0, 0], [-1], [True], ["1"], 3, [1.5]):
            with self.assertRaises(cs.ApprovalError, msg=repr(bad)) as cm:
                await cs.approve_partial(OID, "admin:1", bad, repo=repo)
            self.assertEqual(cm.exception.code, "invalid_selection")
        with self.assertRaises(cs.ApprovalError) as cm:
            await cs.approve_partial(OID, " ", [0], repo=repo)
        self.assertEqual(cm.exception.code, "actor_required")
        repo.approve_order_partial.assert_not_awaited()

    async def test_partial_passes_sorted_indexes_and_returns_the_remainder(self):
        res = {"ok": True, "already_applied": False, "partial": True, "remainder_order_id": CHILD, "items": []}
        repo = self.repo(res)
        out = await cs.approve_partial(OID, " admin:1 ", [2, 0], "motivo", "REF", repo=repo)
        self.assertEqual(out["remainder_order_id"], CHILD)
        repo.approve_order_partial.assert_awaited_once_with(OID, "admin:1", [0, 2], "motivo", "REF")
        with self.assertRaises(cs.ApprovalError):
            await cs.approve_partial(OID, "a", [0], repo=self.repo({"ok": False}))

    async def test_void_requires_actor_and_reason_and_validates_the_answer(self):
        repo = self.repo({"ok": True, "items": []})
        for actor, reason, code in (("", "x", "actor_required"), ("a", "  ", "reason_required"), ("a", None, "reason_required")):
            with self.assertRaises(cs.ApprovalError) as cm:
                await cs.void_order(OID, actor, reason, repo=repo)
            self.assertEqual(cm.exception.code, code)
        repo.void_order.assert_not_awaited()
        self.assertTrue((await cs.void_order(OID, "admin:1", " error ", repo=repo))["ok"])
        repo.void_order.assert_awaited_once_with(OID, "admin:1", "error")
        with self.assertRaises(cs.ApprovalError):
            await cs.void_order(OID, "a", "x", repo=self.repo(None))

    async def test_repository_calls_the_right_sql_functions_with_exact_params(self):
        from database import orders
        db = FakeSB(rpc_result={"ok": True})
        with mock.patch.object(orders, "get_supabase", lambda: db):
            await orders.approve_order_partial(OID, "admin:1", [0, 2], "m", "R")
            await orders.void_order(OID, "admin:1", "error")
        self.assertEqual(db.rpc_calls, [
            ("split_and_approve_checkout_order", {"p_order_id": OID, "p_actor": "admin:1", "p_keep": [0, 2], "p_reason": "m", "p_payment_reference": "R"}),
            ("void_checkout_order", {"p_order_id": OID, "p_actor": "admin:1", "p_reason": "error"})])

    async def test_new_error_codes_are_translated_with_a_way_forward(self):
        from database import orders
        for code in ("invalid_selection", "manual_reversal_required", "subscription_changed"):
            with mock.patch.object(orders, "get_supabase", lambda c=code: FakeSB(rpc_exc=api_error(c, json.dumps({"x": 1})))):
                with self.assertRaises(cs.ApprovalError) as cm:
                    await orders.void_order(OID, "a", "x")
            self.assertEqual(cm.exception.code, code)
            self.assertIn("No se cambió nada", cm.exception.user_message)


class TestOrchestration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parent = {"id": OID, "status": "approved", "users": {"telegram_id": 5, "name": "Cliente Ficticio"},
                       "manifest": cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000")}
        self.child = {"id": CHILD, "status": "pending", "parent_order_id": OID,
                      "manifest": cs.build_manifest([cs.build_new_item("hbo", "monthly", "5")], "1000")}
        self.result = {"ok": True, "already_applied": False, "partial": True, "order_id": OID, "remainder_order_id": CHILD,
                       "remainder_items": 1, "remainder_total_usd": 5, "remainder_total_bs": 5000,
                       "items": [item_result(0, "nueva", "s1")]}
        self.sent = mock.AsyncMock(return_value=True)
        self.partial = mock.AsyncMock(side_effect=lambda *a, **k: self.result)
        self.void = mock.AsyncMock(return_value={"ok": True, "already_applied": False, "voided": True, "items": [
            {"tipo": "renovacion", "action": "end_date_restored", "restored_end_date": "2026-06-20T03:59:59+00:00", "subscription_id": "s1"},
            {"tipo": "nueva", "action": "voided", "subscription_id": "s2", "released_profile": {"released": True, "profile_name": "P<1>", "account_email": "c@ejemplo.test", "old_pin": "1", "new_pin": "2"}}]})
        self.fetches = 0

        async def get_order(oid):
            return self.child if oid == CHILD else self.parent
        for p in (mock.patch("database.orders.get_order", new=mock.AsyncMock(side_effect=get_order)),
                  mock.patch("database.orders.get_subscriptions_with_access", new=mock.AsyncMock(return_value={"s1": sub_row("s1")})),
                  mock.patch("database.platforms.get_platform_by_id", new=mock.AsyncMock(return_value={"name": "Netflix", "icon_emoji": "🎬"})),
                  mock.patch("database.users.increment_user_purchases", new=mock.AsyncMock()),
                  mock.patch("services.notification_service.send_to_user", self.sent),
                  mock.patch.object(cs, "approve_partial", self.partial), mock.patch.object(cs, "void_order", self.void)):
            p.start(); self.addCleanup(p.stop)

    async def test_partial_notifies_the_approved_part_and_the_remainder(self):
        out = await order_admin.approve_partial_and_notify(OID, "admin:1", [0], None, None)
        self.partial.assert_awaited_once_with(OID, "admin:1", [0], None, None)
        self.assertEqual(out["remainder_order_id"], CHILD)
        self.assertIn("Orden dividida", out["summary"])
        self.assertIn("#BBBBBBBB", out["summary"])
        texts = [c.args[1] for c in self.sent.await_args_list]
        self.assertEqual(len(texts), 3)                              # confirmación + acceso + aviso de lo pendiente
        self.assertIn("Aprobamos una parte", texts[2])
        self.assertIn("5000", texts[2])
        self.assertTrue(self.sent.await_args_list[2].kwargs["keyboard"].inline_keyboard)

    async def test_partial_already_applied_does_not_message_again_and_errors_propagate(self):
        self.result = {**self.result, "already_applied": True}
        await order_admin.approve_partial_and_notify(OID, "admin:1", [0])
        self.sent.assert_not_awaited()
        self.partial.side_effect = cs.ApprovalError("invalid_selection")
        with self.assertRaises(cs.ApprovalError):
            await order_admin.approve_partial_and_notify(OID, "admin:1", [0, 1])

    async def test_void_tells_the_admin_what_to_do_and_the_client_generically(self):
        out = await order_admin.void_and_notify(OID, "admin:1", "pago duplicado interno")
        self.void.assert_awaited_once_with(OID, "admin:1", "pago duplicado interno")
        self.assertIn("vuelve al 19/06/2026", out["summary"])
        self.assertIn("P&lt;1&gt;", out["summary"])
        self.assertIn("<code>1</code>", out["summary"])
        text = self.sent.await_args.args[1]
        self.assertIn("anulado", text)
        self.assertNotIn("pago duplicado interno", text)              # el motivo interno no se le muestra al cliente
        self.assertTrue(self.sent.await_args.kwargs["keyboard"].inline_keyboard)

    async def test_void_errors_and_repeat(self):
        self.void.side_effect = cs.ApprovalError("manual_reversal_required")
        with self.assertRaises(cs.ApprovalError) as cm:
            await order_admin.void_and_notify(OID, "admin:1", "x")
        self.assertEqual(cm.exception.code, "manual_reversal_required")
        self.sent.assert_not_awaited()
        self.void.side_effect = None
        self.void.return_value = {"ok": True, "already_applied": True, "items": []}
        out = await order_admin.void_and_notify(OID, "admin:1", "x")
        self.assertIn("ya estaba anulada", out["summary"])
        self.sent.assert_not_awaited()

    def test_order_view_fields_for_the_panel(self):
        v = notes.order_view({**self.child, "users": {"name": "A"}}, {"hbo": "H"})
        self.assertEqual((v["parent_id"], v["parent_short"], v["has_change_item"]), (OID, "AAAAAAAA", False))
        self.assertEqual(v["items"][0]["index"], 0)
        chg = cs.build_change_item({"id": "s", "status": "expired", "end_date": "2026-01-01T00:00:00+00:00", "platform_id": "a"}, "b", "monthly", "5", "5")
        o = {"id": OID, "manifest": cs.build_manifest([chg], "1000"), "users": {}}
        self.assertTrue(notes.order_view(o, {})["has_change_item"])


def query_of(resp):
    return {k: v[0] for k, v in parse_qs(urlparse(resp.headers["location"]).query).items()}


class TestPanelRoutesAndTemplates(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from admin_panel import router
        self.r = router
        self.audit = mock.AsyncMock()
        self.partial = mock.AsyncMock(return_value={"order": {}, "result": {"already_applied": False}, "summary": "✂️ <b>Orden dividida</b>",
                                                    "remainder_order_id": CHILD, "client_notified": True})
        self.void = mock.AsyncMock(return_value={"order": {}, "result": {"already_applied": False, "items": [{"released_profile": {"released": True}}]},
                                                 "summary": "↩️ <b>Orden anulada</b>", "client_notified": True})
        self.notify = mock.AsyncMock()
        for p in (mock.patch.object(router, "_auth_guard", return_value=None), mock.patch.object(router, "_audit", self.audit),
                  mock.patch.object(router, "_notify_release", self.notify),
                  mock.patch("services.order_admin.approve_partial_and_notify", self.partial),
                  mock.patch("services.order_admin.void_and_notify", self.void)):
            p.start(); self.addCleanup(p.stop)

    async def test_partial_route(self):
        resp = await self.r.order_approve_partial(fake_request(), OID, items=[2, 0], reason=" ", payment_reference="")
        self.partial.assert_awaited_once_with(OID, "panel:admin", [2, 0], None, None)
        self.audit.assert_awaited_once()
        self.assertEqual(query_of(resp)["success"], "✂️ Orden dividida")
        self.partial.side_effect = cs.ApprovalError("invalid_selection")
        resp = await self.r.order_approve_partial(fake_request(), OID, items=[], reason="", payment_reference="")
        self.assertIn("selección", query_of(resp)["error"])

    async def test_void_route_requires_a_reason_audits_and_notifies_the_pin(self):
        resp = await self.r.order_void(fake_request(), OID, reason="  no ")
        self.assertIn("motivo", query_of(resp)["error"]); self.void.assert_not_awaited()
        resp = await self.r.order_void(fake_request(), OID, reason="aprobada por error")
        self.void.assert_awaited_once_with(OID, "panel:admin", "aprobada por error")
        self.audit.assert_awaited_once(); self.notify.assert_awaited_once()
        self.assertIn("success", query_of(resp))
        self.void.side_effect = cs.ApprovalError("manual_reversal_required")
        resp = await self.r.order_void(fake_request(), OID, reason="aprobada por error")
        self.assertIn("cambio de plataforma", query_of(resp)["error"])

    def render(self, **ctx):
        from admin_panel.router import templates
        from services.order_notifications import REJECT_REASONS
        return templates.env.get_template("order_detail.html").render(request=fake_request(), page="x", success=None, error=None,
                                                                      reject_reasons=REJECT_REASONS, **ctx)

    def view(self, status, n_items=2, extra=None):
        items = [cs.build_new_item("nf", "monthly", "5") for _ in range(n_items)]
        o = {"id": OID, "status": status, "manifest": cs.build_manifest(items, "1000"), "users": {"name": HOSTILE, "telegram_id": 5},
             "created_at": "2026-12-01T14:00:00+00:00", **(extra or {})}
        return notes.order_view(o, {"nf": HOSTILE})

    def assertSafe(self, html):
        self.assertNotIn("x-init", attribute_names(html)); self.assertNotIn("<img src=x", html)

    def test_partial_form_only_for_pending_or_expired_orders_with_several_items(self):
        html = self.render(o=self.view("pending"))
        self.assertSafe(html)
        self.assertIn("/approve-partial", html)
        self.assertEqual(html.count('name="items"'), 2)
        self.assertEqual(html.count('name="csrf_token" value="tok-test"'), html.count("<form"))
        self.assertNotIn("/approve-partial", self.render(o=self.view("pending", n_items=1)))
        self.assertNotIn("/approve-partial", self.render(o=self.view("approved")))
        exp = self.render(o=self.view("expired"))
        self.assertIn("/approve-partial", exp); self.assertIn("obligatorio: la orden caducó", exp)

    def test_void_form_only_for_approved_orders_and_warns_about_platform_changes(self):
        html = self.render(o=self.view("approved"))
        self.assertIn("/void", html); self.assertIn("required", html)
        self.assertEqual(html.count('name="csrf_token" value="tok-test"'), html.count("<form"))
        self.assertNotIn("/void", self.render(o=self.view("pending")))
        chg = cs.build_change_item({"id": "s", "status": "expired", "end_date": "2026-01-01T00:00:00+00:00", "platform_id": "a"}, "b", "monthly", "5", "5")
        o = {"id": OID, "status": "approved", "manifest": cs.build_manifest([chg], "1000"), "users": {"name": "A"}, "created_at": "2026-12-01T14:00:00+00:00"}
        self.assertIn("cambio de plataforma", self.render(o=notes.order_view(o, {})))

    def test_child_order_links_to_its_parent(self):
        html = self.render(o=self.view("pending", extra={"parent_order_id": "cccccccc-0000-0000-0000-000000000003"}))
        self.assertIn("/panel/orders/cccccccc-0000-0000-0000-000000000003", html)
        self.assertIn("aprobación parcial", html)


class TestNewRoutesNeedCsrf(unittest.TestCase):
    def test_post_without_token_is_forbidden_on_the_real_panel(self):
        import main
        from admin_panel import security as sec
        from starlette.testclient import TestClient
        from tests.support import PANEL_PASSWORD, login_csrf_from
        sec._throttle = sec.LoginThrottle(sec._MemoryStore())
        c = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
        c.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": login_csrf_from(c.get("/panel/login").text)})
        for path in (f"/panel/orders/{OID}/approve-partial", f"/panel/orders/{OID}/void"):
            self.assertEqual(c.post(path, data={"items": "0", "reason": "motivo válido"}).status_code, 403, path)


if __name__ == "__main__":
    unittest.main()
