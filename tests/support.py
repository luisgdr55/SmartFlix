"""Soporte común de las pruebas: variables de entorno ficticias y una base falsa.

Solo valores de prueba; nada de producción. Importar este módulo ANTES que `config`.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace
from unittest import mock

TEST_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:fake-token",
    "SUPABASE_URL": "https://example.supabase.test",
    "SUPABASE_KEY": "fake-key",
    "REDIS_URL": "redis://localhost:6399/0",
    "GEMINI_API_KEY": "fake-gemini",
    "APP_URL": "https://example.test",
    "WEBHOOK_SECRET": "unit-test-webhook-secret",
    "SECRET_KEY": "unit-test-session-signing-key",
    "ADMIN_PANEL_PASSWORD": "unit-test-panel-password",
}
for _k, _v in TEST_ENV.items():
    os.environ[_k] = _v

PANEL_PASSWORD = TEST_ENV["ADMIN_PANEL_PASSWORD"]
WEBHOOK_SECRET = TEST_ENV["WEBHOOK_SECRET"]

# Texto hostil: intenta cerrar el atributo, inyectar uno nuevo y cerrar un <script>.
HOSTILE = 'Ana" x-init="window.__pwned=1" z=\' ); alert(1); // </script><img src=x onerror=alert(2)>'


class FakeQuery:
    """Imita la cadena de supabase-py: cualquier método devuelve self; execute() da los datos."""

    def __init__(self, data=None, count=None, raises=None):
        self._data, self._count, self._raises = data if data is not None else [], count, raises

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: self

    @property
    def not_(self):
        return self

    def execute(self):
        if self._raises:
            raise self._raises
        return SimpleNamespace(data=self._data, count=self._count)


class FakeSupabase:
    def __init__(self, data=None, count=None, raises=None):
        self.kw = dict(data=data, count=count, raises=raises)

    def table(self, name):
        return FakeQuery(**self.kw)


def patch_database(fake):
    """Sustituye get_supabase en `database` y en todos los módulos que lo importaron."""
    import importlib
    import sys

    importlib.import_module("database")
    patches = []
    for name, mod in list(sys.modules.items()):
        if (name == "database" or name.startswith("database.")) and hasattr(mod, "get_supabase"):
            patches.append(mock.patch.object(mod, "get_supabase", lambda: fake))
    return patches


def login_csrf_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "el formulario de login no trae token"
    return m.group(1)


def fake_request():
    return SimpleNamespace(state=SimpleNamespace(csrf_token="tok-test"), url=SimpleNamespace(path="/panel/x"),
                           query_params={})


def attribute_names(html: str) -> set[str]:
    from html.parser import HTMLParser

    names: set[str] = set()

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            names.update(n for n, _ in attrs)

        handle_startendtag = handle_starttag

    P(convert_charrefs=False).feed(html)
    return names
