"""Pruebas de O02 Lote 5 (servicios): etiquetas, detector de anomalías, textos de aprobación y
orquestación de aprobar/rechazar con aviso al cliente. Datos ficticios."""
from tests import support  # noqa: F401

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from services import anomalies, labels
from services import checkout_service as cs
from services import order_admin
from services import order_notifications as notes
from tests.support import HOSTILE
from tests.test_checkout_service import caracas

NOW = caracas(2026, 12, 10, 12)
OID = "aaaaaaaa-0000-0000-0000-000000000001"


def iso(dt):
    return dt if isinstance(dt, str) else dt.isoformat()


class TestLabels(unittest.TestCase):
    def test_human_labels_and_fallback(self):
        self.assertEqual(labels.subscription_label("expired_payment"), "Pedido caducado sin pago")
        self.assertEqual(labels.subscription_label("voided"), "Anulada")
        self.assertEqual(labels.subscription_label("raro"), "raro")
        self.assertEqual(labels.subscription_label(None), "Sin estado")
        self.assertEqual(labels.order_label("approved"), "Aprobada")
        self.assertEqual(labels.order_label("cancelled"), "Cancelada por el cliente")

    def test_overdue_is_derived_not_a_state(self):
        late = {"status": "active", "end_date": iso(NOW - timedelta(days=3))}
        self.assertEqual(labels.subscription_display(late, NOW), ("En mora (3 días)", "badge-expired"))
        legacy = {"status": "expired", "end_date": iso(NOW - timedelta(days=1))}
        self.assertEqual(labels.subscription_display(legacy, NOW)[0], "En mora (1 día)")
        ok = {"status": "active", "end_date": iso(NOW + timedelta(days=3))}
        self.assertEqual(labels.subscription_display(ok, NOW), ("Activa", "badge-active"))
        self.assertEqual(labels.subscription_display({"status": "cancelled", "end_date": iso(NOW - timedelta(days=9))}, NOW)[0], "Cancelada")


def S(sid, user="u1", plat="nf", prof=None, status="active", end=None, created=None, ref=None, **kw):
    return {"id": sid, "user_id": user, "platform_id": plat, "profile_id": prof, "status": status, "plan_type": "monthly",
            "end_date": iso(end or NOW + timedelta(days=10)), "created_at": iso(created or NOW - timedelta(days=1)),
            "payment_reference": ref, "users": {"name": "Cliente Ficticio"}, "platforms": {"name": "Netflix"}, **kw}


def P(pid, status="occupied", name="P"):
    return {"id": pid, "profile_name": name, "status": status, "platforms": {"name": "Netflix"}}


class TestAnomalies(unittest.TestCase):
    def test_healthy_data_has_no_findings(self):
        d = anomalies.detect([S("s1", prof="p1")], [P("p1")], NOW)
        self.assertEqual(d["total"], 0)

    def test_duplicates_and_same_profile(self):
        d = anomalies.detect([S("a", prof="p1"), S("b", prof="p1"), S("c", user="u2", prof="p2")], [P("p1"), P("p2")], NOW)
        self.assertEqual(len(d["duplicates"]), 1)
        self.assertTrue(d["duplicates"][0]["same_profile"])
        self.assertEqual(len(d["shared_profiles"]), 1)
        d2 = anomalies.detect([S("a", prof="p1"), S("b", prof="p2")], [P("p1"), P("p2")], NOW)
        self.assertFalse(d2["duplicates"][0]["same_profile"])      # dos perfiles: puede ser legítimo
        self.assertEqual(d2["shared_profiles"], [])

    def test_active_sub_with_free_profile(self):
        d = anomalies.detect([S("a", prof="p1")], [P("p1", "available")], NOW)
        self.assertEqual(len(d["available_with_active"]), 1)

    def test_overdue_with_occupied_profile_counts_both_states_but_not_free_profiles(self):
        subs = [S("a", prof="p1", end=NOW - timedelta(days=9)), S("b", user="u2", prof="p2", status="expired", end=NOW - timedelta(days=2)),
                S("c", user="u3", prof="p3", status="expired", end=NOW - timedelta(days=2))]
        d = anomalies.detect(subs, [P("p1"), P("p2"), P("p3", "available")], NOW)
        self.assertEqual({r["short"] for r in d["overdue_occupied"]}, {"a", "b"})
        self.assertEqual(d["overdue_occupied"][0]["days_overdue"], 9)

    def test_orphan_profiles_and_their_kinds(self):
        subs = [S("x", prof="p2", status="expired", end=NOW - timedelta(days=1))]
        d = anomalies.detect(subs, [P("p1"), P("p2"), P("p3", "available")], NOW)
        orphans = {o["profile_id"]: o["kind"] for o in d["orphan_profiles"]}
        self.assertEqual(orphans, {"p1": "sin_suscripcion"})        # p2 ya aparece como mora; p3 está libre

    def test_expired_payment_age_and_receipt_flag(self):
        d = anomalies.detect([S("a", status="expired_payment", created=NOW - timedelta(days=5), ref="REF1"),
                              S("b", user="u2", status="expired_payment", created=NOW - timedelta(days=1))], [], NOW)
        by = {r["short"]: r for r in d["expired_payment"]}
        self.assertEqual((by["a"]["age_days"], by["a"]["has_receipt"]), (5, True))
        self.assertFalse(by["b"]["has_receipt"])

    def test_old_pending_only(self):
        d = anomalies.detect([S("old", status="pending_payment", created=NOW - timedelta(hours=9)),
                              S("new", user="u2", status="pending_payment", created=NOW - timedelta(hours=1))], [], NOW)
        self.assertEqual([r["short"] for r in d["pending_orphans"]], ["old"])

    def test_counts_total_and_bad_dates_do_not_crash(self):
        d = anomalies.detect([S("a", status="pending_payment", created="no-fecha")], [], NOW)
        self.assertEqual(d["pending_orphans"], [])
        self.assertEqual(set(d["counts"]), {k for k, _t, _h in anomalies.CATEGORIES})
        self.assertEqual(d["total"], sum(d["counts"].values()))


