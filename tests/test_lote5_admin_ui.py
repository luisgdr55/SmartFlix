"""Pruebas de O02 Lote 5 (interfaz del admin): Telegram, rutas del panel, borrado seguro y plantillas.
Base, Redis, Telegram y motor falsos; datos ficticios."""
from tests import support  # noqa: F401

import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

from bot.handlers import admin_orders
from services import checkout_service as cs
from tests.support import HOSTILE, attribute_names, fake_request

ADMIN = 777
OID = "aaaaaaaa-0000-0000-0000-000000000001"


def cb(data, user=ADMIN):
    q = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=user), effective_message=None), q


def kb_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def summary(out_summary="✅ <b>Orden aprobada</b>", already=False, notified=True):
    return {"order": {}, "result": {"already_applied": already}, "notices": [], "summary": out_summary,
            "client_notified": notified, "client_has_telegram": True}


class TestTelegramOrders(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.log = mock.AsyncMock()
        self.approve = mock.AsyncMock(return_value=summary())
        self.reject = mock.AsyncMock(return_value={"ok": True, "reason": "x", "order": {}, "client_notified": True})
        self.order = {"id": OID, "status": "pending", "users": {"name": "Cliente Ficticio", "telegram_id": 5},
                      "manifest": cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000"),
                      "ocr_flags": {"monto": {"nivel": "warn", "detalle": "no coincide"}}}
        for p in (mock.patch("bot.handlers.admin._check_admin", side_effect=lambda t: t == ADMIN),
                  mock.patch("database.users.log_admin_action", self.log),
                  mock.patch("services.order_admin.approve_and_notify", self.approve),
                  mock.patch("services.order_admin.reject_and_notify", self.reject),
                  mock.patch("services.order_admin._names", new=mock.AsyncMock(return_value={"nf": "🎬 Netflix"})),
                  mock.patch("database.orders.get_order", new=mock.AsyncMock(side_effect=lambda _i: self.order)),
                  mock.patch("database.orders.list_orders", new=mock.AsyncMock(side_effect=lambda st, limit=10: [self.order]))):
            p.start(); self.addCleanup(p.stop)

    async def test_non_admin_is_refused(self):
        upd, q = cb(f"admin:oapprove:{OID}", user=1)
        await admin_orders.handle_order_admin_callback(upd, None)
        self.approve.assert_not_awaited()
        self.assertTrue(q.answer.await_args.kwargs.get("show_alert"))

    async def test_approve_shows_summary_logs_and_offers_exits(self):
        upd, q = cb(f"admin:oapprove:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.approve.assert_awaited_once_with(OID, f"admin:{ADMIN}", None)
        self.assertIn("aprobada", q.edit_message_text.await_args.args[0])
        self.assertIn("admin:pending", kb_data(q.edit_message_text.await_args.kwargs["reply_markup"]))
        self.log.assert_awaited_once()

    async def test_second_tap_is_not_logged_twice(self):
        self.approve.return_value = summary(already=True)
        upd, q = cb(f"admin:oapprove:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.log.assert_not_awaited()

    async def test_late_approval_passes_the_explicit_reason(self):
        upd, q = cb(f"admin:oapprove:{OID}:late")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.assertEqual(self.approve.await_args.args[2], admin_orders.LATE_REASON)

    async def test_every_approval_error_has_a_message_and_a_way_out(self):
        for code in [c for c in cs.APPROVAL_MESSAGES if c not in ("unknown",)] + ["unknown"]:
            self.approve.side_effect = cs.ApprovalError(code)
            upd, q = cb(f"admin:oapprove:{OID}")
            await admin_orders.handle_order_admin_callback(upd, None)
            data = kb_data(q.edit_message_text.await_args.kwargs["reply_markup"])
            self.assertIn("admin:pending", data, code)
            self.assertIn(f"admin:oview:{OID}", data, code)
            if code == "reason_required":
                self.assertIn(f"admin:oapprove:{OID}:late", data)
            if code in ("profile_unavailable", "unavailable"):
                self.assertIn(f"admin:oapprove:{OID}", data)

    async def test_unexpected_error_does_not_leak_details(self):
        self.approve.side_effect = RuntimeError("clave-secreta en el traceback")
        upd, q = cb(f"admin:oapprove:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.assertNotIn("clave-secreta", q.edit_message_text.await_args.args[0])

    async def test_reject_flow_asks_for_the_reason_then_rejects(self):
        upd, q = cb(f"admin:oreject:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        data = kb_data(q.edit_message_text.await_args.kwargs["reply_markup"])
        self.assertIn(f"admin:oreason:{OID}:monto", data)
        self.assertIn(f"admin:oview:{OID}", data)
        self.reject.assert_not_awaited()
        upd, q = cb(f"admin:oreason:{OID}:monto")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.reject.assert_awaited_once_with(OID, f"admin:{ADMIN}", "monto")
        self.assertIn("rechazada", q.edit_message_text.await_args.args[0])
        upd, q = cb(f"admin:oreason:{OID}:monto")
        self.reject.return_value = {"ok": False, "reason": "x", "order": {}, "client_notified": False}
        await admin_orders.handle_order_admin_callback(upd, None)
        self.assertIn("otra persona", q.edit_message_text.await_args.args[0])

    async def test_view_shows_items_signals_and_status(self):
        upd, q = cb(f"admin:oview:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        text = q.edit_message_text.await_args.args[0]
        self.assertIn("ORDEN DE PAGO", text); self.assertIn("Monto", text); self.assertIn("Pendiente", text)
        self.assertEqual(kb_data(q.edit_message_text.await_args.kwargs["reply_markup"]), [f"admin:oapprove:{OID}", f"admin:oreject:{OID}"])
        self.order["status"] = "expired"
        upd, q = cb(f"admin:oview:{OID}")
        await admin_orders.handle_order_admin_callback(upd, None)
        self.assertIn(f"admin:oapprove:{OID}:late", kb_data(q.edit_message_text.await_args.kwargs["reply_markup"]))

    async def test_pending_button_works_from_a_callback_and_lists_orders_with_the_flag(self):
        from bot.handlers import admin as admin_mod, checkout_v2
        msg = SimpleNamespace(reply_text=mock.AsyncMock())
        upd = SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=ADMIN), message=None)
        with mock.patch.object(checkout_v2, "v2_enabled", return_value=True), \
             mock.patch.object(admin_mod, "get_pending_subscriptions", new=mock.AsyncMock(return_value=[])):
            await admin_mod.cmd_pendientes(upd, None)
        self.assertEqual(msg.reply_text.await_count, 1)                 # la orden; sin «no hay pendientes»
        self.assertIn(f"admin:oapprove:{OID}", kb_data(msg.reply_text.await_args.kwargs["reply_markup"]))

    async def test_pending_screen_is_unchanged_with_the_flag_off(self):
        from bot.handlers import admin as admin_mod, checkout_v2
        msg = SimpleNamespace(reply_text=mock.AsyncMock())
        upd = SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=ADMIN), message=None)
        with mock.patch.object(checkout_v2, "v2_enabled", return_value=False), \
             mock.patch.object(admin_mod, "get_pending_subscriptions", new=mock.AsyncMock(return_value=[])):
            await admin_mod.cmd_pendientes(upd, None)
        self.assertIn("No hay pagos pendientes", msg.reply_text.await_args.args[0])


class TestHogarException(unittest.IsolatedAsyncioTestCase):
    async def test_a_photo_during_the_hogar_flow_is_hogar_even_with_a_pending_order(self):
        from bot.handlers import checkout_v2 as v2
        upd = SimpleNamespace(effective_user=SimpleNamespace(id=5), message=SimpleNamespace(photo=(object(),), document=None))
        hogar, legacy = mock.AsyncMock(return_value=True), mock.AsyncMock()
        with mock.patch.object(v2, "handle_order_receipt", new=mock.AsyncMock(return_value=True)) as receipt, \
             mock.patch("bot.handlers.hogar.get_state", return_value="waiting_photo"):
            await v2.route_photo(upd, None, hogar, legacy)
            receipt.assert_not_awaited(); hogar.assert_awaited_once()
            hogar.reset_mock()
        with mock.patch.object(v2, "handle_order_receipt", new=mock.AsyncMock(return_value=True)) as receipt, \
             mock.patch("bot.handlers.hogar.get_state", return_value=None):
            await v2.route_photo(upd, None, hogar, legacy)
            receipt.assert_awaited_once(); hogar.assert_not_awaited()


# ── Panel ────────────────────────────────────────────────────────────────────
def location(resp):
    return unquote(resp.headers["location"])


def query_of(resp):
    return {k: v[0] for k, v in parse_qs(urlparse(resp.headers["location"]).query).items()}


class TestPanelOrderRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from admin_panel import router
        self.r = router
        self.audit = mock.AsyncMock()
        self.approve = mock.AsyncMock(return_value=summary("✅ <b>Orden #AAAAAAAA aprobada</b>"))
        self.reject = mock.AsyncMock(return_value={"ok": True, "order": {}, "client_notified": True, "reason": "x"})
        for p in (mock.patch.object(router, "_auth_guard", return_value=None), mock.patch.object(router, "_audit", self.audit),
                  mock.patch("services.order_admin.approve_and_notify", self.approve),
                  mock.patch("services.order_admin.reject_and_notify", self.reject)):
            p.start(); self.addCleanup(p.stop)

    async def test_approve_uses_the_engine_audits_and_returns_plain_text(self):
        resp = await self.r.order_approve(fake_request(), OID, reason="", payment_reference=" R-1 ")
        self.approve.assert_awaited_once_with(OID, "panel:admin", None, "R-1")
        self.audit.assert_awaited_once()
        self.assertEqual(query_of(resp)["success"], "✅ Orden #AAAAAAAA aprobada")    # sin etiquetas HTML

    async def test_second_approval_is_not_audited_twice(self):
        self.approve.return_value = summary(already=True)
        await self.r.order_approve(fake_request(), OID, reason="", payment_reference="")
        self.audit.assert_not_awaited()

    async def test_approval_error_returns_to_the_order_with_a_human_message(self):
        self.approve.side_effect = cs.ApprovalError("reason_required")
        resp = await self.r.order_approve(fake_request(), OID, reason="", payment_reference="")
        self.assertTrue(location(resp).startswith(f"/panel/orders/{OID}?error="))
        self.assertIn("motivo", query_of(resp)["error"])
        self.audit.assert_not_awaited()

    async def test_unexpected_error_is_public_and_has_no_detail(self):
        self.approve.side_effect = RuntimeError("secreto-interno")
        resp = await self.r.order_approve(fake_request(), OID, reason="", payment_reference="")
        self.assertNotIn("secreto-interno", location(resp))
        self.assertIn("código", query_of(resp)["error"])

    async def test_reject_paths(self):
        resp = await self.r.order_reject(fake_request(), OID, reason_code="monto", detail="x")
        self.reject.assert_awaited_once_with(OID, "panel:admin", "monto", "x")
        self.audit.assert_awaited_once()
        self.assertIn("success", query_of(resp))
        self.reject.return_value = {"ok": False, "order": {}, "client_notified": False, "reason": "x"}
        resp = await self.r.order_reject(fake_request(), OID, reason_code="monto", detail="")
        self.assertIn("otra persona", query_of(resp)["error"])
        self.reject.side_effect = ValueError("Motivo de rechazo no válido")
        resp = await self.r.order_reject(fake_request(), OID, reason_code="inventado", detail="")
        self.assertIn("no válido", query_of(resp)["error"])

    async def test_unauthenticated_requests_are_redirected_to_login(self):
        with mock.patch.object(self.r, "_auth_guard", return_value=SimpleNamespace(status_code=302)) as g:
            out = await self.r.order_approve(fake_request(), OID, reason="", payment_reference="")
        self.assertEqual(out.status_code, 302); self.approve.assert_not_awaited()


class TestPanelAnomalyActions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from admin_panel import router
        self.r = router
        self.audit, self.notify = mock.AsyncMock(), mock.AsyncMock()
        self.void = mock.AsyncMock(return_value={"voided": True, "already": False, "release": {"released": True, "profile_name": "P", "old_pin": "1", "new_pin": "2", "account_email": "c@ejemplo.test"}})
        self.release = mock.AsyncMock(return_value={"released": True, "profile_name": "P", "old_pin": "1", "new_pin": "2", "account_email": "c@ejemplo.test"})
        for p in (mock.patch.object(router, "_auth_guard", return_value=None), mock.patch.object(router, "_audit", self.audit),
                  mock.patch.object(router, "_notify_release", self.notify),
                  mock.patch("database.subscriptions.void_subscription", self.void),
                  mock.patch("database.profiles.release_profile_for_cut", self.release)):
            p.start(); self.addCleanup(p.stop)

    async def test_void_needs_a_reason_and_is_audited(self):
        resp = await self.r.anomaly_void(fake_request(), sub_id="s1", reason="  ab ")
        self.assertIn("motivo", query_of(resp)["error"]); self.void.assert_not_awaited()
        resp = await self.r.anomaly_void(fake_request(), sub_id="s1", reason="duplicada de renovación")
        self.void.assert_awaited_once_with("s1", "panel", "duplicada de renovación")
        self.audit.assert_awaited_once(); self.notify.assert_awaited_once()
        self.assertIn("success", query_of(resp))

    async def test_void_failure_and_shared_profile_note(self):
        self.void.return_value = {"voided": False, "already": False, "release": None, "error": "not_found"}
        resp = await self.r.anomaly_void(fake_request(), sub_id="s1", reason="motivo válido")
        self.assertIn("error", query_of(resp)); self.audit.assert_not_awaited()
        self.void.return_value = {"voided": True, "already": False, "release": {"released": False, "reason": "otra_suscripcion_activa"}}
        resp = await self.r.anomaly_void(fake_request(), sub_id="s1", reason="motivo válido")
        self.assertIn("NO se liberó", query_of(resp)["success"])

    async def test_release_profile_rules(self):
        resp = await self.r.anomaly_release_profile(fake_request(), profile_id="p1", reason="huérfano confirmado")
        self.release.assert_awaited_once_with("p1", None); self.audit.assert_awaited_once()
        self.assertIn("success", query_of(resp))
        for info, word in (({"released": False, "reason": "otra_suscripcion_activa"}, "anúlala"), ({"released": False, "reason": "no_ocupado"}, "ocupado"), (None, "leer")):
            self.release.return_value = info
            resp = await self.r.anomaly_release_profile(fake_request(), profile_id="p1", reason="motivo válido")
            self.assertIn(word, query_of(resp)["error"])

    async def test_merge_validations(self):
        rows = [{"id": "a", "user_id": "u1", "platform_id": "nf", "status": "active"}, {"id": "b", "user_id": "u1", "platform_id": "nf", "status": "expired"},
                {"id": "c", "user_id": "u2", "platform_id": "nf", "status": "active"}]
        sb = mock.Mock()
        sb.table.return_value.select.return_value.in_.return_value.execute.side_effect = lambda: SimpleNamespace(data=[r for r in rows if r["id"] in self.ids])
        def in_(col, ids):
            self.ids = ids
            return sb.table.return_value.select.return_value.in_.return_value
        sb.table.return_value.select.return_value.in_.side_effect = in_
        with mock.patch("database.get_supabase", return_value=sb):
            for keep, void, why in (("a", "a", "iguales"), ("a", "c", "otro cliente")):
                resp = await self.r.anomaly_merge(fake_request(), keep_id=keep, void_id=void, reason="motivo válido")
                self.assertIn("error", query_of(resp), why)
            self.void.assert_not_awaited()
            resp = await self.r.anomaly_merge(fake_request(), keep_id="a", void_id="b", reason="renovación duplicada")
        self.void.assert_awaited_once()
        self.assertIn("fusionada con a", self.void.await_args.args[2])
        self.assertIn("success", query_of(resp)); self.audit.assert_awaited_once()


class TestSafeDelete(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from admin_panel import router
        self.r = router
        self.delete_sub = mock.AsyncMock(return_value=True)
        self.delete_user = mock.AsyncMock(return_value=True)
        sb = mock.Mock()
        sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[{"user_id": "u1"}])
        for p in (mock.patch.object(router, "_auth_guard", return_value=None), mock.patch("database.get_supabase", return_value=sb),
                  mock.patch("database.subscriptions.delete_subscription", self.delete_sub),
                  mock.patch("database.users.delete_user", self.delete_user)):
            p.start(); self.addCleanup(p.stop)

    async def test_subscription_with_payment_history_is_not_deleted(self):
        with mock.patch("database.orders.count_subscription_periods", new=mock.AsyncMock(return_value=2)):
            resp = await self.r.subscription_delete(fake_request(), "s1")
        self.delete_sub.assert_not_awaited()
        self.assertIn("historial de pagos", query_of(resp)["error"])

    async def test_subscription_without_periods_is_deleted_as_before(self):
        with mock.patch("database.orders.count_subscription_periods", new=mock.AsyncMock(return_value=0)):
            resp = await self.r.subscription_delete(fake_request(), "s1")
        self.delete_sub.assert_awaited_once_with("s1")
        self.assertIn("success", query_of(resp))

    async def test_user_with_history_or_failed_delete_never_reports_success(self):
        with mock.patch("database.orders.count_user_periods", new=mock.AsyncMock(return_value=3)):
            resp = await self.r.user_delete(fake_request(), "u1")
        self.delete_user.assert_not_awaited(); self.assertIn("error", query_of(resp))
        self.delete_user.return_value = False
        with mock.patch("database.orders.count_user_periods", new=mock.AsyncMock(return_value=0)):
            resp = await self.r.user_delete(fake_request(), "u1")
        self.assertIn("error", query_of(resp)); self.assertNotIn("success", query_of(resp))
        self.delete_user.return_value = True
        with mock.patch("database.orders.count_user_periods", new=mock.AsyncMock(return_value=0)):
            resp = await self.r.user_delete(fake_request(), "u1")
        self.assertIn("success", query_of(resp))

    async def test_period_count_tolerates_a_missing_table_but_not_other_errors(self):
        from database import orders
        def sb_raising(msg):
            sb = mock.Mock()
            sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = RuntimeError(msg)
            return sb
        with mock.patch.object(orders, "get_supabase", return_value=sb_raising('relation "subscription_periods" does not exist')):
            self.assertEqual(await orders.count_subscription_periods("s1"), 0)     # migración aún sin ejecutar
        with mock.patch.object(orders, "get_supabase", return_value=sb_raising("timeout de red")):
            self.assertEqual(await orders.count_subscription_periods("s1"), 1)     # ante la duda, no se borra
        ok = mock.Mock()
        ok.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(count=4, data=[])
        with mock.patch.object(orders, "get_supabase", return_value=ok):
            self.assertEqual(await orders.count_subscription_periods("s1"), 4)


class TestTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from admin_panel.router import templates
        cls.env = templates.env

    def render(self, name, **ctx):
        base = dict(request=fake_request(), page="x", success=None, error=None)
        base.update(ctx)
        return self.env.get_template(name).render(**base)

    def view(self, status="pending", **kw):
        from services.order_notifications import order_view
        m = cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000")
        o = {"id": OID, "status": status, "manifest": m, "users": {"name": HOSTILE, "username": HOSTILE, "telegram_id": 5},
             "created_at": "2026-12-01T14:00:00+00:00", "payment_reference": "R1", "receipt_text": "OCR",
             "ocr_flags": {"alertas": ["a"], "monto": {"nivel": "warn", "detalle": "no coincide", "leido": "1", "esperado": "5000"}}, **kw}
        return order_view(o, {"nf": HOSTILE})

    def assertSafe(self, html):
        names = attribute_names(html)
        for bad in ("x-init", "onerror", "z"):
            self.assertNotIn(bad, names)
        self.assertNotIn("<img src=x", html)

    def test_orders_list_is_safe_and_human(self):
        html = self.render("orders.html", pending=[self.view()], recent=[self.view("approved")], expired_count=1)
        self.assertSafe(html)
        self.assertIn("Pendiente", html); self.assertIn("Aprobada", html)
        self.assertNotIn("pending_payment", html)
        self.assertIn("señal(es) a revisar", html)

    def test_order_detail_forms_have_csrf_and_confirm(self):
        from services.order_notifications import REJECT_REASONS
        html = self.render("order_detail.html", o=self.view(), reject_reasons=REJECT_REASONS)
        self.assertSafe(html)
        self.assertEqual(html.count('name="csrf_token" value="tok-test"'), html.count("<form"))   # todos los formularios POST llevan token
        self.assertIn("/approve", html); self.assertIn("/reject", html); self.assertIn("confirm(", html)
        self.assertIn("Monto", html)

    def test_expired_order_requires_a_reason_and_has_no_reject_form(self):
        from services.order_notifications import REJECT_REASONS
        html = self.render("order_detail.html", o=self.view("expired"), reject_reasons=REJECT_REASONS)
        self.assertIn("obligatorio: la orden caducó", html); self.assertIn("required", html)
        self.assertNotIn("/reject", html)
        done = self.render("order_detail.html", o=self.view("approved"), reject_reasons=REJECT_REASONS)
        self.assertNotIn("/approve", done)                              # una aprobada no se vuelve a decidir

    def test_anomalies_page_is_safe_has_csrf_and_requires_reasons(self):
        from services import anomalies
        sub = {"id": "s-1", "short": "s-1", "status": "expired_payment", "end_date": "2026-12-01T03:59:59+00:00", "platform": HOSTILE,
               "user": HOSTILE, "days_overdue": 0, "has_receipt": True, "age_days": 3}
        data = {"counts": {}, "total": 3, "duplicates": [{"subs": [{**sub, "id": "a", "short": "a", "status": "active"}, {**sub, "id": "b", "short": "b"}],
                "same_profile": True, "platform": HOSTILE, "user": HOSTILE}], "shared_profiles": [],
                "available_with_active": [], "overdue_occupied": [], "expired_payment": [sub], "pending_orphans": [],
                "orphan_profiles": [{"profile_id": "p1", "profile": HOSTILE, "platform": HOSTILE, "kind": "sin_suscripcion"}]}
        html = self.render("anomalies.html", ok=True, data=data, categories=anomalies.CATEGORIES, load_error=None)
        self.assertSafe(html)
        self.assertEqual(html.count('name="csrf_token" value="tok-test"'), html.count("<form"))   # ningún formulario POST sin token
        self.assertGreaterEqual(html.count("<form"), 4)
        self.assertIn("Pedido caducado sin pago", html)                  # etiqueta humana, no el estado crudo
        self.assertNotIn(">expired_payment<", html)
        self.assertIn("required", html); self.assertIn("/anomalies/merge", html); self.assertIn("/anomalies/release-profile", html)

    def test_anomalies_page_says_when_the_data_could_not_be_read(self):
        from services import anomalies
        html = self.render("anomalies.html", ok=False, data={"counts": {}, "total": 0}, categories=anomalies.CATEGORIES,
                           load_error="Error de prueba")
        self.assertIn("NO significa que no haya anomalías", html)

    def test_status_label_filter_in_the_existing_pages(self):
        self.assertEqual(self.env.filters["status_label"]("voided"), "Anulada")
        self.assertEqual(self.env.filters["status_label"]("expired_payment"), "Pedido caducado sin pago")


if __name__ == "__main__":
    unittest.main()


class TestPanelEndToEnd(unittest.TestCase):
    """Rutas nuevas con el panel real (login, CSRF, plantillas): base y motor falsos."""

    @classmethod
    def setUpClass(cls):
        import main
        from starlette.testclient import TestClient
        cls.main, cls.TestClient = main, TestClient

    def setUp(self):
        from admin_panel import security as sec
        from tests.support import PANEL_PASSWORD, login_csrf_from
        sec._throttle = sec.LoginThrottle(sec._MemoryStore())     # sin Redis y sin estado compartido
        self.client = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        page = self.client.get("/panel/login")
        self.client.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": login_csrf_from(page.text)})
        from admin_panel.security import csrf_token_for
        self.token = csrf_token_for(self.client.cookies.get("sv_admin_session"))
        order = {"id": OID, "status": "pending", "users": {"name": HOSTILE, "telegram_id": 5}, "created_at": "2026-12-01T14:00:00+00:00",
                 "manifest": cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000"), "ocr_flags": {"alertas": ["a"]}}
        self.approve = mock.AsyncMock(return_value=summary())
        for p in (mock.patch("database.orders.list_orders", new=mock.AsyncMock(return_value=[order])),
                  mock.patch("database.orders.get_order", new=mock.AsyncMock(return_value=order)),
                  mock.patch("services.order_admin._names", new=mock.AsyncMock(return_value={"nf": HOSTILE})),
                  mock.patch("services.order_admin.approve_and_notify", self.approve),
                  mock.patch("database.users.log_admin_action", new=mock.AsyncMock(return_value=True)),
                  mock.patch("database.anomalies.fetch_anomaly_data", new=mock.AsyncMock(return_value=([], [])))):
            p.start(); self.addCleanup(p.stop)

    def test_pages_require_login(self):
        anon = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        for path in ("/panel/orders", f"/panel/orders/{OID}", "/panel/anomalies"):
            r = anon.get(path)
            self.assertEqual((r.status_code, r.headers["location"]), (302, "/panel/login"), path)

    def test_pages_render(self):
        r = self.client.get("/panel/orders")
        self.assertEqual(r.status_code, 200); self.assertIn("Órdenes pendientes", r.text); self.assertNotIn("<img src=x", r.text)
        r = self.client.get(f"/panel/orders/{OID}")
        self.assertEqual(r.status_code, 200); self.assertIn("Aprobar orden", r.text)
        r = self.client.get("/panel/anomalies")
        self.assertEqual(r.status_code, 200); self.assertIn("Sin hallazgos", r.text)
        self.assertEqual(self.client.get("/panel/orders/no-es-uuid").status_code, 302)

    def test_post_routes_reject_a_missing_csrf_token_and_accept_a_valid_one(self):
        for path, data in ((f"/panel/orders/{OID}/approve", {}), (f"/panel/orders/{OID}/reject", {"reason_code": "otro"}),
                           ("/panel/anomalies/void", {"sub_id": "s", "reason": "motivo válido"}),
                           ("/panel/anomalies/release-profile", {"profile_id": "p", "reason": "motivo válido"}),
                           ("/panel/anomalies/merge", {"keep_id": "a", "void_id": "b", "reason": "motivo válido"})):
            self.assertEqual(self.client.post(path, data=data).status_code, 403, path)
        self.approve.assert_not_awaited()
        r = self.client.post(f"/panel/orders/{OID}/approve", data={"csrf_token": self.token, "reason": "", "payment_reference": ""})
        self.assertEqual(r.status_code, 302); self.approve.assert_awaited_once()
        self.assertTrue(r.headers["location"].startswith("/panel/orders?success="))
