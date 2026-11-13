import unittest
from datetime import date

import numpy as np

from src import obs
from src.fairvalue import Combiner, DayState, DropModel, taker_fee, yes_interval


class ParseMetarTest(unittest.TestCase):
    def test_hourly_report_uses_tenths_and_six_hour_groups(self):
        p = obs.parse_metar("KBOS 291154Z 22010KT 10SM FEW200 10/07 A3036 RMK AO2 SLP281 "
                            "T01000067 10111 20083 56013")
        self.assertEqual(p["temp_c"], 10.0)
        self.assertEqual(p["precise"], 1)
        self.assertAlmostEqual(p["min6_c"], 8.3)
        self.assertAlmostEqual(p["max6_c"], 11.1)

    def test_negative_temperatures(self):
        p = obs.parse_metar("KMSP 121153Z 30008KT 10SM CLR M05/M11 A3012 RMK AO2 T10501106 21061")
        self.assertAlmostEqual(p["temp_c"], -5.0)
        self.assertAlmostEqual(p["min6_c"], -6.1)

    def test_five_minute_reading_is_not_precise_and_has_no_groups(self):
        p = obs.parse_metar("KBOS 290005Z AUTO 18005KT 10SM CLR 08/06 A3040 RMK T00800060 MADISHF")
        self.assertEqual(p["temp_c"], 8.0)
        self.assertEqual(p["precise"], 0)
        self.assertIsNone(p["min6_c"])

    def test_pressure_tendency_is_not_a_minimum(self):
        p = obs.parse_metar("KBOS 290254Z 22010KT 10SM FEW200 10/07 A3036 RMK AO2 SLP281 T01000067 56013")
        self.assertIsNone(p["min6_c"])
        self.assertIsNone(p["max6_c"])

    def test_body_temperature_when_no_t_group(self):
        p = obs.parse_metar("KNYC 290451Z AUTO 00000KT 10SM CLR M02/M08 A3040 RMK AO2")
        self.assertEqual(p["temp_c"], -2.0)
        self.assertEqual(p["precise"], 0)

    def test_csv_rows(self):
        text = ("station,valid,metar\n"
                "BOS,2026-10-29 00:54,KBOS 290054Z 21005KT 10SM 09/06 A3039 RMK AO2 SLP289 T00890056\n"
                "BOS,bad,KBOS\n")
        rows = obs.parse_iem_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "BOS")
        self.assertEqual(rows[0][1], 1793235240)
        self.assertAlmostEqual(rows[0][2], 8.9)

    def test_climate_day_is_standard_time(self):
        start, end = obs.climate_day_bounds(date(2026, 7, 4), 5)
        self.assertEqual(start, 1783141200)          # 05:00 UTC, also in summer
        self.assertEqual(end - start, 86400)


class YesIntervalTest(unittest.TestCase):
    def test_strike_types(self):
        self.assertEqual(yes_interval("greater", 48, None), (49, 999))
        self.assertEqual(yes_interval("less", None, 41), (-999, 40))
        self.assertEqual(yes_interval("between", 47, 48), (47, 48))
        with self.assertRaises(ValueError):
            yes_interval("functional", None, None)


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


class DayStateTest(unittest.TestCase):
    def setUp(self):
        self.t0 = 1_000_000 * 3600           # an exact hour
        ts = np.array([-600, 600, 3180, 3600, 7200, 21480]) + self.t0
        temp = f_to_c(np.array([40.0, 50.0, 48.0, 46.0, 52.0, 55.0]))
        precise = np.array([0, 0, 1, 0, 0, 1])
        min6 = np.array([np.nan, np.nan, np.nan, np.nan, np.nan, f_to_c(44.0)])
        self.day = DayState(ts, temp, precise, min6, self.t0, lat_precise=120, lat_coarse=900)

    def test_nothing_known_before_first_report_is_public(self):
        a = self.day.at([self.t0 + 600 + 899])
        self.assertTrue(np.isnan(a["x"][0]))

    def test_previous_day_reading_is_ignored(self):
        a = self.day.at([self.t0 + 600 + 900])
        self.assertAlmostEqual(a["x"][0], 50.0)
        self.assertTrue(a["coarse"][0])

    def test_reports_count_only_after_their_delay(self):
        a = self.day.at([self.t0 + 3180 + 119, self.t0 + 3180 + 120])
        self.assertAlmostEqual(a["x"][0], 50.0)
        self.assertAlmostEqual(a["x"][1], 48.0)
        self.assertFalse(a["coarse"][1])

    def test_gap_and_coarse_flag(self):
        a = self.day.at([self.t0 + 7200 + 900])
        self.assertAlmostEqual(a["x"][0], 46.0)
        self.assertAlmostEqual(a["gap"][0], 6.0)
        self.assertTrue(a["coarse"][0])              # 46 came from a whole-degree reading
        self.assertAlmostEqual(a["hour"][0], 2.25)

    def test_six_hour_minimum_sees_between_readings(self):
        a = self.day.at([self.t0 + 21480 + 120])
        self.assertAlmostEqual(a["x"][0], 44.0)
        self.assertFalse(a["coarse"][0])

    def test_six_hour_group_spanning_the_previous_day_is_dropped(self):
        ts = np.array([3480.0]) + self.t0             # the group would cover five hours of yesterday
        day = DayState(ts, f_to_c(np.array([50.0])), np.array([1]), np.array([f_to_c(30.0)]),
                       self.t0, lat_precise=0)
        self.assertAlmostEqual(day.at([self.t0 + 4000])["x"][0], 50.0)

    def test_glitched_six_hour_minimum_is_dropped(self):
        # 55F "minimum" on a morning whose readings never went below 63F
        ts = np.array([3480.0, 7080.0, 21480.0]) + self.t0
        temp = f_to_c(np.array([66.0, 63.0, 63.0]))
        min6 = np.array([np.nan, np.nan, f_to_c(55.0)])
        day = DayState(ts, temp, np.array([1, 1, 1]), min6, self.t0, lat_precise=0)
        self.assertAlmostEqual(day.at([self.t0 + 22000])["x"][0], 63.0)

    def test_empty_day(self):
        day = DayState([], [], [], [], self.t0)
        self.assertTrue(np.isnan(day.at([self.t0 + 100.0])["x"][0]))


