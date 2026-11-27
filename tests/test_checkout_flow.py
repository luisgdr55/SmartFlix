"""Pruebas de `services/checkout_flow.py` (O02 Lote 4): armado de ítems, política de orden pendiente y textos."""
from tests import support  # noqa: F401

import unittest
from decimal import Decimal as D

from services import checkout_flow as flow
from services import checkout_service as cs
from tests.test_checkout_service import sub as mk_sub

U = "user-1"
PLAT = {"nf": {"id": "nf", "name": "Netflix", "icon_emoji": "🎬", "monthly_price_usd": 4.5, "express_price_usd": 1.0},
        "hbo": {"id": "hbo", "name": "HBO <Max>", "icon_emoji": "💜", "monthly_price_usd": 5, "express_price_usd": 1.0},
        "sin": {"id": "sin", "name": "SinPrecio", "monthly_price_usd": 0, "express_price_usd": None}}
NAMES = {"nf": "🎬 Netflix", "hbo": "💜 HBO <Max>"}


def msub(sid, platform="nf", **kw):
    return {**mk_sub(sid, platform=platform), "user_id": U, **kw}


class TestItems(unittest.TestCase):
    def test_prices_come_from_the_platform_not_the_cart(self):
        items = flow.purchase_items([{"platform_id": "nf", "plan_type": "monthly", "price_usd": 0.01},
                                     {"platform_id": "nf", "plan_type": "express"}], PLAT)
        self.assertEqual([i["tipo"] for i in items], ["nueva", "nueva"])
        self.assertEqual([i["subtotal_usd"] for i in items], ["4.50", "1.00"])

    def test_missing_price_or_platform_blocks(self):
        for entry in ({"platform_id": "sin", "plan_type": "monthly"}, {"platform_id": "sin", "plan_type": "express"},
                      {"platform_id": "nope", "plan_type": "monthly"}, {"platform_id": "nf", "plan_type": "anual"}):
            with self.assertRaises(cs.CheckoutError, msg=str(entry)):
                flow.purchase_items([entry], PLAT)

    def test_two_services_same_platform_are_two_renewal_items(self):
        subs = {"a": msub("a"), "b": msub("b")}
        items = flow.renewal_items([{"sub_id": "a"}, {"sub_id": "b"}], subs, PLAT, U)
        self.assertEqual([i["subscription_id"] for i in items], ["a", "b"])
        self.assertTrue(all(i["tipo"] == "renovacion" and i["periodos"] == 1 for i in items))
        m = cs.build_manifest(items, "1000")
        self.assertEqual(m["total_usd"], "9.00")

    def test_renewal_rejects_foreign_missing_and_ineligible_subs(self):
        with self.assertRaises(cs.CheckoutError):
            flow.renewal_items([{"sub_id": "a"}], {"a": msub("a", user_id="otro")}, PLAT, U)
        with self.assertRaises(cs.CheckoutError):
            flow.renewal_items([{"sub_id": "zzz"}], {}, PLAT, U)
        with self.assertRaises(cs.CheckoutError):
            flow.renewal_items([{"sub_id": "a"}], {"a": msub("a", status="cancelled")}, PLAT, U)

    def test_duplicate_cart_entry_and_periods_override(self):
        subs = {"a": msub("a")}
        items = flow.renewal_items([{"sub_id": "a"}, {"sub_id": "a"}], subs, PLAT, U, {"a": 2})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["periodos"], 2)

    def test_only_new_items_reserve_a_profile(self):
        subs = {"a": msub("a")}
        items = flow.renewal_items([{"sub_id": "a"}], subs, PLAT, U) + flow.purchase_items(
            [{"platform_id": "hbo", "plan_type": "monthly"}, {"platform_id": "nf", "plan_type": "express"}], PLAT)
        self.assertEqual(flow.reservation_targets(items), [("hbo", "monthly"), ("nf", "express")])
        self.assertEqual(flow.reservation_targets(flow.renewal_items([{"sub_id": "a"}], subs, PLAT, U)), [])


class TestPendingPolicy(unittest.TestCase):
    def test_policy(self):
        self.assertEqual(flow.pending_order_policy(None), "none")
        self.assertEqual(flow.pending_order_policy({"status": "approved"}), "none")
        self.assertEqual(flow.pending_order_policy({"status": "pending"}), "supersede")
        self.assertEqual(flow.pending_order_policy({"status": "pending", "payment_reference": "R1"}), "waiting_review")
        self.assertEqual(flow.pending_order_policy({"status": "pending", "receipt_text": "OCR"}), "waiting_review")
        self.assertFalse(flow.order_has_receipt({"payment_reference": "  ", "receipt_text": ""}))


class TestTexts(unittest.TestCase):
    def order(self):
        subs = {"a": msub("a")}
        items = flow.renewal_items([{"sub_id": "a"}], subs, PLAT, U) + flow.purchase_items([{"platform_id": "hbo", "plan_type": "monthly"}], PLAT)
        return {"id": "11111111-2222-3333-4444-555555555555", "manifest": cs.build_manifest(items, "1000")}

    def test_instructions_one_amount_one_receipt_and_escaping(self):
        o = self.order()
        text = flow.payment_instructions_text(o, NAMES, {"banco": "Banco <Fic>", "telefono": "0414", "cedula": "V-1", "titular": "T"})
        self.assertIn("Bs 9,500.00", text)                 # un solo monto exacto
        self.assertIn("un solo comprobante", text)
        self.assertIn("24 horas", text)
        self.assertIn("30 minutos", text)                  # hay un ítem nueva: se reserva perfil
        self.assertIn("HBO &lt;Max&gt;", text)             # nombres escapados
        self.assertIn("Banco &lt;Fic&gt;", text)
        self.assertIn("#11111111", text)
        self.assertIn("🔄 Renovación", text)
        self.assertIn("🆕", text)

    def test_renewals_only_do_not_mention_reservation(self):
        subs = {"a": msub("a")}
        m = cs.build_manifest(flow.renewal_items([{"sub_id": "a"}], subs, PLAT, U), "1000")
        text = flow.payment_instructions_text({"id": "x" * 8, "manifest": m}, NAMES, {})
        self.assertNotIn("reservan", text)
        self.assertIn("N/A", text)

    def test_admin_ticket_has_items_totals_and_signals_escaped(self):
        t = flow.admin_order_ticket(self.order(), NAMES, "Ana <b>", 42, ["⚠️ Monto: <x>"])
        self.assertIn("Ana &lt;b&gt;", t)
        self.assertIn("⚠️ Monto: &lt;x&gt;", t)
        self.assertIn("Bs 9,500.00", t)
        self.assertIn("decides tú", t)


if __name__ == "__main__":
    unittest.main()
