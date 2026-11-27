"""Pruebas de O01 Lote 0: seguridad del panel (stdlib `unittest`, sin dependencias nuevas).

Ejecutar:  python -m unittest discover -s tests -t .
Las pruebas que dependen de Supabase, Redis o Telegram reales NO están aquí: se usan
una base falsa y un almacén en memoria.
"""
from tests import support  # noqa: F401  (fija las variables de entorno de prueba primero)

import hashlib
import re
import unittest
from contextlib import ExitStack
from unittest import mock

from types import SimpleNamespace

from tests.support import (
    HOSTILE, PANEL_PASSWORD, WEBHOOK_SECRET, FakeSupabase, attribute_names, fake_request,
    login_csrf_from, patch_database,
)


# ═════════════════════════════════════════════════════════════════════════════
# Secretos de arranque
# ═════════════════════════════════════════════════════════════════════════════
class ConfigTests(unittest.TestCase):
    def _settings(self, **over):
        import config
        base = dict(
            TELEGRAM_BOT_TOKEN="1:x", SUPABASE_URL="https://x.test", SUPABASE_KEY="k", REDIS_URL="redis://x",
            GEMINI_API_KEY="g", APP_URL="https://x.test", WEBHOOK_SECRET="w-secret-a", SECRET_KEY="s-secret-b",
            ADMIN_PANEL_PASSWORD="p-secret-c",
        )
        base.update(over)
        return config.Settings(_env_file=None, **base)

    def test_valid_settings_load(self):
        self.assertEqual(self._settings().SECRET_KEY, "s-secret-b")

    def test_missing_secret_fails_to_start_with_clear_message(self):
        import config
        from pydantic import ValidationError
        args = dict(TELEGRAM_BOT_TOKEN="1:x", SUPABASE_URL="https://x.test", SUPABASE_KEY="k",
                    REDIS_URL="redis://x", GEMINI_API_KEY="g", APP_URL="https://x.test")
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValidationError) as ctx:
                config.Settings(_env_file=None, **args)
        msg = str(ctx.exception)
        for name in ("WEBHOOK_SECRET", "SECRET_KEY", "ADMIN_PANEL_PASSWORD"):
            self.assertIn(name, msg)

    def test_empty_and_blank_secrets_rejected(self):
        from pydantic import ValidationError
        for bad in ("", "   "):
            with self.assertRaises(ValidationError):
                self._settings(SECRET_KEY=bad)

    def test_public_default_is_rejected_without_echoing_the_value(self):
        import config
        from pydantic import ValidationError
        marker = "public-default-for-test"
        digest = hashlib.sha256(marker.encode()).hexdigest()
        with mock.patch.object(config, "_PUBLIC_DEFAULT_DIGESTS", frozenset({digest})):
            self.assertTrue(config.is_public_default(marker))
            with self.assertRaises(ValidationError) as ctx:
                self._settings(ADMIN_PANEL_PASSWORD=marker)
        self.assertNotIn(marker, str(ctx.exception))

    def test_default_check_ignores_case_and_surrounding_spaces(self):
        import config
        marker = "Public-Default-For-Test"
        digest = hashlib.sha256(marker.lower().encode()).hexdigest()
        with mock.patch.object(config, "_PUBLIC_DEFAULT_DIGESTS", frozenset({digest})):
            for variant in (marker, marker.upper(), f"  {marker}  ", marker.lower()):
                self.assertTrue(config.is_public_default(variant), repr(variant))
            self.assertFalse(config.is_public_default(marker + "x"))

    def test_webhook_secret_must_be_a_valid_telegram_secret_token(self):
        from pydantic import ValidationError
        self.assertEqual(self._settings(WEBHOOK_SECRET="abc_DEF-123").WEBHOOK_SECRET, "abc_DEF-123")
        for bad in ("con espacio", "ñandú-secreto", "a.b", "x" * 257):
            with self.assertRaises(ValidationError, msg=bad):
                self._settings(WEBHOOK_SECRET=bad)

    def test_proxy_hops_default_is_one(self):
        self.assertEqual(self._settings().TRUSTED_PROXY_HOPS, 1)

    def test_source_no_longer_contains_default_secret_values(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for rel in ("config.py", "admin_panel/auth.py", "main.py"):
            text = (root / rel).read_text(encoding="utf8")
            self.assertNotRegex(text, r'(SECRET|PASSWORD)[A-Z_]*\s*(:\s*str)?\s*=\s*"[^"]{6,}"', rel)


# ═════════════════════════════════════════════════════════════════════════════
# Sesión y contraseña
# ═════════════════════════════════════════════════════════════════════════════
class AuthTests(unittest.TestCase):
    def test_password_check(self):
        from admin_panel.auth import verify_password
        self.assertTrue(verify_password(PANEL_PASSWORD))
        self.assertFalse(verify_password(PANEL_PASSWORD + "x"))
        self.assertFalse(verify_password(""))

    def test_session_cookie_flags(self):
        from fastapi.responses import Response
        from admin_panel.auth import create_session
        r = Response()
        create_session(r)
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)          # APP_URL de prueba es https
        self.assertIn("samesite=lax", cookie)

    def test_csrf_token_is_bound_to_the_session(self):
        from admin_panel.security import csrf_token_for
        self.assertEqual(csrf_token_for("a"), csrf_token_for("a"))
        self.assertNotEqual(csrf_token_for("a"), csrf_token_for("b"))


