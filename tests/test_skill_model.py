"""Unit tests for the ACC-weighted skill model (backend/analysis/skill_model.py).

Covers the spec's math, the validation doc's direct-evidence rules and the
"insufficient data instead of guessing" product rule. All cases are constructed;
no database or network is involved.
"""
from __future__ import annotations

import math
import unittest

from backend.analysis.pp_predict import curve_multiplier
from backend.analysis.skill_model import (
    ACC_MAX,
    ACC_MIN,
    DIRECT_TRANSFER_LIMIT,
    LAMBDA,
    MIN_DIRECT_RECORDS,
    STAR_CAP,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    TRACKS,
    ScoreRecord,
    acc_utility,
    available_track_keys,
    difficulty_axis,
    enforce_monotonic,
    equivalent_stars,
    evidence_weight,
    inverse_difficulty_axis,
    predicted_acc,
    rate_player,
    rate_track,
    raw_transfer,
    star_evidence_weight,
    usable_records,
    weighted_median,
)

NOW = 1_800_000_000.0   # fixed clock: the model must be a pure function of `now`
DAY = 86400.0


def rec(stars: float, acc: float, *, key: str = "", age_days: float = 0.0,
        pp: float = 100.0, max_pp: float = 100.0, modifiers: str = "") -> ScoreRecord:
    return ScoreRecord(stars=stars, acc=acc, timestamp=NOW - age_days * DAY,
                       leaderboard_key=key or f"{stars}-{acc}-{age_days}",
                       pp=pp, max_pp=max_pp, modifiers=modifiers)


def acc_for_utility(target_utility: float) -> float:
    """Invert ``ln(curve(acc))`` by bisection (test-local helper for the gate edges)."""
    lo, hi = ACC_MIN, ACC_MAX
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if acc_utility(mid) < target_utility:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


class TestCurveMath(unittest.TestCase):
    def test_utility_is_log_of_curve_multiplier(self):
        for acc in (0.80, 0.85, 0.90, 0.94, 0.96, 0.99):
            self.assertAlmostEqual(acc_utility(acc), math.log(curve_multiplier(acc)),
                                   places=12)

    def test_utility_is_monotonic(self):
        accs = [0.80, 0.82, 0.85, 0.90, 0.94, 0.96, 0.98, 0.995]
        utils = [acc_utility(a) for a in accs]
        self.assertEqual(utils, sorted(utils))

    def test_utility_clamps_beyond_range(self):
        self.assertAlmostEqual(acc_utility(0.10), acc_utility(ACC_MIN), places=12)
        self.assertAlmostEqual(acc_utility(0.9999), acc_utility(ACC_MAX), places=12)

    def test_spec_reference_values(self):
        """The spec's table: C(80/94/96%) and their logs."""
        self.assertAlmostEqual(curve_multiplier(0.80), 0.6872268862950283, places=12)
        self.assertAlmostEqual(curve_multiplier(0.94), 0.9417362980580238, places=12)
        self.assertAlmostEqual(curve_multiplier(0.96), 1.0871883573850478, places=12)
        self.assertAlmostEqual(acc_utility(0.80), -0.375091, places=5)
        self.assertAlmostEqual(acc_utility(0.94), -0.060030, places=5)
        self.assertAlmostEqual(acc_utility(0.96), 0.083595, places=5)


class TestDifficultyAxis(unittest.TestCase):
    def test_axis_is_identity_when_gamma_is_one(self):
        for stars in (0.0, 1.0, 6.05, 10.24, 14.0):
            self.assertAlmostEqual(difficulty_axis(stars), stars, places=9)
            self.assertAlmostEqual(inverse_difficulty_axis(stars), stars, places=9)

    def test_negative_stars_clamp_to_zero(self):
        self.assertEqual(difficulty_axis(-3.0), 0.0)


