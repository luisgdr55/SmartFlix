"""Pruebas de `services/locks.py` (O02 Lote 1) con un Redis falso."""
from tests import support  # noqa: F401

import unittest

from services import locks


class FakeRedis:
    def __init__(self, fail=False):
        self.store, self.fail, self.ttls = {}, fail, {}

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("redis caído")
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttls[key] = ex
        return True

    def eval(self, script, numkeys, key, token):
        if self.fail:
            raise ConnectionError("redis caído")
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class TestLocks(unittest.TestCase):
    def test_exclusive_and_ttl(self):
        r = FakeRedis()
        t1 = locks.acquire_lock("job:deuda", 600, r)
        self.assertIsNotNone(t1)
        self.assertEqual(r.ttls["lock:job:deuda"], 600)
        self.assertIsNone(locks.acquire_lock("job:deuda", 600, r))
        self.assertIsNotNone(locks.acquire_lock("job:otro", 60, r))

    def test_only_owner_can_release(self):
        r = FakeRedis()
        t1 = locks.acquire_lock("a", 10, r)
        self.assertFalse(locks.release_lock("a", "token-ajeno", r))
        self.assertIn("lock:a", r.store)
        self.assertTrue(locks.release_lock("a", t1, r))
        self.assertIsNotNone(locks.acquire_lock("a", 10, r))

    def test_expired_lock_taken_by_other_is_not_deleted_by_first_owner(self):
        r = FakeRedis()
        t1 = locks.acquire_lock("a", 10, r)
        del r.store["lock:a"]  # vence el TTL
        t2 = locks.acquire_lock("a", 10, r)
        self.assertFalse(locks.release_lock("a", t1, r))
        self.assertEqual(r.store["lock:a"], t2)

    def test_context_manager_releases_and_reports_busy(self):
        r = FakeRedis()
        with locks.lock("x", 10, r) as tok:
            self.assertIsNotNone(tok)
            with locks.lock("x", 10, r) as tok2:
                self.assertIsNone(tok2)
            self.assertIn("lock:x", r.store)
        self.assertNotIn("lock:x", r.store)

    def test_releases_even_if_body_raises(self):
        r = FakeRedis()
        with self.assertRaises(RuntimeError):
            with locks.lock("x", 10, r):
                raise RuntimeError("falla")
        self.assertNotIn("lock:x", r.store)

    def test_fails_closed_when_redis_down(self):
        r = FakeRedis(fail=True)
        self.assertIsNone(locks.acquire_lock("x", 10, r))
        with locks.lock("x", 10, r) as tok:
            self.assertIsNone(tok)
        self.assertFalse(locks.release_lock("x", "t", r))

    def test_invalid_arguments(self):
        r = FakeRedis()
        for name in ("", "  ", "con espacio"):
            with self.assertRaises(ValueError):
                locks.acquire_lock(name, 10, r)
        with self.assertRaises(ValueError):
            locks.acquire_lock("x", 0, r)


class TestDefaultClient(unittest.TestCase):
    def test_default_client_creates_redis_lazily_instead_of_reading_a_none_global(self):
        """Regresión: el scheduler pide el candado antes de que el bot haya creado el cliente Redis."""
        from unittest import mock
        import bot.middleware as mw
        fake = FakeRedis()
        with mock.patch.object(mw, "_redis_client", None), mock.patch.object(mw, "_get_redis", return_value=fake) as g:
            token = locks.acquire_lock("job:x", 60)
        self.assertTrue(token)
        g.assert_called_once()


if __name__ == "__main__":
    unittest.main()
