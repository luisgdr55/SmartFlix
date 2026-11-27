"""Pruebas de O02 Lote 6 (scheduler, opción B): sin corte automático, recordatorios D+1..D+6 con reclamo
atómico, resumen diario de mora, jobs con candado, avisos con textos nuevos y corte manual con advertencia."""
from tests import support  # noqa: F401

import inspect
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest import mock

from services import mora
from tests.test_checkout_service import caracas
from utils.dates import parse_db_datetime

NOW = caracas(2026, 12, 10, 9)


def iso(dt):
    return dt if isinstance(dt, str) else dt.isoformat()


def sub(sid, days_ago=None, status="active", plan="monthly", price=5, user=None, count=0, **kw):
    """Suscripción vencida hace `days_ago` días (fin de día de Caracas) o vigente si es None."""
    end = (caracas(2026, 12, 10, 23, 59, 59) - timedelta(days=days_ago)) if days_ago is not None else NOW + timedelta(days=9)
    return {"id": sid, "status": status, "plan_type": plan, "price_usd": price, "end_date": iso(end), "profile_id": "p-" + sid,
            "debt_reminder_count": count, "expiry_notified": False, "reminder_sent": False, "user_id": "u-" + sid,
            "users": user or {"name": f"Cliente {sid}", "telegram_id": 100 + len(sid)}, "platforms": {"name": "Netflix", "icon_emoji": "🎬"}, **kw}


class TestMora(unittest.TestCase):
    def test_debt_is_price_times_periods_to_be_current(self):
        self.assertEqual(mora.debt_usd(sub("a", 8), NOW), D("5.00"))                  # 8 días: 1 periodo
        self.assertEqual(mora.debt_usd(sub("b", 45), NOW), D("10.00"))                # más de un mes: 2 periodos
        self.assertEqual(mora.debt_usd(sub("c", 8, price=0), NOW), D("0.00"))
        self.assertEqual(mora.debt_usd({"price_usd": 5}, NOW), D("0.00"))

    def test_rows_only_from_7_days_sorted_by_debt(self):
        subs = [sub("a", 6), sub("b", 7), sub("c", 40, price=4), sub("d", 8, status="expired"), sub("e", None), sub("f", 9, status="cancelled")]
        rows = mora.mora_rows(subs, NOW)
        self.assertEqual([r["sub_id"] for r in rows], ["c", "d", "b"])
        self.assertEqual(rows[0]["days"], 40)
        self.assertEqual(mora.mora_rows([sub("a", 6)], NOW, min_days=3)[0]["days"], 6)

    def test_summary_is_capped_escaped_and_says_service_stays_active(self):
        rows = mora.mora_rows([sub(f"{i:02d}", 10 + i, user={"name": "Ana <b>", "telegram_id": 500 + i}) for i in range(20)], NOW)
        text = mora.summary_text(rows, "https://panel.test/x")
        self.assertIn("20 servicio(s)", text)
        self.assertEqual(text.count("• "), mora.SUMMARY_CAP)
        self.assertIn("… y 5 más", text)
        self.assertIn("Ana &lt;b&gt;", text)
        self.assertIn("sigue activo", text)
        self.assertNotIn("liber", text.lower())
        self.assertIn("https://panel.test/x", text)

    def test_buttons_one_per_telegram_client_without_duplicates(self):
        subs = [sub("a", 10, user={"name": "A", "telegram_id": 7}), sub("b", 12, user={"name": "A", "telegram_id": 7}),
                sub("c", 14, user={"name": "Externo", "telegram_id": None})]
        rows = mora.mora_rows(subs, NOW)
        self.assertEqual([cb for _t, cb in mora.client_buttons(rows)], ["admin:client_detail:7"])
        self.assertIn("(sin Telegram)", mora.summary_text(rows))