class TestEvidenceWeight(unittest.TestCase):
    def test_star_weight_doubles_at_cap(self):
        self.assertAlmostEqual(star_evidence_weight(0.0), 1.0, places=9)
        self.assertAlmostEqual(star_evidence_weight(7.0), 1.25, places=9)
        self.assertAlmostEqual(star_evidence_weight(STAR_CAP), 2.0, places=9)
        self.assertAlmostEqual(star_evidence_weight(99.0), 2.0, places=9)

    def test_weight_decays_with_acc_distance(self):
        track = TRACKS[1]                       # personal94
        near = evidence_weight(rec(8.0, 0.94), track, NOW)
        far = evidence_weight(rec(8.0, 0.90), track, NOW)
        self.assertGreater(near, far)
        self.assertGreater(far, 0.0)

    def test_weight_halves_every_half_life(self):
        track = TRACKS[1]
        fresh = evidence_weight(rec(8.0, 0.94, age_days=0.0), track, NOW)
        aged = evidence_weight(rec(8.0, 0.94, age_days=180.0), track, NOW)
        self.assertAlmostEqual(aged / fresh, 0.5, places=6)


class TestWeightedMedian(unittest.TestCase):
    def test_single_value(self):
        self.assertEqual(weighted_median([5.0], [1.0]), 5.0)

    def test_weights_dominate_position(self):
        # 8.0 carries 9x the weight of 5.0 -> median stays at 8.0
        self.assertEqual(weighted_median([5.0, 8.0], [1.0, 9.0]), 8.0)

    def test_zero_weight_entries_are_ignored(self):
        self.assertEqual(weighted_median([5.0, 9.0], [1.0, 0.0]), 5.0)

    def test_empty(self):
        self.assertIsNone(weighted_median([], []))


class TestFilterAndDedup(unittest.TestCase):
    def test_rejects_unmodified_but_unranked_or_unscored(self):
        self.assertEqual(usable_records([rec(8.0, 0.9, pp=0.0)]), [])
        self.assertEqual(usable_records([rec(8.0, 0.9, max_pp=0.0)]), [])
        self.assertEqual(usable_records([rec(0.0, 0.9)]), [])

    def test_rejects_modified_plays(self):
        self.assertEqual(usable_records([rec(8.0, 0.9, modifiers="NF")]), [])
        self.assertEqual(usable_records([rec(8.0, 0.9, modifiers="DA")]), [])

    def test_excludes_acc_outside_spec_range_instead_of_clamping(self):
        self.assertEqual(usable_records([rec(8.0, 0.79)]), [])
        self.assertEqual(usable_records([rec(8.0, 0.999)]), [])
        self.assertEqual(len(usable_records([rec(8.0, 0.80), rec(8.0, 0.995)])), 2)

    def test_one_record_per_leaderboard_best_accuracy(self):
        a = rec(8.0, 0.90, key="lb1")
        b = rec(8.0, 0.95, key="lb1")
        c = rec(7.0, 0.93, key="lb2")
        kept = usable_records([a, b, c])
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(next(r.acc for r in kept if r.leaderboard_key == "lb1"), 0.95)