# ═════════════════════════════════════════════════════════════════════════════
# Límite de intentos de login (lógica pura, reloj falso)
# ═════════════════════════════════════════════════════════════════════════════
class ThrottleTests(unittest.TestCase):
    def setUp(self):
        from admin_panel import security as sec
        self.sec = sec
        self.t = [1000.0]
        self.th = sec.LoginThrottle(sec._MemoryStore(clock=lambda: self.t[0]))

    def fail(self, ip):
        """Un intento fallido completo, como en `login_post`: contar, verificar (mal), registrar."""
        allowed, wait = self.th.begin_attempt(ip)
        return self.th.register_failure(ip) if allowed else wait

    def test_locks_after_five_failures_and_unlocks_later(self):
        for _ in range(4):
            self.assertEqual(self.fail("1.1.1.1"), 0)
        self.assertEqual(self.th.seconds_locked("1.1.1.1"), 0)
        self.assertEqual(self.fail("1.1.1.1"), self.sec.LOGIN_BASE_LOCK_S)
        self.assertGreater(self.th.seconds_locked("1.1.1.1"), 0)
        self.assertFalse(self.th.begin_attempt("1.1.1.1")[0])          # ni siquiera se verifica la clave
        self.t[0] += self.sec.LOGIN_BASE_LOCK_S + 1
        self.assertEqual(self.th.seconds_locked("1.1.1.1"), 0)
        self.assertTrue(self.th.begin_attempt("1.1.1.1")[0])

    def test_burst_of_concurrent_attempts_cannot_test_more_than_the_limit(self):
        """Antes se contaba DESPUÉS de verificar: N peticiones simultáneas evaluaban N claves."""
        results = [self.th.begin_attempt("8.8.8.8") for _ in range(25)]   # nadie ha registrado un fallo aún
        self.assertEqual(sum(1 for ok, _ in results if ok), self.sec.LOGIN_MAX_FAILS)
        self.assertTrue(all(wait > 0 for ok, wait in results if not ok))
        self.assertGreater(self.th.seconds_locked("8.8.8.8"), 0)

    def test_fifth_attempt_with_the_right_password_still_gets_in(self):
        for _ in range(4):
            self.fail("2.0.0.2")
        allowed, _ = self.th.begin_attempt("2.0.0.2")          # quinto intento
        self.assertTrue(allowed)
        self.th.register_success("2.0.0.2")
        self.assertTrue(self.th.begin_attempt("2.0.0.2")[0])

    def test_lock_doubles_on_repeat_offence_and_is_capped(self):
        sec = self.sec
        expected = sec.LOGIN_BASE_LOCK_S
        for strike in range(1, 9):
            for _ in range(sec.LOGIN_MAX_FAILS):
                lock = self.fail("2.2.2.2")
            self.assertEqual(lock, min(expected, sec.LOGIN_MAX_LOCK_S), f"reincidencia {strike}")
            expected *= 2
            self.t[0] += lock + 1
        self.assertEqual(lock, sec.LOGIN_MAX_LOCK_S)

    def test_success_clears_the_failure_counter(self):
        for _ in range(4):
            self.fail("3.3.3.3")
        self.th.register_success("3.3.3.3")
        self.assertEqual(self.fail("3.3.3.3"), 0)

    def test_ips_are_independent(self):
        for _ in range(5):
            self.fail("4.4.4.4")
        self.assertEqual(self.th.seconds_locked("5.5.5.5"), 0)
        self.assertTrue(self.th.begin_attempt("5.5.5.5")[0])

    def test_global_delay_kicks_in_under_distributed_attack(self):
        self.assertEqual(self.th.global_delay(), 0.0)
        for i in range(self.sec.GLOBAL_SLOWDOWN_FAILS):
            self.fail(f"10.0.0.{i}")
        self.assertGreater(self.th.global_delay(), 0.0)

    def test_failure_window_expires(self):
        for _ in range(4):
            self.fail("6.6.6.6")
        self.t[0] += self.sec.LOGIN_WINDOW_S + 1
        self.assertEqual(self.fail("6.6.6.6"), 0)  # empieza de cero

    def test_falls_back_to_memory_when_redis_is_down(self):
        sec = self.sec

        class Broken:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise ConnectionError("redis caído")
                return boom

        th = sec.LoginThrottle(sec._FallbackStore(Broken(), sec._MemoryStore(clock=lambda: self.t[0])))
        for _ in range(5):
            ok, _w = th.begin_attempt("7.7.7.7")
            if ok:
                th.register_failure("7.7.7.7")
        self.assertGreater(th.seconds_locked("7.7.7.7"), 0)   # sigue limitando sin Redis

    def test_redis_counters_always_get_a_ttl(self):
        """INCR + EXPIRE separados dejaban un contador eterno si fallaba el segundo paso."""
        calls = []

        class R:
            def set(self, key, val, ex=None, nx=False):
                calls.append(("set", key, ex, nx))

            def incr(self, key):
                calls.append(("incr", key))
                return 1

        self.sec._RedisStore(R()).incr("k", 60)
        self.assertEqual(calls[0], ("set", "k", 60, True))   # nace con TTL
        self.assertEqual(calls[1], ("incr", "k"))

    def test_client_ip_uses_the_trusted_hop_from_the_end(self):
        from types import SimpleNamespace
        sec = self.sec
        req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 9.9.9.9"}, client=SimpleNamespace(host="127.0.0.1"))
        self.assertEqual(sec.client_ip(req), "9.9.9.9")        # 1 proxy: lo que añadió Railway
        with mock.patch.object(sec, "trusted_proxy_hops", return_value=2):
            self.assertEqual(sec.client_ip(req), "6.6.6.6")    # 2 proxies (p. ej. un CDN delante)
        with mock.patch.object(sec, "trusted_proxy_hops", return_value=5):
            self.assertEqual(sec.client_ip(req), "6.6.6.6")    # cabecera más corta que los saltos
        req2 = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
        self.assertEqual(sec.client_ip(req2), "127.0.0.1")


