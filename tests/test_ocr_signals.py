"""Pruebas de `services/ocr_signals.py` (O02 Lote 4): señales del comprobante; solo marcan."""
import unittest
from datetime import datetime
from decimal import Decimal as D

import pytz

from services import ocr_signals as sig
from utils.dates import CARACAS

CFG = {"telefono": "0414-1234567", "cedula": "V-12345678", "banco": "Banco Ficticio", "titular": "Titular Ficticio"}
CREATED = CARACAS.localize(datetime(2026, 12, 1, 10, 0))


def good(**over):
    base = {"is_comprobante_valido": True, "referencia": "00123456", "fecha": "01/12/2026", "monto": "1250.50",
            "celular_destino": "04141234567", "cedula_receptor": "V12345678", "confianza": "alta"}
    base.update(over)
    return base


def ev(extracted, **kw):
    kw.setdefault("total_bs", "1250.50")
    kw.setdefault("payment_cfg", CFG)
    kw.setdefault("order_created_at", CREATED)
    return sig.evaluate_signals(extracted, **kw)


class TestParseAmount(unittest.TestCase):
    def test_formats(self):
        cases = {"1250.50": "1250.50", "1.250,50": "1250.50", "1,250.50": "1250.50", "Bs 1.250,50": "1250.50",
                 "1250": "1250", "1.250": "1250", "1,250": "1250", "12,5": "12.5", "0,50": "0.50",
                 "1.250.500": "1250500", " 99.99 ": "99.99"}
        for raw, exp in cases.items():
            self.assertEqual(sig.parse_amount_bs(raw), D(exp), raw)

    def test_invalid(self):
        for raw in (None, "", "abc", "Bs", True, "..", ","):
            self.assertIsNone(sig.parse_amount_bs(raw), repr(raw))
        self.assertEqual(sig.parse_amount_bs(D("10.5")), D("10.5"))
        self.assertEqual(sig.parse_amount_bs(1250.5), D("1250.5"))


class TestSignals(unittest.TestCase):
    def test_everything_matches(self):
        f = ev(good())
        for k in ("monto", "telefono", "cedula", "referencia", "fecha"):
            self.assertEqual(f[k]["nivel"], "ok", k)
        self.assertEqual(f["alertas"], [])
        self.assertFalse(f["imagen_repetida"])

    def test_amount_tolerance_and_mismatch(self):
        self.assertEqual(ev(good(monto="1250.00"))["monto"]["nivel"], "ok")          # dentro de 0,50
        f = ev(good(monto="1000"))
        self.assertEqual(f["monto"]["nivel"], "warn")
        self.assertTrue(any("Monto" in a for a in f["alertas"]))
        self.assertEqual(ev(good(monto=None))["monto"]["nivel"], "unknown")
        self.assertEqual(ev(good(monto="ilegible"))["monto"]["nivel"], "unknown")

    def test_recipient_phone_and_id(self):
        self.assertEqual(ev(good(celular_destino="+58 414 123 4567"))["telefono"]["nivel"], "ok")  # prefijo +58
        self.assertEqual(ev(good(celular_destino="04149999999"))["telefono"]["nivel"], "warn")
        self.assertEqual(ev(good(celular_destino=None))["telefono"]["nivel"], "unknown")
        self.assertEqual(ev(good(cedula_receptor="V-99999999"))["cedula"]["nivel"], "warn")
        self.assertEqual(ev(good(cedula_receptor="12.345.678"))["cedula"]["nivel"], "ok")
        # sin configuración de cobro no se puede comparar: no es alarma
        f = ev(good(), payment_cfg=None)
        self.assertEqual((f["telefono"]["nivel"], f["cedula"]["nivel"]), ("unknown", "unknown"))
        self.assertEqual(f["alertas"], [])

    def test_repeated_reference_and_image(self):
        f = ev(good(), reference_seen=True, image_duplicate=True)
        self.assertEqual(f["referencia"]["nivel"], "warn")
        self.assertTrue(f["imagen_repetida"])
        self.assertEqual(len(f["alertas"]), 2)
        self.assertEqual(ev(good(referencia=""))["referencia"]["nivel"], "unknown")

    def test_date_before_the_order_is_flagged(self):
        self.assertEqual(ev(good(fecha="30/11/2026"))["fecha"]["nivel"], "warn")
        self.assertEqual(ev(good(fecha="02/12/2026"))["fecha"]["nivel"], "ok")
        self.assertEqual(ev(good(fecha="mañana"))["fecha"]["nivel"], "unknown")

    def test_ocr_unavailable_or_not_a_receipt_never_blocks(self):
        for ext, state in ((None, "unavailable"), ({"error": "x", "is_comprobante_valido": None}, "unavailable"),
                           ({"is_comprobante_valido": False}, "not_receipt")):
            f = ev(ext)
            self.assertEqual(f["ocr"], state)
            self.assertEqual(f["monto"]["nivel"], "unknown")
            self.assertTrue(f["alertas"])

    def test_only_marks_never_decides(self):
        # La salida no contiene ninguna clave de decisión.
        f = ev(good(monto="1"), reference_seen=True)
        self.assertFalse({"aprobado", "rechazado", "valid", "approved", "rejected"} & set(f))

    def test_flags_are_json_serializable_and_summarized(self):
        import json
        f = ev(good(monto="1000"), image_duplicate=True)
        json.dumps(f)
        lines = sig.summary_lines(f)
        self.assertTrue(any(l.startswith("⚠️ Monto") and "1000" in l for l in lines))
        self.assertTrue(any("Imagen repetida" in l for l in lines))
        self.assertEqual(sig.summary_lines({}), ["❔ Sin señales"])


if __name__ == "__main__":
    unittest.main()