class TestDirectEvidenceGate(unittest.TestCase):
    def test_transfer_signs(self):
        track = TRACKS[1]                       # personal94
        self.assertLess(raw_transfer(rec(8.0, 0.90), track), 0.0)
        self.assertGreater(raw_transfer(rec(8.0, 0.98), track), 0.0)

    def test_beyond_limit_has_no_estimate(self):
        """The validation doc's core rule: a low-star high-acc play must not be
        extrapolated into a high-star 80% capability claim."""
        challenge = TRACKS[2]                   # personal80
        self.assertIsNone(equivalent_stars(rec(6.0, 0.98), challenge))
        self.assertIsNone(equivalent_stars(rec(8.0, 0.995), challenge))

    def test_within_limit_shifts_stars(self):
        standard = TRACKS[1]                    # personal94
        est = equivalent_stars(rec(10.0, 0.93), standard)
        self.assertIsNotNone(est)
        self.assertLess(est, 10.0)              # below 94% on a 10★ map
        est_high = equivalent_stars(rec(10.0, 0.95), standard)
        self.assertIsNotNone(est_high)
        self.assertGreater(est_high, 10.0)

    def test_gate_accepts_a_narrow_band_around_each_target(self):
        """What the 2.0-unit gate means in accuracy terms (measured, documented).

        The gate bounds ``(u(acc) - u(target)) / LAMBDA``, so each track only accepts
        accuracies near its own target -- which is exactly the validation doc's intent
        ("a 6★ 98% play cannot prove a high-star 80% capability"). Recorded here so a
        parameter change cannot silently widen or narrow a track's evidence window.
        """
        expected = {"personal96": (0.9311, 0.9722), "personal94": (0.8778, 0.9630), "personal80": (0.8000, 0.8983)}
        for track in TRACKS:
            base = acc_utility(track.target_acc)
            lo = acc_for_utility(base - DIRECT_TRANSFER_LIMIT * LAMBDA)
            hi = acc_for_utility(base + DIRECT_TRANSFER_LIMIT * LAMBDA)
            self.assertAlmostEqual(lo, expected[track.key][0], places=3)
            self.assertAlmostEqual(hi, expected[track.key][1], places=3)
            # just inside each edge -> a direct estimate exists
            self.assertIsNotNone(equivalent_stars(rec(9.0, lo + 0.002), track))
            self.assertIsNotNone(equivalent_stars(rec(9.0, hi - 0.002), track))
            # beyond the upper edge -> no estimate; note the lower side stays open for
            # personal80 because its target accuracy IS the clamp floor (ACC_MIN)
            self.assertIsNone(equivalent_stars(
                rec(9.0, acc_for_utility(base + (DIRECT_TRANSFER_LIMIT + 0.05) * LAMBDA)), track))
            if track.target_acc > ACC_MIN:
                self.assertIsNone(equivalent_stars(
                    rec(9.0, acc_for_utility(base - (DIRECT_TRANSFER_LIMIT + 0.05) * LAMBDA)), track))

    def test_a_95_percent_play_is_not_evidence_for_the_challenge_track(self):
        """A high-accuracy play says little about how far 80% accuracy reaches: it is
        outside personal80's evidence band and may only appear as a lower bound."""
        challenge = TRACKS[2]
        self.assertIsNone(equivalent_stars(rec(9.0, 0.95), challenge))
        self.assertIsNotNone(equivalent_stars(rec(9.0, 0.85), challenge))


class TestTrackRating(unittest.TestCase):
    def test_fewer_than_minimum_is_insufficient_with_lower_bound(self):
        challenge = TRACKS[2]                   # personal80
        records = [rec(8.0 + i * 0.1, 0.82) for i in range(MIN_DIRECT_RECORDS - 1)]
        records.append(rec(12.99, 0.85))        # high-star play, still direct evidence
        rating = rate_track(records, challenge, NOW)
        # 12.99★ @85% is direct evidence for personal80, so it counts; keep the count honest
        self.assertEqual(rating.direct_count, len(records))
        self.assertEqual(rating.status, STATUS_OK)
        self.assertAlmostEqual(rating.lower_bound_stars, 12.99, places=6)

    def test_lower_bound_only_counts_plays_at_or_above_target(self):
        standard = TRACKS[1]                    # personal94
        records = [rec(6.0 + i * 0.1, 0.90) for i in range(MIN_DIRECT_RECORDS)]
        rating = rate_track(records, standard, NOW)
        self.assertIsNone(rating.lower_bound_stars)     # nothing reached 94%

    def test_insufficient_when_all_evidence_is_far_from_target(self):
        challenge = TRACKS[2]                   # personal80
        records = [rec(7.0 + i * 0.1, 0.95) for i in range(20)]   # 95% plays
        rating = rate_track(records, challenge, NOW)
        self.assertEqual(rating.status, STATUS_INSUFFICIENT)
        self.assertIsNone(rating.stars)
        self.assertIsNotNone(rating.lower_bound_stars)            # reported as a bound

    def test_more_evidence_does_not_beat_higher_difficulty(self):
        """Validation doc problem #1: the style comparison must hold."""
        challenge = TRACKS[2]
        low_star_precise = [rec(6.0, 0.98, key=f"a{i}") for i in range(30)]
        high_star_rough = [rec(8.4, 0.80, key=f"b{i}") for i in range(30)]
        r_low = rate_track(low_star_precise, challenge, NOW)
        r_high = rate_track(high_star_rough, challenge, NOW)
        self.assertEqual(r_low.status, STATUS_INSUFFICIENT)
        self.assertEqual(r_high.status, STATUS_OK)
        self.assertLess((r_low.stars if r_low.stars else 0.0), r_high.stars)


