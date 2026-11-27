"""Pruebas de `services/checkout_service.py` (O02 Lote 1): dinero con Decimal, mora y periodos,
manifiesto, transiciones de la orden. Lógica pura, datos ficticios."""
from tests import support  # noqa: F401

import json
import random
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

import pytz

from services import checkout_service as cs
from utils.dates import CARACAS

D = Decimal


def caracas(y, m, d, hh=0, mm=0, ss=0):
    return CARACAS.localize(datetime(y, m, d, hh, mm, ss))


def sub(sid="s1", status="active", end="2026-05-20T03:59:59+00:00", platform="netflix", plan="monthly"):
    """`end` por defecto = 19/05/2026 23:59:59 en Caracas guardado en UTC."""
    return {"id": sid, "status": status, "end_date": end, "platform_id": platform, "plan_type": plan}


class TestMoney(unittest.TestCase):
    def test_half_up_not_float_rounding(self):
        self.assertEqual(cs.money(1.005), D("1.01"))  # round(1.005, 2) == 1.0 con float
        self.assertEqual(cs.money("2.675"), D("2.68"))
        self.assertEqual(cs.money(D("0.125")), D("0.13"))

    def test_invalid_amounts(self):
        for bad in (None, True, "abc", float("nan"), float("inf"), ""):
            with self.assertRaises(cs.CheckoutError, msg=repr(bad)):
                cs.to_decimal(bad)

    def test_normalize_rate(self):
        self.assertEqual(cs.normalize_rate("1071.12345"), D("1071.1235"))
        self.assertEqual(cs.normalize_rate({"usd_binance": 1071.5}), D("1071.5000"))
        for bad in (None, 0, -1, "nan", "abc", True, {}, {"usd_binance": None}, ""):
            with self.assertRaises(cs.RateUnavailable, msg=repr(bad)):
                cs.normalize_rate(bad)

    def test_usd_to_bs_single_rounding(self):
        self.assertEqual(cs.usd_to_bs("10", "1071.1234"), D("10711.23"))
        self.assertEqual(cs.usd_to_bs(D("3.33"), D("33.3333")), D("111.00"))  # 110.99989 -> 111.00

    def test_allocation_sums_exactly(self):
        total, parts = cs.allocate_bs([D("5.00")] * 3, "33.3333")
        self.assertEqual(total, D("500.00"))
        self.assertEqual(sum(parts), total)
        self.assertEqual(parts, [D("166.67"), D("166.67"), D("166.66")])

    def test_allocation_property(self):
        rng = random.Random(7)
        for _ in range(300):
            usd = [D(rng.randint(1, 4000)) / 100 for _ in range(rng.randint(1, 8))]
            rate = D(rng.randint(10000, 30000000)) / 10000
            total, parts = cs.allocate_bs(usd, rate)
            self.assertEqual(sum(parts), total)
            self.assertEqual(total, cs.usd_to_bs(sum(usd), rate))
            for u, p in zip(usd, parts):
                self.assertLessEqual(abs(p - u * cs.normalize_rate(rate)), D("0.01") * (len(usd) + 1))

    def test_allocation_without_items_or_rate(self):
        with self.assertRaises(cs.CheckoutError):
            cs.allocate_bs([], "10")
        with self.assertRaises(cs.RateUnavailable):
            cs.allocate_bs([D("1")], None)


class TestOverdueAndPeriods(unittest.TestCase):
    def test_is_overdue_predicate(self):
        now = caracas(2026, 6, 19, 16)
        self.assertTrue(cs.is_overdue(sub(status="active"), now))
        self.assertTrue(cs.is_overdue(sub(status="expired"), now))  # legado: se lee como mora
        for st in ("cancelled", "pending_payment", "expired_payment", "voided"):
            self.assertFalse(cs.is_overdue(sub(status=st), now), st)
        self.assertFalse(cs.is_overdue(sub(end="2026-07-20T03:59:59+00:00"), now))
        self.assertFalse(cs.is_overdue({"status": "active"}, now))

    def test_days_overdue_near_midnight_caracas(self):
        s = sub(end="2026-06-20T03:59:59+00:00")  # vence 19/06 23:59:59 Caracas
        self.assertEqual(cs.days_overdue(s, caracas(2026, 6, 19, 23, 59, 58)), 0)
        self.assertEqual(cs.days_overdue(s, caracas(2026, 6, 20, 0, 0, 1)), 1)
        self.assertEqual(cs.days_overdue(s, caracas(2026, 6, 26, 9)), 7)
        self.assertEqual(cs.days_overdue(sub(status="cancelled"), caracas(2026, 6, 26)), 0)

    def test_ray_example_from_fase8(self):
        end = "2026-05-20T03:59:59+00:00"  # cubierto hasta 19/05
        now = caracas(2026, 6, 19, 16)
        self.assertEqual(cs.overdue_periods(end, now), 1)
        self.assertEqual(cs.periods_to_be_current(end, now), 2)  # 1 mes deja 19/06: aún debe el periodo en curso
        self.assertEqual(cs.renewal_coverage(end, 1)[1].isoformat(), "2026-06-19")
        self.assertEqual(cs.renewal_coverage(end, 2)[1].isoformat(), "2026-07-19")
        self.assertEqual(cs.periods_to_be_current(end, caracas(2026, 6, 18, 10)), 1)

    def test_end_of_month_renewal(self):
        end = "2026-02-01T03:59:59+00:00"  # 31/01 Caracas
        desde, hasta = cs.renewal_coverage(end, 1)
        self.assertEqual((desde.isoformat(), hasta.isoformat()), ("2026-01-31", "2026-02-28"))

    def test_coverage_uses_caracas_day_not_utc_text(self):
        # El texto UTC empieza con 2026-05-20, pero el día real de Caracas es el 19.
        desde, _ = cs.renewal_coverage("2026-05-20T03:59:59+00:00", 1)
        self.assertEqual(desde.isoformat(), "2026-05-19")

    def test_periods_bounds(self):
        for bad in (0, 13, -1, True, 1.5, "2"):
            with self.assertRaises(cs.CheckoutError, msg=repr(bad)):
                cs.renewal_coverage("2026-05-20T03:59:59+00:00", bad)