class FakeQ:
    ops = {"eq": lambda a, b: a == b, "neq": lambda a, b: a != b}

    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.payload, self._limit = db, name, [], "select", None, None

    def select(self, *a, **k): return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def neq(self, c, v): self.f.append(("neq", c, v)); return self
    def lt(self, c, v): self.f.append(("lt", c, v)); return self
    def lte(self, c, v): self.f.append(("lte", c, v)); return self
    def gte(self, c, v): self.f.append(("gte", c, v)); return self
    def in_(self, c, v): self.f.append(("in", c, v)); return self
    def limit(self, n): self._limit = n; return self

    @property
    def not_(self):
        q = self
        return SimpleNamespace(in_=lambda c, v: (q.f.append(("nin", c, v)), q)[1])
    def order(self, *a, **k): return self

    @staticmethod
    def _dt(x):
        return parse_db_datetime(x)

    def _ok(self, r):
        for op, c, v in self.f:
            x = r.get(c)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "nin" and x in v: return False
            if op in ("lt", "lte", "gte"):
                if c.endswith("date") or c.endswith("_at"):
                    a, b = self._dt(x), self._dt(v)
                else:
                    a, b = x or 0, v
                if op == "lt" and not a < b: return False
                if op == "lte" and not a <= b: return False
                if op == "gte" and not a >= b: return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        hit = [r for r in rows if self._ok(r)]
        if self.op == "update":
            for r in hit: r.update(self.payload)
        return SimpleNamespace(data=[dict(r) for r in (hit[: self._limit] if self._limit else hit)])


class FakeSB:
    def __init__(self, **tables): self.tables = tables
    def table(self, n): return FakeQ(self, n)


class TestQueries(unittest.IsolatedAsyncioTestCase):
    def use(self, rows):
        from database import subscriptions as m
        self.m, self.db = m, FakeSB(subscriptions=rows)
        for p in (mock.patch.object(m, "get_supabase", lambda: self.db), mock.patch.object(m, "venezuela_now", lambda: NOW)):
            p.start(); self.addCleanup(p.stop)

    async def test_debt_reminders_days_1_to_6_once_per_day_never_after(self):
        self.use([sub("d0", 0), sub("d1", 1), sub("d3", 3), sub("d6", 6), sub("d7", 7), sub("old", 40, status="expired"),
                  sub("done3", 3, count=3), sub("stale", 2, count=4), sub("exp2", 2, status="expired"), sub("express", 2, plan="express"),
                  sub("canc", 2, status="cancelled"), sub("future", None)])
        got = {r["id"]: r["days_overdue"] for r in await self.m.get_subscriptions_for_debt_reminders()}
        self.assertEqual(got, {"d1": 1, "d3": 3, "d6": 6, "exp2": 2})

    async def test_atomic_claim_only_one_wins(self):
        rows = [sub("a", 3)]
        self.use(rows)
        self.assertTrue(await self.m.claim_debt_reminder("a", 3))
        self.assertFalse(await self.m.claim_debt_reminder("a", 3))              # segunda pasada simultánea
        await self.m.release_debt_reminder("a", 3)
        self.assertEqual(rows[0]["debt_reminder_count"], 2)
        self.assertTrue(await self.m.claim_debt_reminder("a", 3))               # tras devolver el reclamo se reintenta

    async def test_flag_claims_are_atomic_and_whitelisted(self):
        rows = [sub("a", None)]
        self.use(rows)
        self.assertTrue(await self.m.claim_flag("a", "reminder_sent"))
        self.assertFalse(await self.m.claim_flag("a", "reminder_sent"))
        await self.m.release_flag("a", "reminder_sent")
        self.assertTrue(await self.m.claim_flag("a", "reminder_sent"))
        with self.assertRaises(ValueError):
            await self.m.claim_flag("a", "status")

    async def test_d0_includes_legacy_expired_but_only_the_last_48_hours(self):
        self.use([sub("fresh", 0, end_date=iso(NOW - timedelta(hours=2))), sub("legacy", 0, status="expired", end_date=iso(NOW - timedelta(hours=30))),
                  sub("old", 0, end_date=iso(NOW - timedelta(hours=60))), sub("notified", 0, end_date=iso(NOW - timedelta(hours=2)), expiry_notified=True),
                  sub("future", None)])
        ids = {r["id"] for r in await self.m.get_expired_subscriptions_to_notify()}
        self.assertEqual(ids, {"fresh", "legacy"})

    async def test_attention_derives_mora_from_the_date(self):
        self.use([sub("late", 3, user_id="U"), sub("legacy", 3, status="expired", user_id="U"), sub("soon", None, user_id="U", end_date=iso(NOW + timedelta(days=2))), sub("far", None, user_id="U", end_date=iso(NOW + timedelta(days=20)))])
        out = await self.m.get_user_attention_subscriptions("U")
        self.assertEqual({s["id"] for s in out["expired"]}, {"late", "legacy"})
        self.assertEqual({s["id"] for s in out["expiring_soon"]}, {"soon"})

    async def test_legacy_renewal_starts_a_new_notice_cycle(self):
        rows = [sub("a", 5, count=6, reminder_sent=True, expiry_notified=True)]
        self.use(rows)
        await self.m.confirm_renewal_subscription("a", "p1", "REF", NOW + timedelta(days=30))
        self.assertEqual((rows[0]["reminder_sent"], rows[0]["expiry_notified"], rows[0]["debt_reminder_count"]), (False, False, 0))

    def test_auto_expire_and_the_automatic_cut_queries_are_gone(self):
        from database import subscriptions as m
        for name in ("auto_expire_overdue_subscriptions", "get_subscriptions_past_grace_period", "get_subscriptions_in_grace_period", "increment_debt_reminder"):
            self.assertFalse(hasattr(m, name), name)