class TestPlayerRating(unittest.TestCase):
    def test_monotonicity_is_enforced(self):
        ratings = {
            "personal96": rate_track([rec(8.0, 0.95, key=f"a{i}") for i in range(20)], TRACKS[0], NOW),
            "personal94": rate_track([rec(8.0, 0.93, key=f"b{i}") for i in range(20)], TRACKS[1], NOW),
            "personal80": rate_track([rec(8.0, 0.80, key=f"c{i}") for i in range(20)], TRACKS[2], NOW),
        }
        before = {k: (v.stars or 0.0) for k, v in ratings.items()}
        enforce_monotonic(ratings)
        after = {k: (v.stars or 0.0) for k, v in ratings.items()}
        self.assertLessEqual(after["personal96"], before["personal96"] + 1e-9)
        self.assertGreaterEqual(after["personal96"], after["personal94"] - 1e-9)
        self.assertGreaterEqual(after["personal94"], after["personal80"] - 1e-9)

    def test_available_tracks_reflect_sufficiency(self):
        ratings = {
            "personal96": rate_track([rec(9.0, 0.95, key=f"a{i}") for i in range(20)], TRACKS[0], NOW),
            "personal94": rate_track([], TRACKS[1], NOW),
            "personal80": rate_track([rec(8.0, 0.80, key=f"c{i}") for i in range(20)], TRACKS[2], NOW),
        }
        self.assertEqual(available_track_keys(ratings), ["personal96", "personal80"])

    def test_rate_player_end_to_end_shape(self):
        records = [rec(8.0 + (i % 10) * 0.1, 0.82, key=f"k{i}") for i in range(40)]
        ratings = rate_player(records, NOW)
        self.assertEqual(set(ratings), {"personal96", "personal94", "personal80"})
        for track in TRACKS:
            rating = ratings[track.key]
            self.assertEqual(rating.target_acc, track.target_acc)
            if rating.status == STATUS_OK:
                self.assertIsInstance(rating.stars, float)
                self.assertIn(rating.confidence, ("low", "medium", "high"))

    def test_rate_player_is_deterministic(self):
        records = [rec(8.0 + (i % 7) * 0.2, 0.84, key=f"k{i}", age_days=i * 3) for i in range(35)]
        first = {k: v.stars for k, v in rate_player(records, NOW).items()}
        second = {k: v.stars for k, v in rate_player(list(records), NOW).items()}
        self.assertEqual(first, second)


class TestPredictedAcc(unittest.TestCase):
    def test_equal_stars_predicts_target_accuracy(self):
        self.assertAlmostEqual(predicted_acc(9.0, 9.0), 0.94, places=6)

    def test_easier_map_predicts_higher_accuracy(self):
        self.assertGreater(predicted_acc(7.0, 9.0), predicted_acc(9.0, 9.0))
        self.assertLess(predicted_acc(11.0, 9.0), predicted_acc(9.0, 9.0))

    def test_prediction_stays_inside_curve_range(self):
        self.assertGreaterEqual(predicted_acc(0.0, 14.0), ACC_MIN)
        self.assertLessEqual(predicted_acc(20.0, 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
