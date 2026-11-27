"""Pruebas de O02-H: guardas de aprobar/rechazar, liberación segura de perfiles y
candidatas de express (stdlib `unittest`, base de datos falsa, datos ficticios)."""
from tests import support  # noqa: F401  (variables de entorno de prueba)

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from services import payment_guards as pg

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
ADMIN = 777


def iso(dt):
    return dt.isoformat()


class TestPureGuards(unittest.TestCase):
    def test_can_decide_payment(self):
        self.assertTrue(pg.can_decide_payment("pending_payment"))
        for st in ("active", "expired", "expired_payment", "cancelled", None, ""):
            self.assertFalse(pg.can_decide_payment(st), st)

    def test_status_label_has_fallback(self):
        self.assertEqual(pg.status_label("active"), "activa")
        self.assertEqual(pg.status_label("raro"), "raro")
        self.assertEqual(pg.status_label(None), "desconocido")

    def test_profile_safe_to_release(self):
        self.assertTrue(pg.profile_is_safe_to_release("occupied", 0))
        self.assertFalse(pg.profile_is_safe_to_release("occupied", 1))
        self.assertFalse(pg.profile_is_safe_to_release("available", 0))
        self.assertFalse(pg.profile_is_safe_to_release("reserved", 0))
        self.assertFalse(pg.profile_is_safe_to_release(None, 0))


class TestExpressCandidates(unittest.TestCase):
    def row(self, status, hours_ago, profile_status="occupied", with_profile=True, tz=True):
        end = NOW - timedelta(hours=hours_ago)
        if not tz:
            end = end.replace(tzinfo=None)
        r = {"id": f"{status}-{hours_ago}", "status": status, "end_date": iso(end)}
        r["profiles"] = {"id": "p1", "status": profile_status} if with_profile else None
        return r

    def ids(self, rows):
        return [r["id"] for r in pg.express_release_candidates(rows, NOW)]

    def test_active_past_included_even_if_old(self):
        self.assertEqual(self.ids([self.row("active", 1), self.row("active", 100)]), ["active-1", "active-100"])

    def test_active_future_excluded(self):
        self.assertEqual(self.ids([self.row("active", -2)]), [])

    def test_expired_recent_with_occupied_profile_included(self):
        self.assertEqual(self.ids([self.row("expired", 2)]), ["expired-2"])

    def test_expired_older_than_24h_excluded(self):
        self.assertEqual(self.ids([self.row("expired", 25)]), [])

    def test_expired_with_released_profile_not_reprocessed(self):
        self.assertEqual(self.ids([self.row("expired", 2, profile_status="available")]), [])

    def test_expired_without_profile_excluded(self):
        self.assertEqual(self.ids([self.row("expired", 2, with_profile=False)]), [])

    def test_other_states_and_bad_dates_ignored(self):
        bad = {"id": "bad", "status": "active", "end_date": "no-es-fecha"}
        self.assertEqual(self.ids([self.row("cancelled", 1), self.row("pending_payment", 1), bad]), [])

    def test_naive_datetime_accepted(self):
        self.assertEqual(self.ids([self.row("expired", 2, tz=False)]), ["expired-2"])


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters, self.upd = [], None

    def select(self, *_a, **_k):
        return self

    def update(self, values):
        self.upd = values
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def limit(self, *_a):
        return self

    def _match(self, row):
        for op, col, val in self.filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "neq" and row.get(col) == val:
                return False
        return True

    def execute(self):
        rows = [r for r in self.db.tables[self.name] if self._match(r)]
        if self.upd is not None:
            for r in rows:
                r.update(self.upd)
            self.db.updates.append((self.name, dict(self.upd)))
        return SimpleNamespace(data=[dict(r) for r in rows])


class FakeDB:
    def __init__(self, profiles=None, subs=None, fail=False):
        self.tables = {"profiles": profiles or [], "subscriptions": subs or []}
        self.updates = []
        self.fail = fail

    def table(self, name):
        if self.fail:
            raise RuntimeError("db caída")
        return FakeTable(self, name)