class TestJobs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from scheduler import jobs
        self.jobs = jobs
        self.n = SimpleNamespace(
            claim_flag=mock.AsyncMock(return_value=True), release_flag=mock.AsyncMock(),
            claim_debt=mock.AsyncMock(return_value=True), release_debt=mock.AsyncMock(),
            reminder=mock.AsyncMock(return_value=True), notif=mock.AsyncMock(return_value=True), debt=mock.AsyncMock(return_value=True),
            admin=mock.AsyncMock(return_value=True), cancel=mock.AsyncMock(), expire=mock.AsyncMock(return_value=2))
        self.expiring = [sub("a", None, end_date=iso(NOW + timedelta(days=2)))]
        self.expired = [sub("b", 0, end_date=iso(NOW - timedelta(hours=1)))]
        self.debts = [{**sub("c", 3), "days_overdue": 3}]
        self.overdue = []
        for p in (mock.patch("database.subscriptions.claim_flag", self.n.claim_flag), mock.patch("database.subscriptions.release_flag", self.n.release_flag),
                  mock.patch("database.subscriptions.claim_debt_reminder", self.n.claim_debt), mock.patch("database.subscriptions.release_debt_reminder", self.n.release_debt),
                  mock.patch("database.subscriptions.get_expiring_subscriptions", new=mock.AsyncMock(side_effect=lambda days_ahead=3: self.expiring)),
                  mock.patch("database.subscriptions.get_expired_subscriptions_to_notify", new=mock.AsyncMock(side_effect=lambda: self.expired)),
                  mock.patch("database.subscriptions.get_subscriptions_for_debt_reminders", new=mock.AsyncMock(side_effect=lambda: self.debts)),
                  mock.patch("database.subscriptions.get_overdue_subscriptions", new=mock.AsyncMock(side_effect=lambda d: self.overdue)),
                  mock.patch("database.subscriptions.cancel_subscription", self.n.cancel),
                  mock.patch("services.notification_service.send_expiry_reminder", self.n.reminder),
                  mock.patch("services.notification_service.send_expiry_notification", self.n.notif),
                  mock.patch("services.notification_service.send_debt_reminder", self.n.debt),
                  mock.patch("services.notification_service.send_to_admin", self.n.admin),
                  mock.patch("database.orders.expire_stale_orders", self.n.expire),
                  mock.patch("asyncio.sleep", new=mock.AsyncMock()), *support.patch_locks()):
            p.start(); self.addCleanup(p.stop)

    async def test_d3_and_d0_claim_before_sending_and_return_the_claim_on_failure(self):
        await self.jobs.job_expiry_reminders()
        self.n.claim_flag.assert_awaited_with("a", "reminder_sent")
        self.n.reminder.assert_awaited_once_with("a")
        self.n.release_flag.assert_not_awaited()
        self.n.reminder.return_value = False
        await self.jobs.job_expiry_reminders()
        self.n.release_flag.assert_awaited_with("a", "reminder_sent")
        await self.jobs.job_expiry_notifications()
        self.n.claim_flag.assert_awaited_with("b", "expiry_notified")
        self.n.notif.assert_awaited_once_with("b")

    async def test_a_lost_claim_sends_nothing(self):
        self.n.claim_flag.return_value = False
        self.n.claim_debt.return_value = False
        await self.jobs.job_expiry_reminders(); await self.jobs.job_expiry_notifications(); await self.jobs.job_debt_reminders_and_cuts()
        self.n.reminder.assert_not_awaited(); self.n.notif.assert_not_awaited(); self.n.debt.assert_not_awaited()

    async def test_debt_job_sends_reminders_and_NEVER_cuts(self):
        await self.jobs.job_debt_reminders_and_cuts()
        self.n.claim_debt.assert_awaited_once_with("c", 3)
        self.n.debt.assert_awaited_once()
        self.assertEqual(self.n.debt.await_args.args[1], 3)
        self.n.cancel.assert_not_awaited()
        src = inspect.getsource(self.jobs)
        for forbidden in ("cancel_subscription", "past_grace", "Corte automático", '"status": "available"' if False else "get_subscriptions_past_grace_period"):
            self.assertNotIn(forbidden, src.replace("sin corte automático", "").replace("no hay corte automático", ""))
        self.n.debt.return_value = False
        await self.jobs.job_debt_reminders_and_cuts()
        self.n.release_debt.assert_awaited_with("c", 3)

    async def test_daily_mora_summary_goes_to_the_admin_with_client_buttons(self):
        self.debts = []
        self.overdue = [sub("x", 12, user={"name": "Ana", "telegram_id": 77}), sub("y", 9, user={"name": "Luis", "telegram_id": None})]
        with mock.patch("services.mora.now_caracas", return_value=NOW):
            await self.jobs.job_debt_reminders_and_cuts()
        self.n.admin.assert_awaited_once()
        text = self.n.admin.await_args.args[0]
        self.assertIn("Mora de 7 días", text); self.assertIn("Ana", text); self.assertIn("sigue activo", text)
        cbs = [b.callback_data for row in self.n.admin.await_args.kwargs["keyboard"].inline_keyboard for b in row]
        self.assertIn("admin:client_detail:77", cbs)

    async def test_no_summary_when_nobody_is_that_late_and_errors_do_not_escape(self):
        self.debts, self.overdue = [], [sub("x", 3)]
        await self.jobs.job_debt_reminders_and_cuts()
        self.n.admin.assert_not_awaited()
        self.n.admin.side_effect = RuntimeError("telegram caído")
        self.overdue = [sub("x", 12)]
        with mock.patch("services.mora.now_caracas", return_value=NOW):
            await self.jobs.job_debt_reminders_and_cuts()          # no lanza

    async def test_jobs_are_skipped_when_the_lock_is_held_or_redis_is_down(self):
        for p in support.patch_locks(obtained=False):
            p.start(); self.addCleanup(p.stop)
        await self.jobs.job_expiry_reminders(); await self.jobs.job_debt_reminders_and_cuts(); await self.jobs.job_expire_orders()
        self.n.claim_flag.assert_not_awaited(); self.n.claim_debt.assert_not_awaited(); self.n.expire.assert_not_awaited()

    async def test_order_expiry_runs_only_with_the_flag(self):
        from config import settings
        await self.jobs.job_expire_orders()
        self.n.expire.assert_not_awaited()
        with mock.patch.object(settings, "CHECKOUT_V2_ENABLED", True):
            await self.jobs.job_expire_orders()
        self.n.expire.assert_awaited_once()

    def test_scheduler_has_no_auto_expire_and_every_job_is_hardened(self):
        self.jobs.scheduler.remove_all_jobs()
        sc = self.jobs.setup_scheduler()
        ids = {j.id for j in sc.get_jobs()}
        self.assertNotIn("auto_expire", ids)
        self.assertEqual(ids, {"expiry_reminders", "expiry_notifications", "express_release", "queue_cleanup", "new_releases_scan",
                               "pending_payment_cleanup", "daily_admin_report", "debt_reminders_and_cuts", "release_expired_reservations",
                               "job_monthly_closing_report", "expire_orders"})
        for j in sc.get_jobs():
            self.assertTrue(j.coalesce, j.id); self.assertEqual(j.max_instances, 1, j.id); self.assertGreaterEqual(j.misfire_grace_time or 0, 60, j.id)
        self.jobs.scheduler.remove_all_jobs()


