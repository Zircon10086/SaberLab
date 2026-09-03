"""Personal star palette (player_palette.py) unit tests.

Covers: record filtering (ranked/stars/pp/NF), pp-based top-20 selection
(time deliberately ignored), percentile interpolation, round_to_quarter,
the three sample-size branches (top20 / blend8-19 / fallback) + unknown,
and build_tiers boundary semantics (delta = ±0.5 / ±1.5, epsilon at +0.5).
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.player_palette import (
    build_tiers, classify_player, percentile, round_to_quarter)


def rec(stars, pp, time_set="2026-01-01T00:00:00Z", modifiers=None,
        ranked=True):
    return {"stars": stars, "pp": pp, "time_set": time_set,
            "modifiers": modifiers or "", "ranked": ranked}


class TestFiltering(unittest.TestCase):
    def test_unknown_when_no_valid(self):
        res = classify_player([rec(5.0, 100, ranked=False)])
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(res["yellow_stars"], None)

    def test_excludes_unranked_zero_stars_zero_pp(self):
        records = [rec(5.0, 100, ranked=False),
                   rec(0.0, 100), rec(5.0, 0), rec(5.0, -1)]
        res = classify_player(records)
        self.assertEqual(res["status"], "unknown")

    def test_excludes_nf(self):
        records = [rec(5.0, 100), rec(9.0, 400, modifiers="NF"),
                   rec(8.0, 350, modifiers="GN,NF")]
        res = classify_player(records)
        self.assertEqual(res["valid_count"], 1)
        self.assertEqual(res["nf_excluded"], 2)
        self.assertEqual(res["max_single_pp"], 100.0)

    def test_non_nf_modifiers_kept(self):
        records = [rec(5.0, 100, modifiers="GN,FS")]
        res = classify_player(records)
        self.assertEqual(res["valid_count"], 1)


class TestPercentile(unittest.TestCase):
    def test_interpolation(self):
        # numpy-style linear interpolation on sorted [10, 20, 30, 40]
        self.assertEqual(percentile([40, 10, 30, 20], 0.25), 17.5)
        self.assertEqual(percentile([40, 10, 30, 20], 0.50), 25.0)

    def test_single_value(self):
        self.assertEqual(percentile([7.5], 0.25), 7.5)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            percentile([], 0.25)


class TestRoundToQuarter(unittest.TestCase):
    def test_quarters(self):
        self.assertEqual(round_to_quarter(7.51), 7.5)
        self.assertEqual(round_to_quarter(7.49), 7.5)
        self.assertEqual(round_to_quarter(7.13), 7.25)
        self.assertEqual(round_to_quarter(8.0), 8.0)


class TestTop20ByPp(unittest.TestCase):
    def test_uses_pp_not_time(self):
        """Time is deliberately ignored: records with the HIGHEST pp win,
        even when their time_set is old / out of order."""
        records = []
        for i in range(30):
            pp = 100 + i          # ascending pp
            ts = f"2026-01-{31 - i:02d}T00:00:00Z"   # newest = lowest pp
            records.append(rec(3.0 + i * 0.1, pp, time_set=ts))
        res = classify_player(records)
        # top 20 by pp = i=10..29 -> stars 4.0 .. 5.9
        self.assertEqual(res["sample_count"], 20)
        self.assertEqual(res["method"], "top20")
        # Q25 of 4.0..5.9 (20 values): idx 4.75 -> 4.475; Q50 = 4.95
        # (4.475 + 4.95)/2 = 4.7125 -> round_to_quarter = 4.75
        self.assertEqual(res["yellow_stars"], 4.75)

    def test_sample_rejects_pp_filtered(self):
        """NF records must not count toward the top-20 sample."""
        records = [rec(9.0, 999, modifiers="NF")] + \
                  [rec(5.0, 100 + i) for i in range(25)]
        res = classify_player(records)
        self.assertEqual(res["valid_count"], 25)
        self.assertEqual(res["sample_count"], 20)
        self.assertLess(res["yellow_stars"], 9.0)


class TestBranches(unittest.TestCase):
    def test_top20_branch(self):
        records = [rec(5.0 + i * 0.1, 200 + i) for i in range(20)]
        res = classify_player(records)
        self.assertEqual(res["method"], "top20")
        self.assertEqual(res["status"], "known")
        self.assertEqual(res["sample_count"], 20)

    def test_blend8_19_branch(self):
        records = [rec(6.0 + i * 0.1, 250 + i) for i in range(12)]
        res = classify_player(records)
        self.assertEqual(res["method"], "blend8-19")
        self.assertEqual(res["sample_count"], 12)
        # personal = round_quarter((Q25+Q50)/2); yellow = round_quarter((personal+fallback)/2)
        # max_pp = 261 -> stage 进阶/高阶 fallback 7.0
        self.assertEqual(res["fallback_stars"], 7.0)
        self.assertTrue(6.0 <= res["yellow_stars"] <= 7.0)

    def test_fallback_branch(self):
        records = [rec(6.0, 150), rec(6.5, 180), rec(7.0, 190)]
        res = classify_player(records)
        self.assertEqual(res["method"], "fallback")
        # max_pp=190 < 200 -> 初级/休闲 fallback 5.75
        self.assertEqual(res["stage"], "初级/休闲")
        self.assertEqual(res["yellow_stars"], 5.75)

    def test_stage_by_max_pp(self):
        self.assertEqual(classify_player([rec(6.0, 199)])["stage"], "初级/休闲")
        self.assertEqual(classify_player([rec(6.0, 200)])["stage"], "进阶/高阶")
        self.assertEqual(classify_player([rec(6.0, 349)])["stage"], "进阶/高阶")
        self.assertEqual(classify_player([rec(6.0, 350)])["stage"], "竞技向")


class TestBuildTiers(unittest.TestCase):
    def test_tiers_for_yellow_7_5(self):
        """Doc example (yellow=7.50): grey <6 / green 6-7 / yellow 7-8 /
        red 8.01-9 / purple >9."""
        tiers = build_tiers(7.5)
        self.assertEqual([t["cls"] for t in tiers],
                         ["star-gray", "star-green", "star-yellow",
                          "star-red", "star-purple"])
        self.assertEqual(tiers[0]["max"], 6.0)
        self.assertEqual(tiers[1]["max"], 7.0)
        self.assertEqual(tiers[3]["max"], 9.0 + 1e-6)
        self.assertIsNone(tiers[4]["max"])

    def test_boundary_inclusive_yellow_at_plus_0_5(self):
        """stars == yellow + 0.5 must stay YELLOW (delta <= +0.5)."""
        tiers = build_tiers(7.5)
        self.assertLess(8.0, tiers[2]["max"])          # 8.0 -> yellow
        self.assertGreater(8.01, tiers[2]["max"])      # 8.01 -> red
        self.assertLess(9.0, tiers[3]["max"])          # 9.0 -> red (delta=+1.5 inclusive)
        self.assertGreater(9.01, tiers[3]["max"])      # 9.01 -> purple

    def test_delta_minus_0_5_green(self):
        """delta = -0.5 belongs to YELLOW; delta = -1.5 belongs to GREEN."""
        tiers = build_tiers(7.5)
        self.assertLess(6.0, tiers[1]["max"])          # 6.0 -> green
        self.assertFalse(7.0 < tiers[1]["max"])        # 7.0 -> yellow (not green)

    def test_delta_minus_1_5_grey(self):
        tiers = build_tiers(7.5)
        self.assertFalse(6.0 < tiers[0]["max"])        # 6.0 -> green (not grey)
        self.assertLess(5.99, tiers[0]["max"])         # 5.99 -> grey

    def test_quarter_boundaries_stay_tidy(self):
        tiers = build_tiers(5.75)
        self.assertEqual([t["max"] for t in tiers[:4]],
                         [4.25, 5.25, 6.25 + 1e-6, 7.25 + 1e-6])

if __name__ == "__main__":
    unittest.main()