class DropModelTest(unittest.TestCase):
    def test_probabilities_follow_the_training_drops(self):
        rng = np.random.default_rng(0)
        n = 4000
        hour = np.full(n, 3.5)
        gap = np.full(n, 0.0)
        coarse = np.zeros(n, bool)
        d = -rng.integers(0, 4, n).astype(float)      # low ends 0..3 F below x, evenly
        m = DropModel().fit(hour, gap, coarse, d)
        x = np.array([50.0])
        p_ge_50 = m.prob([3.2], [0.0], [False], x, [50], [999])[0]
        p_48_49 = m.prob([3.2], [0.0], [False], x, [48], [49])[0]
        p_above = m.prob([3.2], [0.0], [False], x, [51], [999])[0]
        self.assertAlmostEqual(p_ge_50, 0.25, delta=0.03)
        self.assertAlmostEqual(p_48_49, 0.50, delta=0.03)
        self.assertLess(p_above, 0.001)
        self.assertGreater(p_above, 0.0)             # never exactly zero

    def test_sparse_cell_falls_back_to_the_hour(self):
        hour = np.concatenate([np.full(500, 10.5), np.full(5, 10.5)])
        gap = np.concatenate([np.full(500, 0.0), np.full(5, 20.0)])
        d = np.concatenate([np.zeros(500), np.full(5, -10.0)])
        m = DropModel().fit(hour, gap, np.zeros(505, bool), d)
        p = m.prob([10.1], [20.0], [False], np.array([60.0]), [60], [999])[0]
        self.assertGreater(p, 0.95)

    def test_unknown_state_gives_nan(self):
        m = DropModel().fit(np.full(200, 1.5), np.zeros(200), np.zeros(200, bool), np.zeros(200))
        self.assertTrue(np.isnan(m.prob([1.5], [0.0], [False], np.array([np.nan]), [50], [60])[0]))

    def test_combiner_ignores_a_station_model_that_knows_nothing(self):
        rng = np.random.default_rng(1)
        n = 20000
        price = rng.uniform(0.05, 0.95, n)
        y = (rng.uniform(size=n) < price).astype(float)       # the price is exactly right
        noise = rng.uniform(0.05, 0.95, n)
        hour = rng.uniform(0, 24, n)
        blend = Combiner().fit(price, noise, hour, y, np.ones(n))
        for c in blend.coef.values():
            self.assertAlmostEqual(c[1], 1.0, delta=0.15)     # weight on the price
            self.assertAlmostEqual(c[2], 0.0, delta=0.10)     # weight on the noise
        only = Combiner().fit(price, None, hour, y, np.ones(n))
        out = only.predict(np.array([0.3, 0.7]), None, np.array([1.0, 13.0]))
        self.assertAlmostEqual(out[0], 0.3, delta=0.04)
        self.assertAlmostEqual(out[1], 0.7, delta=0.04)

    def test_combiner_uses_a_station_model_that_knows_more(self):
        rng = np.random.default_rng(2)
        n = 20000
        truth = rng.uniform(0.05, 0.95, n)
        y = (rng.uniform(size=n) < truth).astype(float)
        price = np.full(n, 0.5)                                # the price knows nothing
        blend = Combiner().fit(price + rng.normal(0, 0.01, n), truth, np.full(n, 5.0), y, np.ones(n))
        self.assertGreater(blend.coef[1][2], 0.8)

    def test_fee(self):
        self.assertAlmostEqual(float(taker_fee(0.5)), 0.0175)


if __name__ == "__main__":
    unittest.main()