# ═════════════════════════════════════════════════════════════════════════════
# HTTP: login, CSRF, métodos, cabeceras, documentación
# ═════════════════════════════════════════════════════════════════════════════
class PanelHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient
        import main
        cls.main = main
        cls.TestClient = TestClient

    def setUp(self):
        from admin_panel import security as sec
        sec._throttle = sec.LoginThrottle(sec._MemoryStore())     # sin Redis y sin estado compartido
        self.client = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def login(self, password=PANEL_PASSWORD):
        page = self.client.get("/panel/login")
        token = login_csrf_from(page.text)
        return self.client.post("/panel/login", data={"password": password, "csrf_token": token})

    def csrf(self):
        from admin_panel.security import csrf_token_for
        return csrf_token_for(self.client.cookies.get("sv_admin_session"))

    # ── docs cerradas ────────────────────────────────────────────────────────
    def test_api_docs_are_closed_in_production(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    # ── cabeceras ────────────────────────────────────────────────────────────
    def test_security_headers_on_panel(self):
        r = self.client.get("/panel/login")
        h = {k.lower(): v for k, v in r.headers.items()}
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertIn("strict-transport-security", h)
        self.assertIn("frame-ancestors 'none'", h["content-security-policy-report-only"])
        self.assertNotIn("content-security-policy", h)          # solo Report-Only por ahora

    def test_health_has_base_headers_but_is_not_no_store(self):
        r = self.client.get("/health")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertNotEqual(r.headers.get("cache-control"), "no-store")

    # ── login ────────────────────────────────────────────────────────────────
    def test_login_ok_sets_secure_httponly_cookie(self):
        r = self.login()
        self.assertEqual(r.status_code, 302)
        sc = r.headers["set-cookie"].lower()
        self.assertIn("sv_admin_session", sc)
        self.assertIn("httponly", sc)
        self.assertIn("secure", sc)

    def test_login_wrong_password_is_401_and_sets_no_session(self):
        r = self.login("incorrecta")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Contraseña incorrecta", r.text)
        self.assertNotIn("sv_admin_session", r.headers.get("set-cookie", ""))

    def test_login_without_csrf_token_is_rejected_even_with_right_password(self):
        r = self.client.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("sv_admin_session", r.headers.get("set-cookie", ""))

    def test_burst_after_global_slowdown_still_respects_the_limit(self):
        """Con la demora global activa, N peticiones simultáneas no pueden probar N claves."""
        import asyncio
        from admin_panel import security as sec
        verified = []
        from admin_panel import router as r
        real_verify = r.verify_password
        r.verify_password = lambda pw: (verified.append(pw), False)[1]
        self.addCleanup(lambda: setattr(r, "verify_password", real_verify))
        for i in range(sec.GLOBAL_SLOWDOWN_FAILS):                       # activa la demora global
            sec.get_throttle().register_failure(f"10.1.0.{i}")
        self.assertGreater(sec.get_throttle().global_delay(), 0)
        with mock.patch.object(sec, "GLOBAL_SLOWDOWN_S", 0.05):
            page = self.client.get("/panel/login")
            token = login_csrf_from(page.text)

            async def burst():
                import httpx
                transport = httpx.ASGITransport(app=self.main.app)
                async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as ac:
                    ac.cookies.set("sv_login_csrf", token, path="/panel/login")
                    return await asyncio.gather(*[
                        ac.post("/panel/login", data={"password": f"mal{i}", "csrf_token": token}) for i in range(30)
                    ])
            responses = asyncio.run(burst())
        self.assertLessEqual(len(verified), sec.LOGIN_MAX_FAILS)
        self.assertGreaterEqual(sum(1 for x in responses if x.status_code == 429), 25 - 5)

    def test_lockout_after_repeated_failures_even_with_right_password(self):
        for _ in range(5):
            self.login("mal")
        r = self.login(PANEL_PASSWORD)
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        self.assertIn("minuto", r.text)                       # le dice cuánto esperar
        self.assertIn("administra el servidor", r.text)       # y le ofrece una salida
        self.assertNotIn("sv_admin_session", r.headers.get("set-cookie", ""))

    # ── métodos que antes mutaban por GET ────────────────────────────────────
    def test_get_logout_and_get_rate_fetch_are_405(self):
        self.login()
        self.assertEqual(self.client.get("/panel/logout").status_code, 405)
        self.assertEqual(self.client.get("/panel/api/rate/fetch").status_code, 405)

    # ── CSRF ─────────────────────────────────────────────────────────────────
    def test_post_without_token_is_403_html_with_exit(self):
        self.login()
        r = self.client.post("/panel/logout")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Volver al panel", r.text)
        self.assertIn("No se hizo ningún cambio", r.text)

    def test_json_post_without_token_is_403_json(self):
        self.login()
        r = self.client.post("/panel/api/rate/fetch", headers={"accept": "application/json"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()["success"])

    def test_non_ascii_csrf_token_is_rejected_cleanly_not_a_500(self):
        """compare_digest con str no ASCII lanzaba TypeError y daba HTTP 500."""
        self.login()
        r1 = self.client.post("/panel/logout", headers={"X-CSRF-Token": "é".encode("latin-1")})
        r2 = self.client.post("/panel/logout", data={"csrf_token": "é"})
        self.assertEqual((r1.status_code, r2.status_code), (403, 403))
        # y sin sesión (cliente nuevo), también en el login
        fresh = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        page = fresh.get("/panel/login")
        r3 = fresh.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": "é"})
        self.assertEqual(r3.status_code, 400)
        r4 = fresh.post("/panel/accounts/new", data={"csrf_token": "é", "platform_id": "x", "email": "a", "password": "b"})
        self.assertEqual(r4.status_code, 403)

    def test_token_from_another_session_is_rejected(self):
        self.login()
        r = self.client.post("/panel/logout", headers={"X-CSRF-Token": "0" * 64})
        self.assertEqual(r.status_code, 403)

    def test_logout_with_valid_token_clears_cookie(self):
        self.login()
        r = self.client.post("/panel/logout", headers={"X-CSRF-Token": self.csrf()})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/panel/login")

    def test_form_field_token_is_accepted(self):
        self.login()
        r = self.client.post("/panel/logout", data={"csrf_token": self.csrf()})
        self.assertEqual(r.status_code, 303)

    def test_pages_expose_the_token_to_forms_and_fetch(self):
        self.login()
        for p in patch_database(FakeSupabase(data=[], count=0)):
            self.stack.enter_context(p)
        r = self.client.get("/panel/users")
        self.assertEqual(r.status_code, 200)
        token = self.csrf()
        self.assertIn(f'<meta name="csrf-token" content="{token}"', r.text)
        self.assertIn(f'name="csrf_token" value="{token}"', r.text)      # formulario de cerrar sesión

    # ── errores y entradas ───────────────────────────────────────────────────
    def test_internal_error_detail_never_reaches_the_user(self):
        self.login()
        secret_detail = "SECRET-INTERNAL-DETAIL-xyz"
        for p in patch_database(FakeSupabase(raises=RuntimeError(secret_detail))):
            self.stack.enter_context(p)
        r = self.client.post(
            "/panel/platforms/00000000-0000-0000-0000-000000000001/edit",
            data={"name": "N", "slug": "s", "csrf_token": self.csrf()},
        )
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(secret_detail, r.headers["location"])
        self.assertIn("digo", r.headers["location"].replace("%C3%B3", "ó"))   # "código"

    def test_invalid_amount_is_rejected_with_a_human_message(self):
        self.login()
        for p in patch_database(FakeSupabase(data=[{"id": "x"}])):
            self.stack.enter_context(p)
        r = self.client.post(
            "/panel/platforms/00000000-0000-0000-0000-000000000001/edit",
            data={"name": "N", "slug": "s", "monthly_price_usd": "nan", "csrf_token": self.csrf()},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("precio+mensual", r.headers["location"].replace("%20", "+"))

    def test_rate_rejects_zero_and_nan(self):
        self.login()
        for bad in ("0", "nan", "inf", "-5", "abc"):
            r = self.client.post("/panel/settings/rate", data={"usd_binance": bad, "csrf_token": self.csrf()})
            self.assertEqual(r.status_code, 302, bad)
            self.assertIn("error=", r.headers["location"], bad)
            self.assertNotIn("success", r.headers["location"], bad)

    def test_invalid_page_warns_instead_of_showing_a_silent_empty_list(self):
        self.login()
        for p in patch_database(FakeSupabase(data=[], count=0)):
            self.stack.enter_context(p)
        r = self.client.get("/panel/users?page=abc")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no es válido", r.text)

    def test_database_failure_on_a_list_is_reported_not_hidden(self):
        self.login()
        for p in patch_database(FakeSupabase(raises=RuntimeError("boom-internal"))):
            self.stack.enter_context(p)
        r = self.client.get("/panel/users")
        self.assertIn("No se pudo completar la acción", r.text)
        self.assertNotIn("boom-internal", r.text)

    # ── webhook ──────────────────────────────────────────────────────────────
    def test_webhook_secret_is_checked(self):
        self.assertEqual(self.client.post("/webhook", json={}).status_code, 403)
        bad = self.client.post("/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "otro"})
        self.assertEqual(bad.status_code, 403)
        ok = self.client.post("/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})
        self.assertNotEqual(ok.status_code, 403)       # pasa la verificación (después falla porque no hay bot)

    # ── credenciales bajo demanda ────────────────────────────────────────────
    def test_credentials_endpoint_requires_session_and_valid_id(self):
        sid = "00000000-0000-0000-0000-0000000000aa"
        r = self.client.post(f"/panel/subscriptions/{sid}/credentials", headers={"X-CSRF-Token": "x"})
        self.assertEqual(r.status_code, 403)               # sin sesión no hay token válido
        self.login()
        r = self.client.post("/panel/subscriptions/no-es-uuid/credentials", headers={"X-CSRF-Token": self.csrf()})
        self.assertEqual(r.status_code, 404)

    def test_credentials_are_returned_no_store_and_audited(self):
        self.login()
        row = {"id": "1", "platforms": {"name": "Netflix", "icon_emoji": "🎬"},
               "profiles": {"profile_name": "P1", "pin": "1234",
                            "accounts": {"email": "cuenta@ejemplo.test", "password": "pw-de-prueba"}}}
        for p in patch_database(FakeSupabase(data=[row])):
            self.stack.enter_context(p)
        audit = self.stack.enter_context(mock.patch("database.users.log_admin_action", new=mock.AsyncMock(return_value=True)))
        r = self.client.post("/panel/subscriptions/00000000-0000-0000-0000-0000000000aa/credentials",
                             headers={"X-CSRF-Token": self.csrf()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        data = r.json()
        self.assertEqual((data["email"], data["password"], data["pin"]), ("cuenta@ejemplo.test", "pw-de-prueba", "1234"))
        audit.assert_awaited_once()
        self.assertEqual(audit.await_args.args[1], "panel:reveal_credentials")


class ProfileSecretsTests(unittest.TestCase):
    """PIN y contraseña del cupo: no van en el HTML y editar sin escribirlos los conserva."""

    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient
        import main
        cls.main, cls.TestClient = main, TestClient

    def setUp(self):
        from admin_panel import security as sec
        sec._throttle = sec.LoginThrottle(sec._MemoryStore())
        self.client = self.TestClient(self.main.app, base_url="https://testserver", follow_redirects=False)
        page = self.client.get("/panel/login")
        self.client.post("/panel/login", data={"password": PANEL_PASSWORD, "csrf_token": login_csrf_from(page.text)})
        from admin_panel.security import csrf_token_for
        self.csrf = csrf_token_for(self.client.cookies.get("sv_admin_session"))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def recording_db(self):
        updates = []

        class Q:
            def __init__(self, name):
                self.name = name

            def update(self, data):
                updates.append((self.name, dict(data)))
                return self

            def __getattr__(self, n):
                return lambda *a, **k: self

            def execute(self):
                return SimpleNamespace(data=[], count=0)

        class SB:
            def table(self, name):
                return Q(name)

        for p in patch_database(SB()):
            self.stack.enter_context(p)
        return updates

    def post_edit(self, **form):
        data = {"platform_id": "p", "profile_name": "Perfil", "csrf_token": self.csrf}
        data.update(form)
        return self.client.post("/panel/profiles/00000000-0000-0000-0000-000000000001/edit", data=data)

    def test_blank_pin_and_password_are_kept_not_erased(self):
        updates = self.recording_db()
        self.assertEqual(self.post_edit(pin="", extra_password="").status_code, 302)
        data = [u for t, u in updates if t == "profiles"][0]
        self.assertNotIn("pin", data)
        self.assertNotIn("extra_password", data)

    def test_new_values_are_saved_and_dash_clears(self):
        updates = self.recording_db()
        self.post_edit(pin="4321", extra_password="-")
        data = [u for t, u in updates if t == "profiles"][0]
        self.assertEqual(data["pin"], "4321")
        self.assertIsNone(data["extra_password"])

    def test_profile_credentials_endpoint(self):
        row = {"id": "1", "pin": "4321", "is_extra_member": True, "extra_email": "x@ejemplo.test", "extra_password": "pw-cupo"}
        for p in patch_database(FakeSupabase(data=[row])):
            self.stack.enter_context(p)
        audit = self.stack.enter_context(mock.patch("database.users.log_admin_action", new=mock.AsyncMock(return_value=True)))
        url = "/panel/profiles/00000000-0000-0000-0000-000000000001/credentials"
        self.assertEqual(self.client.post(url).status_code, 403)                      # sin CSRF
        r = self.client.post(url, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual((r.json()["pin"], r.json()["extra_password"]), ("4321", "pw-cupo"))
        audit.assert_awaited_once()
        bad = self.client.post("/panel/profiles/no-uuid/credentials", headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(bad.status_code, 404)

    def test_failed_audit_is_logged_loudly_but_does_not_block(self):
        row = {"id": "1", "platforms": {}, "profiles": {"pin": "1", "accounts": {"email": "e", "password": "p"}}}
        for p in patch_database(FakeSupabase(data=[row])):
            self.stack.enter_context(p)
        self.stack.enter_context(mock.patch("database.users.log_admin_action", new=mock.AsyncMock(return_value=False)))
        logging_ = self.stack.enter_context(mock.patch("admin_panel.router.logger"))
        r = self.client.post("/panel/subscriptions/00000000-0000-0000-0000-0000000000aa/credentials",
                             headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("AUDITORÍA NO REGISTRADA" in str(c) for c in logging_.error.call_args_list))


# ═════════════════════════════════════════════════════════════════════════════
# Plantillas con datos hostiles y sin credenciales incrustadas
# ═════════════════════════════════════════════════════════════════════════════
class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from admin_panel.router import templates
        cls.env = templates.env

    def render(self, name, **ctx):
        base = dict(request=fake_request(), page="x", success=None, error=None)
        base.update(ctx)
        return self.env.get_template(name).render(**base)

    def _sub(self):
        return {
            "id": "00000000-0000-0000-0000-000000000011", "plan_type": "monthly", "status": "active",
            "start_date": "2026-10-01", "end_date": "2026-12-01", "price_usd": 5, "platform_id": "p1",
            "profile_id": "pf1",
            "users": {"name": HOSTILE, "username": HOSTILE},
            "platforms": {"name": HOSTILE, "icon_emoji": "🎬", "slug": "x"},
            "profiles": {"profile_name": HOSTILE, "pin": "PIN-MARCA-7391",
                         "accounts": {"email": "mail-secreto@ejemplo.test", "password": "CLAVE-SECRETA-123"}},
        }

    def assertNoInjection(self, html):
        names = attribute_names(html)
        for bad in ("x-init", "onerror", "z"):
            self.assertNotIn(bad, names, f"atributo inyectado: {bad}")
        self.assertNotIn("<img src=x", html)

    def test_subscriptions_page_is_safe_and_has_no_credentials(self):
        html = self.render("subscriptions.html", subscriptions=[self._sub()], platforms=[], total=1,
                           current_page=1, total_pages=1, per_page=25, status_filter=HOSTILE,
                           platform_filter="", plan_filter="")
        self.assertNoInjection(html)
        self.assertNotIn("CLAVE-SECRETA-123", html)
        self.assertNotIn("mail-secreto@ejemplo.test", html)
        self.assertNotIn("PIN-MARCA-7391", html)
        self.assertIn("/credentials", html)                       # se piden bajo demanda
        self.assertIn('data-sub-id="00000000-0000-0000-0000-000000000011"', html)

    def test_subscriptions_modal_lives_inside_its_x_data(self):
        """Antes el modal quedaba FUERA del x-data y x-show no funcionaba."""
        from html.parser import HTMLParser
        html = self.render("subscriptions.html", subscriptions=[], platforms=[], total=0, current_page=1,
                           total_pages=0, per_page=25, status_filter="", platform_filter="", plan_filter="")
        void = {"input", "meta", "link", "br", "hr", "img", "source", "area", "base", "col", "embed", "wbr"}
        found = {"modal": False, "inside": False}

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.stack = []

            def handle_starttag(self, tag, attrs):
                d = dict(attrs)
                if d.get("x-show") == "show":
                    found["modal"] = True
                    found["inside"] = any(a.get("x-data") == "subModal()" for _, a in self.stack)
                if tag not in void:
                    self.stack.append((tag, d))

            def handle_endtag(self, tag):
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i][0] == tag:
                        del self.stack[i:]
                        break

        P().feed(html)
        self.assertTrue(found["modal"], "no se encontró el modal")
        self.assertTrue(found["inside"], "el modal debe estar dentro de x-data=subModal()")

    def test_user_detail_is_safe_and_has_no_credentials(self):
        user = {"id": "u1", "name": HOSTILE, "username": HOSTILE, "telegram_id": 1, "phone": "", "status": "active",
                "total_purchases": 1, "created_at": "2026-01-01", "last_seen": "2026-01-01", "receives_promos": True,
                "notes": ""}
        html = self.render("user_detail.html", user=user, subscriptions=[self._sub()], platforms=[],
                           now_date="2026-11-27")
        self.assertNoInjection(html)
        self.assertNotIn("CLAVE-SECRETA-123", html)
        self.assertNotIn("mail-secreto@ejemplo.test", html)
        self.assertIn('data-sub-id="00000000-0000-0000-0000-000000000011"', html)

    def test_users_list_confirm_uses_data_attribute_not_inline_name(self):
        html = self.render("users.html", users=[{"id": "u1", "name": HOSTILE, "username": "x", "status": "active",
                                                  "total_purchases": 1, "created_at": "2026-01-01",
                                                  "last_seen": "2026-01-01"}],
                           total=1, current_page=1, total_pages=1, per_page=20, search=HOSTILE)
        self.assertNoInjection(html)
        self.assertIn("this.dataset.name", html)
        self.assertNotRegex(html, r"confirm\('¿Eliminar a [^']*Ana")

    def test_account_edit_form_never_echoes_the_password(self):
        acct = {"id": "a1", "platform_id": "p1", "email": "x@ejemplo.test", "password": "CLAVE-SECRETA-123",
                "status": "active", "cost_usd_monthly": 0, "billing_date": ""}
        html = self.render("account_form.html", account=acct, platforms=[], form_action="/panel/accounts/a1/edit",
                           form_title="Editar Cuenta")
        self.assertNotIn("CLAVE-SECRETA-123", html)
        self.assertIn("Dejar vacío para conservar", html)

    def test_profiles_list_does_not_embed_pins(self):
        prof = {"id": "00000000-0000-0000-0000-0000000000f1", "profile_name": "P", "pin": "PIN-MARCA-7391", "profile_type": "monthly",
                "status": "available", "is_extra_member": False, "accounts": {"email": "a@ejemplo.test"}}
        html = self.render("profiles.html", profiles=prof and [prof], platforms=[], total=1)
        self.assertNotIn("PIN-MARCA-7391", html)
        self.assertIn('data-id="00000000-0000-0000-0000-0000000000f1"', html)
        self.assertIn("pinReveal()", html)

    def test_profile_edit_form_does_not_prefill_secrets(self):
        prof = {"id": "p1", "profile_name": "P", "pin": "PIN-MARCA-7391", "extra_password": "CLAVE-CUPO-123",
                "extra_email": "x@ejemplo.test", "is_extra_member": True, "platform_id": "p", "profile_type": "monthly",
                "status": "available", "account_id": "a"}
        html = self.render("profile_form.html", profile=prof, platforms=[], accounts=[], form_action="/panel/profiles/p1/edit",
                           form_title="Editar Perfil")
        self.assertNotIn("PIN-MARCA-7391", html)
        self.assertNotIn("CLAVE-CUPO-123", html)

    def test_alpine_methods_never_read_dataset_from_this_el(self):
        """Dentro de un método de x-data, `this.$el` es el elemento que dispara el evento (p. ej. el
        botón), no la raíz del componente: así se pedía /profiles/undefined/credentials. Los datos
        de la raíz se copian en init()."""
        import glob
        import os
        tdir = os.path.join(os.path.dirname(support.__file__), "..", "admin_panel", "templates")
        for path in glob.glob(os.path.join(tdir, "*.html")):
            with open(path, encoding="utf8") as fh:
                src = fh.read()
            for m in re.finditer(r"this\.\$el\.dataset", src):
                line = src[:m.start()].count("\n") + 1
                names = re.findall(r"(\w+)\s*\([^)]*\)\s*\{", src[:m.start()])   # método que lo contiene
                self.assertEqual(names[-1] if names else None, "init",
                                 f"{os.path.basename(path)}:{line} usa this.$el.dataset fuera de init()")

    def test_fetch_patch_does_not_send_the_token_to_protocol_relative_urls(self):
        html = self.render("base.html")
        self.assertIn("url.charAt(1) !== '/'", html)

    def test_dashboard_buttons_do_not_interpolate_ids_into_javascript(self):
        import pathlib
        src = (pathlib.Path(support.__file__).resolve().parent.parent / "admin_panel" / "templates" / "dashboard.html").read_text(encoding="utf8")
        self.assertNotRegex(src, r"fetch\('/panel/subscriptions/\{\{")

    def test_list_queries_no_longer_select_account_passwords(self):
        import pathlib
        src = (pathlib.Path(support.__file__).resolve().parent.parent / "admin_panel" / "router.py").read_text(encoding="utf8")
        self.assertEqual(src.count("accounts(email, password)"), 1)       # solo el endpoint bajo demanda

    def test_every_post_form_carries_a_csrf_field(self):
        import glob
        import os
        from html.parser import HTMLParser
        tdir = os.path.join(os.path.dirname(support.__file__), "..", "admin_panel", "templates")
        for path in glob.glob(os.path.join(tdir, "*.html")):
            name = os.path.basename(path)
            if name == "login.html":
                continue
            src = open(path, encoding="utf8").read()
            posts = []

            class P(HTMLParser):
                def handle_starttag(self, tag, attrs):
                    if tag == "form" and dict(attrs).get("method", "get").lower() == "post":
                        posts.append(1)
            P(convert_charrefs=False).feed(src)
            self.assertEqual(len(posts), src.count('name="csrf_token"'), name)


# ═════════════════════════════════════════════════════════════════════════════
# Validación de entradas
# ═════════════════════════════════════════════════════════════════════════════
class InputTests(unittest.TestCase):
    def setUp(self):
        from admin_panel import router
        self.r = router

    def test_amounts(self):
        r = self.r
        self.assertEqual(r._parse_amount("1,5", "x"), 1.5)
        self.assertEqual(r._parse_amount("", "x", default=7), 7)
        self.assertEqual(r._parse_amount(None, "x"), 0.0)
        for bad in ("abc", "-1", "nan", "inf", "1e999", "100001"):
            with self.assertRaises(r.InputError, msg=bad):
                r._parse_amount(bad, "El precio")
        with self.assertRaises(r.InputError):
            r._parse_amount("0", "La tasa", positive=True)

    def test_positive_int(self):
        r = self.r
        self.assertEqual(r._parse_positive_int("123", "x"), 123)
        for bad in ("0", "-3", "1.5", "abc", ""):
            with self.assertRaises(r.InputError, msg=bad):
                r._parse_positive_int(bad, "El Telegram ID")

    def test_page(self):
        from types import SimpleNamespace
        r = self.r
        mk = lambda v: SimpleNamespace(query_params={"page": v})
        self.assertEqual(r._parse_page(mk("3")), (3, None))
        for bad in ("abc", "0", "-1", "999999999"):
            page, err = r._parse_page(mk(bad))
            self.assertEqual(page, 1)
            self.assertIn("no es válido", err)

    def test_search_filter_chars_are_removed(self):
        r = self.r
        cleaned = r._clean_search('a,b(c)*d%e"f\\g')
        for ch in ',()*%"\\':
            self.assertNotIn(ch, cleaned)

    def test_public_error_hides_detail_but_keeps_input_messages(self):
        r = self.r
        self.assertNotIn("detalle-interno", r._public_error(RuntimeError("detalle-interno")))
        self.assertEqual(r._public_error(r.InputError("El precio no es un número válido")),
                         "El precio no es un número válido")


if __name__ == "__main__":
    unittest.main()