class TestItems(unittest.TestCase):
    def test_renewal_item_points_to_a_specific_subscription(self):
        it = cs.build_renewal_item(sub("sub-A"), 2, "5.5")
        self.assertEqual(it["subscription_id"], "sub-A")
        self.assertEqual((it["cobertura_desde"], it["cobertura_hasta"]), ("2026-05-19", "2026-07-19"))
        self.assertEqual(it["subtotal_usd"], "11.00")

    def test_renewal_rejects_bad_states_and_plans(self):
        for st in ("pending_payment", "expired_payment", "cancelled", "voided", None):
            with self.assertRaises(cs.CheckoutError, msg=str(st)):
                cs.build_renewal_item(sub(status=st), 1, "5")
        with self.assertRaises(cs.CheckoutError):
            cs.build_renewal_item(sub(plan="express"), 1, "5")
        with self.assertRaises(cs.CheckoutError):
            cs.build_renewal_item(sub(), 1, "0")
        with self.assertRaises(cs.CheckoutError):
            cs.build_renewal_item({"status": "active", "end_date": "2026-05-20T03:59:59+00:00"}, 1, "5")

    def test_new_item(self):
        it = cs.build_new_item("pl1", "monthly", "4.99", "monthly")
        self.assertIsNone(it["subscription_id"])
        self.assertNotIn("cobertura_desde", it)
        self.assertEqual(it["subtotal_usd"], "4.99")
        with self.assertRaises(cs.CheckoutError):
            cs.build_new_item("pl1", "anual", "4.99")
        with self.assertRaises(cs.CheckoutError):
            cs.build_new_item("", "monthly", "4.99")

    def test_change_item_only_on_overdue(self):
        now = caracas(2026, 6, 19, 16)
        it = cs.build_change_item(sub("s9"), "hbo", "monthly", "6", "5", now)
        self.assertEqual(it["subtotal_usd"], "11.00")
        self.assertEqual(it["platform_origen_id"], "netflix")
        with self.assertRaises(cs.CheckoutError):
            cs.build_change_item(sub("s9", end="2026-07-20T03:59:59+00:00"), "hbo", "monthly", "6", "5", now)
        with self.assertRaises(cs.CheckoutError):
            cs.build_change_item(sub("s9"), "netflix", "monthly", "6", "5", now)
        with self.assertRaises(cs.CheckoutError):
            cs.build_change_item(sub("s9"), "hbo", "monthly", "6", "-1", now)