class TestNotices(unittest.IsolatedAsyncioTestCase):
    async def test_send_to_user_respects_retry_after_and_stops_on_other_errors(self):
        from telegram.error import BadRequest, RetryAfter
        from services import notification_service as ns
        bot = SimpleNamespace(send_message=mock.AsyncMock(side_effect=[RetryAfter(2), None]))
        sleep = mock.AsyncMock()
        with mock.patch.object(ns, "get_bot", return_value=bot), mock.patch("asyncio.sleep", sleep):
            self.assertTrue(await ns.send_to_user(1, "hola"))
        sleep.assert_awaited_once_with(2.0)
        self.assertEqual(bot.send_message.await_count, 2)
        bot = SimpleNamespace(send_message=mock.AsyncMock(side_effect=RetryAfter(1)))
        with mock.patch.object(ns, "get_bot", return_value=bot), mock.patch("asyncio.sleep", mock.AsyncMock()):
            self.assertFalse(await ns.send_to_user(1, "hola"))
        self.assertEqual(bot.send_message.await_count, 3)                       # 3 intentos y se rinde
        bot = SimpleNamespace(send_message=mock.AsyncMock(side_effect=BadRequest("bloqueado")))
        with mock.patch.object(ns, "get_bot", return_value=bot):
            self.assertFalse(await ns.send_to_user(1, "hola"))
        self.assertEqual(bot.send_message.await_count, 1)                       # sin reintento

    async def test_debt_reminder_text_no_longer_threatens_a_cut_that_does_not_exist(self):
        from services import notification_service as ns
        s = sub("a", 3)
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(ns, "send_to_user", sent):
            for day in (1, 5, 6):
                await ns.send_debt_reminder({**s, "platform_id": "p"}, day)
        for call in sent.await_args_list:
            text = call.args[1].lower()
            self.assertIn("mora", text)
            self.assertNotIn("liber", text)
            self.assertNotIn("/6", text)
            self.assertTrue(call.args[2].inline_keyboard)                       # siempre con botón de renovar
        self.assertIn("último recordatorio", sent.await_args_list[2].args[1])
        admin = mock.AsyncMock(return_value=True)
        with mock.patch.object(ns, "send_to_admin", admin):
            await ns.send_debt_reminder({**s, "users": {"name": "Externo", "telegram_id": None}}, 2)
        self.assertNotIn("liber", admin.await_args.args[0].lower())

    def test_hard_cut_wording_and_sender_are_gone(self):
        from bot import messages
        from services import notification_service as ns
        self.assertFalse(hasattr(messages, "HARD_CUT_NOTIFICATION"))
        self.assertFalse(hasattr(ns, "send_hard_cut_notification"))
        self.assertNotIn("expirado", messages.EXPIRY_NOTIFICATION)


