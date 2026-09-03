"""ScoreSaber PP prediction tests (v2.1.0).

The formula pp = maxPP * curve(acc) was replicated from ScoreSaber's official
pp-curve endpoint (realms/1, normalized at acc=0.95) and verified against the
local database: replays whose recorded pp belongs to the play itself match
pp / curve(acc) == maxPP within +-0.1% (_tmp/verify_pp_curve.py). These tests
pin the embedded curve, the prediction helpers and the leaderboard pick rule.
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.pp_predict import (SS_CURVE, SLIDER_HI, SLIDER_LO,
                                         curve_multiplier, predict_pp,
                                         preview_payload, ss_accuracy)
from backend.services.enrichment import pick_leaderboard

MULT_095 = 1.0
MULT_100 = 5.367394282890631
MULT_060 = 0.18223233667439062


class TestCurveMultiplier(unittest.TestCase):
    def test_knots_exact(self):
        self.assertEqual(curve_multiplier(0.95), MULT_095)
        self.assertEqual(curve_multiplier(1.0), MULT_100)
        self.assertEqual(curve_multiplier(0.6), MULT_060)
        self.assertEqual(curve_multiplier(0.0), 0.0)

    def test_interp_between_knots(self):
        # 0.97125 = midpoint of the 0.97 / 0.9725 knots
        m = curve_multiplier(0.97125)
        self.assertAlmostEqual(m, (1.2485807759957321 + 1.3090333065057616) / 2)
        # 0.975 is itself a knot
        self.assertEqual(curve_multiplier(0.975), 1.3807102743105126)

    def test_below_first_knot_linear_to_zero(self):
        # SS_CURVE starts with the [0, 0] -> [0.6, 0.1822] segment (faithful to
        # the API data): half-way down the segment gives half the multiplier
        self.assertAlmostEqual(curve_multiplier(0.3), MULT_060 / 2)

    def test_clamped_ends_and_none(self):
        self.assertEqual(curve_multiplier(-0.1), 0.0)
        self.assertEqual(curve_multiplier(1.5), MULT_100)
        self.assertEqual(curve_multiplier(None), 0.0)

    def test_monotonic_nondecreasing(self):
        xs = [i / 1000 for i in range(0, 1001)]
        ys = [curve_multiplier(x) for x in xs]
        for a, b in zip(ys, ys[1:]):
            self.assertLessEqual(a, b + 1e-12)


class TestPredictPp(unittest.TestCase):
    def test_anchor_at_95(self):
        # maxPP is BY DEFINITION the pp awarded at 95% acc
        self.assertEqual(predict_pp(359.2, 0.95), 359.2)

    def test_full_acc_uses_top_knot(self):
        self.assertAlmostEqual(predict_pp(359.2, 1.0), 359.2 * MULT_100)

    def test_zero_max_pp(self):
        self.assertEqual(predict_pp(0.0, 0.99), 0.0)


class TestSsAccuracy(unittest.TestCase):
    def test_normal_play_passthrough(self):
        self.assertEqual(ss_accuracy(0.8357, 100000, 100000), 0.8357)

    def test_nf_play_halved_score(self):
        # Awarded PP follows the score ScoreSaber actually receives.
        self.assertAlmostEqual(ss_accuracy(0.8, 100000, 50000), 0.4)

    def test_missing_accuracy(self):
        self.assertIsNone(ss_accuracy(None, 100000, 50000))


class TestPreviewPayload(unittest.TestCase):
    def test_shape_and_knots(self):
        d = preview_payload(300.0, default_acc=0.83)
        self.assertEqual(d["lo"], SLIDER_LO)
        self.assertEqual(d["hi"], SLIDER_HI)
        self.assertEqual(d["max_pp"], 300.0)
        self.assertEqual(d["default_acc"], 0.83)
        self.assertEqual(len(d["curve"]), len(SS_CURVE))
        self.assertEqual(d["curve"][-1], [1.0, 300.0 * MULT_100])
        # 0.95 knot substitutes the multiplier-1.0 anchor: pp == maxPP
        self.assertEqual(d["curve"][SS_CURVE.index((0.95, 1.0))], [0.95, 300.0])

    def test_default_acc_optional(self):
        self.assertIsNone(preview_payload(300.0)["default_acc"])

    def test_low_exit_accuracy_extends_slider_range(self):
        d = preview_payload(300.0, default_acc=0.47)
        self.assertEqual(d["lo"], 0.47)
        self.assertEqual(d["default_acc"], 0.47)


class TestPickLeaderboard(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(pick_leaderboard([]))

    def test_ranked_and_standard_preferred(self):
        rows = [
            {"ranked": 0, "game_mode": "SoloStandard", "stars": 1.0},
            {"ranked": 1, "game_mode": "Solo90Degree", "stars": 2.0},
            {"ranked": 1, "game_mode": "SoloStandard", "stars": 3.0},
        ]
        self.assertEqual(pick_leaderboard(rows)["stars"], 3.0)


if __name__ == "__main__":
    unittest.main()