class TestReleaseProfileForCut(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from database import profiles as prof_mod
        self.mod = prof_mod

    def run_with(self, db):
        return mock.patch.object(self.mod, "get_supabase", lambda: db)

    def profile(self, status="occupied"):
        return {"id": "p1", "profile_name": "Perfil 1", "pin": "1111", "status": status,
                "accounts": {"email": "cuenta@ejemplo.test"}}

    async def test_releases_occupied_profile_without_other_active(self):
        db = FakeDB([self.profile()], [{"id": "s1", "profile_id": "p1", "status": "expired"}])
        with self.run_with(db):
            info = await self.mod.release_profile_for_cut("p1", exclude_sub_id="s1")
        self.assertTrue(info["released"])
        self.assertEqual(info["old_pin"], "1111")
        self.assertRegex(info["new_pin"], r"^\d{4}$")
        p = db.tables["profiles"][0]
        self.assertEqual(p["status"], "available")
        self.assertEqual(p["pin"], info["new_pin"])
        self.assertIsNone(p["reserved_until"])

    async def test_does_not_release_if_another_active_sub_uses_it(self):
        db = FakeDB([self.profile()], [{"id": "s1", "profile_id": "p1", "status": "expired"},
                                       {"id": "s2", "profile_id": "p1", "status": "active"}])
        with self.run_with(db):
            info = await self.mod.release_profile_for_cut("p1", exclude_sub_id="s1")
        self.assertFalse(info["released"])
        self.assertEqual(info["reason"], "otra_suscripcion_activa")
        self.assertEqual(db.updates, [])
        self.assertEqual(db.tables["profiles"][0]["pin"], "1111")

    async def test_excluded_sub_itself_does_not_block(self):
        db = FakeDB([self.profile()], [{"id": "s1", "profile_id": "p1", "status": "active"}])
        with self.run_with(db):
            info = await self.mod.release_profile_for_cut("p1", exclude_sub_id="s1")
        self.assertTrue(info["released"])

    async def test_already_available_is_idempotent(self):
        db = FakeDB([self.profile("available")], [])
        with self.run_with(db):
            info = await self.mod.release_profile_for_cut("p1")
        self.assertFalse(info["released"])
        self.assertEqual(info["reason"], "no_ocupado")
        self.assertEqual(db.updates, [])

    async def test_missing_profile_or_db_error_returns_none(self):
        with self.run_with(FakeDB([], [])):
            self.assertIsNone(await self.mod.release_profile_for_cut("nope"))
        with self.run_with(FakeDB([self.profile()], [], fail=True)):
            self.assertIsNone(await self.mod.release_profile_for_cut("p1"))


def make_callback(data):
    q = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=ADMIN)), q