def item_result(idx, tipo, sub_id, **kw):
    return {"index": idx, "tipo": tipo, "subscription_id": sub_id, "profile_id": "p", "profile_changed": False,
            "end_date": "2027-01-20T03:59:59+00:00", **kw}


def sub_row(sid):
    return {"id": sid, "end_date": "2027-01-20T03:59:59+00:00",
            "platforms": {"name": HOSTILE, "slug": "netflix", "icon_emoji": "🎬"},
            "profiles": {"profile_name": HOSTILE, "pin": "1234", "accounts": {"email": "c@ejemplo.test", "password": "clave<script>"}}}


class TestNotifications(unittest.TestCase):
    def test_reject_reasons(self):
        self.assertIn("monto", notes.REJECT_REASONS)
        self.assertEqual(notes.reject_reason_text("monto"), notes.REJECT_REASONS["monto"])
        self.assertTrue(notes.reject_reason_text("otro", "  Falta la fecha ").endswith("Falta la fecha"))
        with self.assertRaises(ValueError):
            notes.reject_reason_text("inventado")

    def test_client_messages_per_item_with_access_and_escaping(self):
        items = [item_result(0, "renovacion", "s1", profile_changed=True), item_result(1, "nueva", "s2"), item_result(2, "cambio", "s3")]
        msgs = notes.client_item_messages(items, {s: sub_row(s) for s in ("s1", "s2", "s3")})
        self.assertEqual(len(msgs), 6)                                # confirmación + acceso por ítem
        self.assertIn("Renovación confirmada", msgs[0])
        self.assertIn("Tu perfil cambió", msgs[0])
        self.assertIn("Pago confirmado", msgs[2])
        self.assertIn("Cambio de plataforma", msgs[4])
        joined = "\n".join(msgs)
        self.assertNotIn("<script>", joined)                          # nada del cliente/plataforma sin escapar
        self.assertIn("clave&lt;script&gt;", joined)
        self.assertIn("1234", msgs[1])
        self.assertEqual(notes.client_item_messages([item_result(0, "nueva", "zzz")], {}), [])

    def order(self):
        m = cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000")
        return {"id": OID, "manifest": m, "users": {"name": HOSTILE, "telegram_id": 5}, "status": "pending",
                "created_at": iso(NOW), "ocr_flags": {"alertas": ["⚠️ x"], "monto": {"nivel": "warn", "detalle": "no coincide"}}}

    def test_admin_summary_variants(self):
        o = self.order()
        names = {"nf": "🎬 Netflix"}
        res = {"ok": True, "already_applied": False, "items": [item_result(0, "nueva", "s1")]}
        text = notes.admin_approval_summary(o, res, [], names)
        self.assertIn("aprobada", text)
        self.assertIn("19/01/2027", text)               # 20/01 03:59:59 UTC es el 19 en Caracas
        again = notes.admin_approval_summary(o, {**res, "already_applied": True}, [], names)
        self.assertIn("ya estaba aprobada", again)
        notices = [{"kind": "pin_rotated", "profile_name": "P<1>", "account_email": "c@ejemplo.test", "old_pin": "1", "new_pin": "2"},
                   {"kind": "profile_changed"}, {"kind": "profile_not_released", "profile_name": "Q"}]
        full = notes.admin_approval_summary(o, res, notices, names)
        self.assertIn("PIN anterior" if False else "<code>1</code>", full)
        self.assertIn("P&lt;1&gt;", full)
        self.assertIn("Se asignó otro perfil", full)
        self.assertIn("NO se liberó", full)

    def test_order_view_is_flat_and_unescaped_for_templates(self):
        v = notes.order_view(self.order(), {"nf": "🎬 <Net>"})
        self.assertEqual(v["short"], "AAAAAAAA")
        self.assertEqual(v["status_label"], "Pendiente")
        self.assertIn("<Net>", v["items"][0]["label"])                 # la plantilla escapa, no el servicio
        self.assertEqual(v["alerts"], ["⚠️ x"])
        self.assertFalse(v["has_receipt"])


