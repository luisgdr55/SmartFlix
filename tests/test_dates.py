"""Pruebas de `utils/dates.py` (O02 Lote 1): meses de calendario, fin de día en Caracas y UTC."""
import unittest
from datetime import date, datetime, timezone

import pytz

from utils import dates as d


class TestAddMonths(unittest.TestCase):
    def test_end_of_month_clamps(self):
        self.assertEqual(d.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(d.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))  # bisiesto
        self.assertEqual(d.add_months(date(2026, 3, 31), 1), date(2026, 4, 30))

    def test_year_rollover_and_negative(self):
        self.assertEqual(d.add_months(date(2026, 12, 15), 1), date(2027, 1, 15))
        self.assertEqual(d.add_months(date(2026, 1, 15), -1), date(2025, 12, 15))
        self.assertEqual(d.add_months(date(2026, 5, 19), 14), date(2027, 7, 19))

    def test_anchor_day_returns_to_original_day(self):
        feb = d.add_months(date(2026, 1, 31), 1, anchor_day=31)
        self.assertEqual(feb, date(2026, 2, 28))
        self.assertEqual(d.add_months(feb, 1, anchor_day=31), date(2026, 3, 31))
        # sin ancla el día deriva (comportamiento documentado)
        self.assertEqual(d.add_months(feb, 1), date(2026, 3, 28))

    def test_invalid_anchor(self):
        with self.assertRaises(ValueError):
            d.add_months(date(2026, 1, 1), 1, anchor_day=0)


class TestWholeMonths(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(d.whole_months_between(date(2026, 5, 19), date(2026, 6, 19)), 1)
        self.assertEqual(d.whole_months_between(date(2026, 5, 19), date(2026, 6, 18)), 0)
        self.assertEqual(d.whole_months_between(date(2026, 1, 31), date(2026, 2, 28)), 1)
        self.assertEqual(d.whole_months_between(date(2026, 6, 1), date(2026, 5, 1)), 0)
        self.assertEqual(d.whole_months_between(date(2026, 5, 19), date(2026, 5, 19)), 0)


class TestTimezones(unittest.TestCase):
    def test_end_of_day_is_23_59_59_caracas(self):
        e = d.end_of_day_caracas(date(2026, 6, 19))
        self.assertEqual((e.hour, e.minute, e.second, e.microsecond), (23, 59, 59, 0))
        self.assertEqual(e.utcoffset().total_seconds(), -4 * 3600)

    def test_db_roundtrip_utc(self):
        e = d.end_of_day_caracas(date(2026, 6, 19))
        self.assertEqual(d.to_db(e), "2026-06-20T03:59:59+00:00")
        self.assertEqual(d.parse_db_datetime(d.to_db(e)), e)

    def test_caracas_date_vs_naive_text_slice_near_midnight(self):
        stored = "2026-06-20T03:59:59+00:00"  # 23:59:59 del 19 en Caracas
        self.assertEqual(stored[:10], "2026-06-20")  # el recorte de texto lee el día equivocado
        self.assertEqual(d.caracas_date(stored), date(2026, 6, 19))

    def test_parse_variants(self):
        self.assertEqual(d.parse_db_datetime("2026-06-20T03:59:59Z"), datetime(2026, 6, 20, 3, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(d.parse_db_datetime("2026-06-20T03:59:59.123456+00:00").microsecond, 123456)
        self.assertEqual(d.parse_db_datetime("2026-06-20T03:59:59+00").hour, 3)
        self.assertEqual(d.parse_db_datetime("2026-06-19 23:59:59-04:00").hour, 3)  # → UTC
        naive = d.parse_db_datetime(datetime(2026, 6, 20, 3, 59, 59))
        self.assertEqual(naive.tzinfo.zone if hasattr(naive.tzinfo, "zone") else "UTC", "UTC")

    def test_invalid_inputs(self):
        for bad in ("", "no-es-fecha", None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                d.parse_db_datetime(bad)
        with self.assertRaises(ValueError):
            d.to_utc(datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            d.to_caracas(datetime(2026, 1, 1))

    def test_period_end(self):
        e = d.period_end(date(2026, 1, 31), 1)
        self.assertEqual(e, d.end_of_day_caracas(date(2026, 2, 28)))
        with self.assertRaises(ValueError):
            d.period_end(date(2026, 1, 31), 0)

    def test_to_caracas_keeps_instant(self):
        utc = pytz.utc.localize(datetime(2026, 6, 20, 2, 0))
        self.assertEqual(d.to_caracas(utc).date(), date(2026, 6, 19))


if __name__ == "__main__":
    unittest.main()