class TestTelegramApproveReject(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from bot.handlers import admin
        self.admin = admin
        self.sub = {"id": "abcdef12-0000", "status": "pending_payment", "platform_id": "pl", "plan_type": "monthly",
                    "user_id": "u", "users": {"telegram_id": 5}, "platforms": {"name": "Netflix", "icon_emoji": "N"}}
        self.patches = [
            mock.patch.object(admin, "_check_admin", return_value=True),
            mock.patch.object(admin, "log_admin_action", new=mock.AsyncMock()),
            mock.patch("database.subscriptions.get_subscription_by_id", new=mock.AsyncMock(side_effect=lambda _id: self.sub)),
        ]
        self.get_sub = None
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_approve_blocked_when_not_pending(self):
        for status in ("active", "expired", "expired_payment", "cancelled"):
            self.sub["status"] = status
            upd, q = make_callback("admin:approve:abcdef12-0000")
            with mock.patch.object(self.admin, "get_available_profiles", new=mock.AsyncMock()) as gap, \
                 mock.patch.object(self.admin, "confirm_subscription", new=mock.AsyncMock()) as conf, \
                 mock.patch.object(self.admin, "assign_profile", new=mock.AsyncMock()) as asg:
                await self.admin.handle_admin_approve_payment(upd, None)
            gap.assert_not_awaited()
            conf.assert_not_awaited()
            asg.assert_not_awaited()
            text = q.edit_message_text.await_args.args[0]
            self.assertIn("ya no está pendiente", text)
            self.assertTrue(q.edit_message_text.await_args.kwargs.get("reply_markup"), "debe ofrecer una salida")

    async def test_approve_new_does_not_assign_profile_if_confirm_loses_race(self):
        upd, q = make_callback("admin:approve:abcdef12-0000")
        with mock.patch("database.subscriptions.get_user_platform_active_subscription", new=mock.AsyncMock(return_value=None)), \
             mock.patch("database.platforms.get_platform_by_id", new=mock.AsyncMock(return_value={"name": "N", "icon_emoji": ""})), \
             mock.patch.object(self.admin, "get_available_profiles", new=mock.AsyncMock(return_value=[{"id": "p9"}])), \
             mock.patch.object(self.admin, "confirm_subscription", new=mock.AsyncMock(return_value=False)), \
             mock.patch.object(self.admin, "assign_profile", new=mock.AsyncMock()) as asg:
            await self.admin.handle_admin_approve_payment(upd, None)
        asg.assert_not_awaited()
        self.assertTrue(q.edit_message_text.await_count >= 1)

    async def test_reject_blocked_when_active_and_nothing_deleted(self):
        self.sub["status"] = "active"
        upd, q = make_callback("admin:reject:abcdef12-0000")
        with mock.patch("database.subscriptions.delete_subscription", new=mock.AsyncMock()) as dele, \
             mock.patch("services.notification_service.send_to_user", new=mock.AsyncMock()) as send:
            await self.admin.handle_admin_reject_payment(upd, None)
        dele.assert_not_awaited()
        send.assert_not_awaited()
        self.assertIn("ya no está pendiente", q.edit_message_text.await_args.args[0])

    async def test_reject_pending_still_deletes_and_notifies(self):
        upd, q = make_callback("admin:reject:abcdef12-0000")
        with mock.patch("database.subscriptions.delete_subscription", new=mock.AsyncMock()) as dele, \
             mock.patch("services.notification_service.send_to_user", new=mock.AsyncMock()) as send:
            await self.admin.handle_admin_reject_payment(upd, None)
        dele.assert_awaited_once_with("abcdef12-0000")
        send.assert_awaited()


class TestConfirmSubscriptionConditional(unittest.IsolatedAsyncioTestCase):
    async def test_returns_false_when_no_row_updated(self):
        from database import subscriptions as sub_mod
        db = FakeDB(subs=[{"id": "s1", "status": "active", "profile_id": "pOld"}])
        with mock.patch.object(sub_mod, "get_supabase", lambda: db):
            ok = await sub_mod.confirm_subscription("s1", "pNew", "REF", "img")
        self.assertFalse(ok)
        self.assertEqual(db.tables["subscriptions"][0]["profile_id"], "pOld")

    async def test_returns_true_when_pending(self):
        from database import subscriptions as sub_mod
        db = FakeDB(subs=[{"id": "s1", "status": "pending_payment"}])
        with mock.patch.object(sub_mod, "get_supabase", lambda: db):
            ok = await sub_mod.confirm_subscription("s1", "pNew", "REF", "img")
        self.assertTrue(ok)
        self.assertEqual(db.tables["subscriptions"][0]["status"], "active")


class TestExpressJob(unittest.IsolatedAsyncioTestCase):
    def sub(self, status):
        return {"id": "s1", "status": status, "platform_id": "pl", "plan_type": "express",
                "users": {"name": "Cliente Ficticio"}, "platforms": {"name": "Netflix", "icon_emoji": "N"},
                "profiles": {"id": "p1", "profile_name": "Perfil 1", "pin": "1111", "status": "occupied",
                             "accounts": {"email": "cuenta@ejemplo.test", "password": "x"}}}

    def patches(self, rows, rel):
        return [
            mock.patch("database.subscriptions.get_expired_express_subscriptions", new=mock.AsyncMock(return_value=rows)),
            mock.patch("database.subscriptions.expire_subscription", new=mock.AsyncMock(return_value=True)),
            mock.patch("database.profiles.release_profile_for_cut", new=mock.AsyncMock(return_value=rel)),
            mock.patch("services.notification_service.send_express_expired", new=mock.AsyncMock()),
            mock.patch("services.notification_service.notify_express_queue", new=mock.AsyncMock()),
            mock.patch("services.notification_service.send_to_admin", new=mock.AsyncMock()),
            mock.patch("asyncio.sleep", new=mock.AsyncMock()),
            *support.patch_locks(),
        ]

    async def run_job(self, rows, rel):
        from scheduler import jobs
        ps = self.patches(rows, rel)
        ms = [p.start() for p in ps]
        try:
            await jobs.job_express_release()
        finally:
            for p in ps:
                p.stop()
        return ms[:7]  # expired_rows, expire, release, notify_client, queue, admin, sleep

    async def test_expired_express_is_released_once_with_notices(self):
        rel = {"released": True, "reason": None, "profile_name": "Perfil 1", "old_pin": "1111", "new_pin": "2222",
               "account_email": "cuenta@ejemplo.test"}
        _, expire, release, notify_client, queue, admin, _ = await self.run_job([self.sub("expired")], rel)
        expire.assert_not_awaited()
        release.assert_awaited_once_with("p1", exclude_sub_id="s1")
        notify_client.assert_awaited_once_with("s1")
        queue.assert_awaited_once_with("pl")
        self.assertIn("2222", admin.await_args.args[0])

    async def test_expired_express_not_released_if_guard_refuses(self):
        rel = {"released": False, "reason": "otra_suscripcion_activa", "profile_name": "P", "old_pin": "1",
               "new_pin": "—", "account_email": "—"}
        _, expire, release, notify_client, queue, admin, _ = await self.run_job([self.sub("expired")], rel)
        notify_client.assert_not_awaited()
        admin.assert_not_awaited()

    async def test_active_express_keeps_the_original_flow(self):
        from database import get_supabase  # noqa: F401
        db = FakeDB([{"id": "p1", "status": "occupied", "pin": "1111"}])
        with mock.patch("database.get_supabase", lambda: db):
            _, expire, release, notify_client, queue, admin, _ = await self.run_job([self.sub("active")], None)
        expire.assert_awaited_once_with("s1")
        release.assert_not_awaited()
        notify_client.assert_awaited_once_with("s1")
        self.assertEqual(db.tables["profiles"][0]["status"], "available")


class TestPanelPaymentGuards(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from admin_panel import router
        self.router = router
        self.sub = {"id": "s1", "status": "active", "user_id": "u", "platform_id": "pl", "plan_type": "monthly",
                    "users": {"telegram_id": 5}, "platforms": {"name": "Netflix", "icon_emoji": ""}}
        for p in (mock.patch.object(router, "_auth_guard", return_value=None),
                  mock.patch("database.subscriptions.get_subscription_by_id", new=mock.AsyncMock(side_effect=lambda _i: self.sub))):
            p.start()
            self.addCleanup(p.stop)

    async def test_panel_reject_active_does_nothing(self):
        with mock.patch("database.subscriptions.delete_subscription", new=mock.AsyncMock()) as dele:
            resp = await self.router.payment_reject(mock.Mock(), "s1", reason="x")
        dele.assert_not_awaited()
        self.assertIn("error=", resp.headers["location"])

    async def test_panel_reject_missing_does_not_delete(self):
        self.sub = None
        with mock.patch("database.subscriptions.delete_subscription", new=mock.AsyncMock()) as dele:
            resp = await self.router.payment_reject(mock.Mock(), "s1", reason="x")
        dele.assert_not_awaited()
        self.assertIn("error=", resp.headers["location"])

    async def test_panel_approve_active_does_nothing(self):
        with mock.patch("database.subscriptions.confirm_subscription", new=mock.AsyncMock()) as conf, \
             mock.patch("database.profiles.assign_profile", new=mock.AsyncMock()) as asg:
            resp = await self.router.payment_approve(mock.Mock(), "s1", profile_id="p9", payment_reference="R")
        conf.assert_not_awaited()
        asg.assert_not_awaited()
        self.assertIn("error=", resp.headers["location"])

    async def test_panel_approve_loses_race_does_not_assign(self):
        self.sub["status"] = "pending_payment"
        with mock.patch("database.subscriptions.get_user_platform_active_subscription", new=mock.AsyncMock(return_value=None)), \
             mock.patch("database.subscriptions.confirm_subscription", new=mock.AsyncMock(return_value=False)), \
             mock.patch("database.profiles.assign_profile", new=mock.AsyncMock()) as asg:
            resp = await self.router.payment_approve(mock.Mock(), "s1", profile_id="p9", payment_reference="R")
        asg.assert_not_awaited()
        self.assertIn("error=", resp.headers["location"])


if __name__ == "__main__":
    unittest.main()