class TestManifest(unittest.TestCase):
    def two_same_platform(self):
        return [cs.build_renewal_item(sub("sub-A"), 1, "5"), cs.build_renewal_item(sub("sub-B"), 1, "5")]

    def test_two_services_same_platform_stay_separate(self):
        m = cs.build_manifest(self.two_same_platform(), "1071.1234")
        self.assertEqual([i["subscription_id"] for i in m["items"]], ["sub-A", "sub-B"])
        self.assertEqual(m["total_usd"], "10.00")
        self.assertEqual(m["total_bs"], "10711.23")
        self.assertEqual(sum(D(i["amount_bs"]) for i in m["items"]), D(m["total_bs"]))
        cs.validate_manifest(m)

    def test_same_subscription_twice_rejected(self):
        with self.assertRaises(cs.CheckoutError):
            cs.build_manifest([cs.build_renewal_item(sub("sub-A"), 1, "5")] * 2, "100")

    def test_mixed_items_and_json(self):
        items = [cs.build_renewal_item(sub("sub-A"), 3, "5"), cs.build_new_item("pl2", "express", "1")]
        m = cs.build_manifest(items, 1100)
        self.assertEqual(m["total_usd"], "16.00")
        self.assertEqual(json.loads(json.dumps(m)), m)
        cs.validate_manifest(json.loads(json.dumps(m)))

    def test_rate_is_frozen_in_manifest(self):
        m = cs.build_manifest(self.two_same_platform(), "1000")
        new_rate_total = cs.usd_to_bs(m["total_usd"], "1200")
        self.assertNotEqual(new_rate_total, D(m["total_bs"]))
        cs.validate_manifest(m)  # sigue siendo válido con SU tasa congelada

    def test_no_rate_blocks_checkout(self):
        for bad in (None, 0, "x"):
            with self.assertRaises(cs.RateUnavailable):
                cs.build_manifest(self.two_same_platform(), bad)

    def test_empty_and_too_many(self):
        with self.assertRaises(cs.CheckoutError):
            cs.build_manifest([], "100")
        many = [cs.build_new_item("pl", "monthly", "1") for _ in range(cs.MAX_ITEMS + 1)]
        with self.assertRaises(cs.CheckoutError):
            cs.build_manifest(many, "100")

    def test_renewal_or_change_without_subscription_rejected(self):
        bad = cs.build_new_item("pl", "monthly", "1")
        bad["tipo"] = "renovacion"
        with self.assertRaises(cs.CheckoutError):
            cs.build_manifest([bad], "100")

    def test_validate_detects_tampering(self):
        base = cs.build_manifest(self.two_same_platform(), "1071.1234")

        def tampered(fn):
            m = json.loads(json.dumps(base))
            fn(m)
            return m

        cases = {
            "total_bs": lambda m: m.update(total_bs="1.00"),
            "total_usd": lambda m: m.update(total_usd="99.00"),
            "subtotal": lambda m: m["items"][0].update(subtotal_usd="1.00"),
            "amount_bs": lambda m: m["items"][0].update(amount_bs="1.00"),
            "dup": lambda m: m["items"][1].update(subscription_id="sub-A"),
            "version": lambda m: m.update(version=99),
            "rate": lambda m: m.update(rate_used="0"),
            "items": lambda m: m.update(items=[]),
        }
        for name, fn in cases.items():
            with self.assertRaises(cs.CheckoutError, msg=name):
                cs.validate_manifest(tampered(fn))


class TestOrderTransitions(unittest.TestCase):
    def test_approve_from_pending(self):
        t = cs.check_order_transition("pending", "approved", "admin:777")
        self.assertEqual((t["from"], t["to"], t["actor"], t["reason"]), ("pending", "approved", "admin:777", None))

    def test_double_approval_is_already_applied(self):
        with self.assertRaises(cs.AlreadyApplied):
            cs.check_order_transition("approved", "approved", "admin:777")

    def test_reject_requires_reason(self):
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("pending", "rejected", "admin:777")
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("pending", "rejected", "admin:777", "   ")
        self.assertEqual(cs.check_order_transition("pending", "rejected", "a", "monto distinto")["reason"], "monto distinto")

    def test_cannot_approve_a_rejected_order(self):
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("rejected", "approved", "admin:777", "x")

    def test_late_receipt_needs_reason(self):
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("expired", "approved", "admin:777")
        cs.check_order_transition("expired", "approved", "admin:777", "comprobante tardío verificado")

    def test_approved_is_immutable_except_void_with_reason(self):
        for target in ("pending", "rejected", "expired", "cancelled"):
            with self.assertRaises(cs.InvalidTransition, msg=target):
                cs.check_order_transition("approved", target, "a", "x")
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("approved", "voided", "a")
        self.assertEqual(cs.check_order_transition("approved", "voided", "a", "error")["to"], "voided")

    def test_terminal_states_and_unknowns(self):
        for term in ("rejected", "cancelled", "voided"):
            for target in cs.ORDER_STATUSES:
                if (term, target) == ("x", "x"):
                    continue
                with self.assertRaises(cs.CheckoutError, msg=f"{term}->{target}"):
                    cs.check_order_transition(term, target, "a", "x")
        with self.assertRaises(cs.InvalidTransition):
            cs.check_order_transition("pending", "raro", "a")

    def test_actor_required(self):
        for actor in (None, "", "  "):
            with self.assertRaises(cs.InvalidTransition):
                cs.check_order_transition("pending", "approved", actor)

    def test_pending_to_expired_and_cancelled(self):
        cs.check_order_transition("pending", "expired", "sistema")
        cs.check_order_transition("pending", "cancelled", "cliente")

    def test_ttl(self):
        created = caracas(2026, 12, 1, 10)
        exp = cs.order_expires_at(created)
        self.assertEqual(exp - created, timedelta(hours=24))
        self.assertFalse(cs.is_order_expired(exp, created + timedelta(hours=23, minutes=59)))
        self.assertTrue(cs.is_order_expired(exp, exp))
        self.assertTrue(cs.is_order_expired(exp.astimezone(pytz.utc).isoformat(), exp + timedelta(seconds=1)))


if __name__ == "__main__":
    unittest.main()