class TestOrderAdmin(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.order = {"id": OID, "status": "pending", "users": {"telegram_id": 5, "name": "Cliente Ficticio"},
                      "manifest": cs.build_manifest([cs.build_new_item("nf", "monthly", "5")], "1000")}
        self.result = {"ok": True, "already_applied": False, "order_id": OID, "items": [item_result(0, "nueva", "s1")]}
        self.sent = mock.AsyncMock(return_value=True)
        self.approve = mock.AsyncMock(side_effect=lambda *a, **k: self.result)
        self.decide = mock.AsyncMock(return_value=True)
        self.inc = mock.AsyncMock()
        for p in (mock.patch("database.orders.get_order", new=mock.AsyncMock(side_effect=lambda _i: self.order)),
                  mock.patch("database.orders.get_subscriptions_with_access", new=mock.AsyncMock(return_value={"s1": sub_row("s1")})),
                  mock.patch("database.orders.decide_order", self.decide),
                  mock.patch("database.platforms.get_platform_by_id", new=mock.AsyncMock(return_value={"name": "Netflix", "icon_emoji": "🎬"})),
                  mock.patch("database.users.increment_user_purchases", self.inc),
                  mock.patch("services.notification_service.send_to_user", self.sent),
                  mock.patch.object(cs, "approve", self.approve)):
            p.start(); self.addCleanup(p.stop)

    async def test_approve_notifies_the_client_and_counts_the_purchase(self):
        out = await order_admin.approve_and_notify(OID, "admin:1", None, "REF")
        self.approve.assert_awaited_once_with(OID, "admin:1", None, "REF")
        self.assertEqual(self.sent.await_count, 2)
        self.assertTrue(out["client_notified"])
        self.inc.assert_awaited_once_with(5)
        self.assertNotIn("No se pudo avisar", out["summary"])

    async def test_second_approval_changes_nothing_and_does_not_message_again(self):
        self.result = {**self.result, "already_applied": True}
        out = await order_admin.approve_and_notify(OID, "admin:1")
        self.sent.assert_not_awaited(); self.inc.assert_not_awaited()
        self.assertIn("ya estaba aprobada", out["summary"])

    async def test_client_without_telegram_or_failed_send_is_called_out_to_the_admin(self):
        self.order["users"]["telegram_id"] = None
        out = await order_admin.approve_and_notify(OID, "admin:1")
        self.assertFalse(out["client_notified"])
        self.assertIn("no tiene Telegram", out["summary"])
        self.order["users"]["telegram_id"] = 5
        self.sent.return_value = False
        out = await order_admin.approve_and_notify(OID, "admin:1")
        self.assertIn("No se pudo avisar al cliente", out["summary"])

    async def test_approval_errors_propagate_and_nothing_is_sent(self):
        self.approve.side_effect = cs.ApprovalError("profile_unavailable")
        with self.assertRaises(cs.ApprovalError):
            await order_admin.approve_and_notify(OID, "admin:1")
        self.sent.assert_not_awaited(); self.inc.assert_not_awaited()
        self.order = None
        with self.assertRaises(cs.ApprovalError) as cm:
            await order_admin.approve_and_notify(OID, "admin:1")
        self.assertEqual(cm.exception.code, "order_not_found")

    async def test_reject_records_reason_and_offers_the_client_a_new_order(self):
        out = await order_admin.reject_and_notify(OID, "admin:1", "monto", "Faltan Bs 50")
        self.assertTrue(out["ok"])
        args = self.decide.await_args.args
        self.assertEqual((args[0], args[1], args[2]), (OID, "rejected", "admin:1"))
        self.assertIn("Faltan Bs 50", args[3])
        kw = self.sent.await_args
        self.assertIn("Comprobante no aprobado", kw.args[1])
        self.assertEqual(kw.kwargs["keyboard"].inline_keyboard[0][0].callback_data, "menu:subscribe")

    async def test_reject_lost_race_invalid_reason_and_missing_order(self):
        self.decide.return_value = False
        out = await order_admin.reject_and_notify(OID, "admin:1", "otro")
        self.assertFalse(out["ok"]); self.sent.assert_not_awaited()
        with self.assertRaises(ValueError):
            await order_admin.reject_and_notify(OID, "admin:1", "inventado")
        self.order = None
        with self.assertRaises(cs.CheckoutError):
            await order_admin.reject_and_notify(OID, "admin:1", "otro")


if __name__ == "__main__":
    unittest.main()