class TestManualCutWarning(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_cut_warns_when_there_is_a_pending_payment_and_forces_on_demand(self):
        from bot.handlers import admin as admin_mod
        sub_row = {"id": "s1", "user_id": "u1", "profile_id": None, "users": {"telegram_id": 5}, "platforms": {"name": "Netflix", "icon_emoji": "🎬"}}
        q = SimpleNamespace(data="admin:cancel_sub_confirm:s1", answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock(), from_user=SimpleNamespace(id=777))
        upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=777), effective_message=None)
        cancel = mock.AsyncMock(return_value=True)
        with mock.patch.object(admin_mod, "_check_admin", return_value=True), mock.patch.object(admin_mod, "get_subscription_by_id", new=mock.AsyncMock(return_value=sub_row)), \
             mock.patch("database.orders.pending_for_user", new=mock.AsyncMock(return_value={"orders": ["ABC12345"], "legacy": []})), \
             mock.patch.object(admin_mod, "cancel_subscription", cancel), mock.patch.object(admin_mod, "log_admin_action", new=mock.AsyncMock()), \
             mock.patch("services.notification_service.send_to_user", new=mock.AsyncMock()):
            await admin_mod.handle_admin_callback(upd, None)
            cancel.assert_not_awaited()
            text = q.edit_message_text.await_args.args[0]
            self.assertIn("pago pendiente", text); self.assertIn("#ABC12345", text)
            cbs = [b.callback_data for row in q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
            self.assertIn("admin:cancel_sub_confirm:s1:force", cbs); self.assertIn("admin:pending", cbs); self.assertIn("admin:back", cbs)
            q.data = "admin:cancel_sub_confirm:s1:force"
            await admin_mod.handle_admin_callback(upd, None)
            cancel.assert_awaited_once_with("s1")

    async def test_telegram_cut_without_pending_payment_goes_straight_through(self):
        from bot.handlers import admin as admin_mod
        sub_row = {"id": "s1", "user_id": "u1", "profile_id": None, "users": {"telegram_id": 5}, "platforms": {"name": "Netflix", "icon_emoji": "🎬"}}
        q = SimpleNamespace(data="admin:cancel_sub_confirm:s1", answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock(), from_user=SimpleNamespace(id=777))
        upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=777), effective_message=None)
        cancel = mock.AsyncMock(return_value=True)
        with mock.patch.object(admin_mod, "_check_admin", return_value=True), mock.patch.object(admin_mod, "get_subscription_by_id", new=mock.AsyncMock(return_value=sub_row)), \
             mock.patch("database.orders.pending_for_user", new=mock.AsyncMock(return_value={"orders": [], "legacy": []})), \
             mock.patch.object(admin_mod, "cancel_subscription", cancel), mock.patch.object(admin_mod, "log_admin_action", new=mock.AsyncMock()), \
             mock.patch("services.notification_service.send_to_user", new=mock.AsyncMock()):
            await admin_mod.handle_admin_callback(upd, None)
        cancel.assert_awaited_once_with("s1")

    async def test_pending_summary_tolerates_the_missing_orders_table(self):
        from database import orders
        class SB:
            def table(self, name):
                q = mock.MagicMock()
                if name == "checkout_orders":
                    q.select.return_value.eq.return_value.eq.return_value.execute.side_effect = RuntimeError('relation "checkout_orders" does not exist')
                else:
                    q.select.return_value.eq.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[{"id": "abcdef12-0"}])
                return q
        with mock.patch.object(orders, "get_supabase", return_value=SB()):
            out = await orders.pending_for_user("u1")
        self.assertEqual(out, {"orders": [], "legacy": ["ABCDEF12"]})


class TestPanelPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        from starlette.testclient import TestClient
        cls.main, cls.TestClient = main, TestClient

    def client(self):
        from admin_panel import security as sec
        from tests.support import PANEL_PASSWORD, login_csrf_from
        sec._throttle = sec.LoginThrottle(sec._MemoryStore())
        c = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        c.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": login_csrf_from(c.get("/panel/login").text)})
        return c

    def test_panel_gets_no_longer_write_and_the_overdue_filter_is_derived(self):
        from admin_panel import router
        src = inspect.getsource(router)
        self.assertNotIn("auto_expire_overdue_subscriptions", src)
        calls = []

        class Q:
            def __getattr__(self, name):
                def f(*a, **k):
                    calls.append((name, a)); return self
                return f
            def execute(self): return SimpleNamespace(data=[], count=0)
        sb = SimpleNamespace(table=lambda n: Q())
        with mock.patch("database.get_supabase", return_value=sb), mock.patch("database.platforms.get_active_platforms", new=mock.AsyncMock(return_value=[])):
            r = self.client().get("/panel/subscriptions?status=overdue")
        self.assertEqual(r.status_code, 200)
        self.assertIn("in_", [c[0] for c in calls]); self.assertIn("lt", [c[0] for c in calls])
        self.assertNotIn(("eq", ("status", "overdue")), calls)
        self.assertNotIn("update", [c[0] for c in calls])                       # el GET no escribe

    def test_cut_confirmation_page_and_redirects(self):
        from admin_panel import router
        sub_row = {"id": "s1", "user_id": "u1", "profile_id": "p1", "plan_type": "monthly", "users": {"name": "Ana <b>"}, "platforms": {"name": "Netflix", "icon_emoji": "🎬"}}
        class Q:
            def __getattr__(self, name):
                def f(*a, **k): return self
                return f
            def execute(self): return SimpleNamespace(data=[sub_row], count=1)
        sb = SimpleNamespace(table=lambda n: Q())
        c = self.client()
        with mock.patch("database.get_supabase", return_value=sb), mock.patch("database.orders.pending_for_user", new=mock.AsyncMock(return_value={"orders": ["ABC12345"], "legacy": ["DEF67890"]})):
            page = c.get("/panel/subscriptions/s1/confirm-cut?kind=release")
            self.assertEqual(page.status_code, 200)
            self.assertIn("tiene un pago pendiente", page.text); self.assertIn("#ABC12345", page.text); self.assertIn("#DEF67890", page.text)
            self.assertIn("Ana &lt;b&gt;", page.text); self.assertNotIn("Ana <b>", page.text)
            self.assertIn('/panel/subscriptions/s1/release', page.text); self.assertIn('name="confirm_pending"', page.text)
            self.assertIn('name="csrf_token"', page.text)
            from admin_panel.security import csrf_token_for
            tok = csrf_token_for(c.cookies.get("sv_admin_session"))
            for path, kind in (("/panel/subscriptions/s1/soft-cut", "soft"), ("/panel/subscriptions/s1/release", "release")):
                r = c.post(path, data={"csrf_token": tok})
                self.assertEqual((r.status_code, r.headers["location"]), (302, f"/panel/subscriptions/s1/confirm-cut?kind={kind}"), path)


if __name__ == "__main__":
    unittest.main()
