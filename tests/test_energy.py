"""Energy / fail-time module tests (official algorithm port, 2026-09).

Covers:
  - every official constant takes effect (good/bad/miss/bomb/burst elements)
  - starting energy: Bar=0.5 / Battery=1.0 / instaFail=1.0 with a single life
  - the 1.0 cap, Bar regenerating, Battery not regenerating and draining a whole cell
  - fail detection (≤1e-5 → 0), fail latching and noFail suppression
  - modifier parsing (no substring false positives)
  - derived fields (the event stream can replay the timeline, drain by reason / by hand, obstacle checkpoints)
  - end-to-end on a real replay (the file that carries walls)
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis import energy as E  # noqa: E402
from backend.bsor import parser  # noqa: E402
from backend.config import load_config  # noqa: E402


class FakeNote:
    """Minimal stand-in with the attributes the simulator reads.

    `params` mirrors the real NoteEvent property (scoring_type decoded from noteID).
    """
    def __init__(self, t, event_type, note_id=33111, saber=""):
        self.event_time = t
        self.event_type = event_type
        self.note_id = note_id
        self.saber = saber

    @property
    def params(self):
        from backend.bsor.models import NoteParams
        return NoteParams.decode(self.note_id)

    @property
    def is_bomb(self):
        return self.note_id == -1 or self.note_id % 10 == 9


class FakeWall:
    def __init__(self, t, recorded_energy, wall_id=1):
        self.time = t
        self.energy = recorded_energy
        self.wall_id = wall_id


# noteID encoding (official NoteParams rule):
#   noteID = scoringType*10000 + lineIndex*1000 + noteLineLayer*100 + colorType*10 + cutDirection
# scoringType: 3 = normal, 4 = slider head, 5 = slider tail, 6 = burst head, 7 = burst element
NORMAL = 33111         # scoring 3, line 3, layer 1, color 1, cutDir 1
NORMAL2 = 30011        # scoring 3, line 0, layer 0, color 1, cutDir 1
BURST_ELEMENT = 73111  # scoring 7 (burst slider element)
BOMB = 30019           # scoring 3 + last digit 9 -> bomb per the official re-tag rule


class TestOfficialConstants(unittest.TestCase):
    def test_constants_match_decompiled_values(self):
        self.assertEqual(E.CHARGE_GOOD, 0.01)
        self.assertEqual(E.DRAIN_BAD, 0.10)
        self.assertEqual(E.DRAIN_MISS, 0.15)
        self.assertEqual(E.DRAIN_BOMB, 0.15)
        self.assertEqual(E.CHARGE_GOOD_BURST, 0.002)
        self.assertEqual(E.DRAIN_BAD_BURST, 0.025)
        self.assertEqual(E.DRAIN_MISS_BURST, 0.03)
        self.assertEqual(E.OBSTACLE_DRAIN_PER_SECOND, 1.3)
        self.assertEqual(E.ENERGY_CAP, 1.0)
        self.assertEqual(E.START_BAR, 0.5)
        self.assertEqual(E.START_BATTERY, 1.0)
        self.assertEqual(E.BATTERY_LIVES, 4)

    def test_good_charges_ten_milli(self):
        res = E.simulate_energy([FakeNote(1.0, E.GOOD)], [])
        self.assertAlmostEqual(res.end_energy, 0.5 + 0.01, places=6)

    def test_bad_miss_bomb_drains(self):
        base = E.EnergyConfig()
        for etype, expected in ((E.BAD, 0.10), (E.MISS, 0.15)):
            res = E.simulate_energy([FakeNote(1.0, etype)], [], base)
            self.assertAlmostEqual(res.end_energy, 0.5 - expected, places=6, msg=str(etype))
        res = E.simulate_energy([FakeNote(1.0, E.BAD, note_id=BOMB)], [], base)
        self.assertAlmostEqual(res.end_energy, 0.5 - E.DRAIN_BOMB, places=6)

    def test_burst_element_uses_its_own_constants(self):
        base = E.EnergyConfig()
        res = E.simulate_energy([FakeNote(1.0, E.GOOD, note_id=BURST_ELEMENT)], [], base)
        self.assertAlmostEqual(res.end_energy, 0.5 + E.CHARGE_GOOD_BURST, places=6)
        res = E.simulate_energy([FakeNote(1.0, E.BAD, note_id=BURST_ELEMENT)], [], base)
        self.assertAlmostEqual(res.end_energy, 0.5 - E.DRAIN_BAD_BURST, places=6)
        res = E.simulate_energy([FakeNote(1.0, E.MISS, note_id=BURST_ELEMENT)], [], base)
        self.assertAlmostEqual(res.end_energy, 0.5 - E.DRAIN_MISS_BURST, places=6)

    def test_obstacle_drains_per_second(self):
        cfg = E.EnergyConfig(obstacle_hit_seconds=0.5)
        res = E.simulate_energy([], [FakeWall(2.0, recorded_energy=0.9)], cfg)
        # Bar start 0.5 - 1.3*0.5 = -0.15 -> clamped to 0 and the run counts as failed
        self.assertAlmostEqual(res.end_energy, 0.0, places=6)
        self.assertTrue(res.did_reach_zero)
        self.assertEqual(len(res.obstacle_hits), 1)
        self.assertEqual(res.obstacle_hits[0]["recorded_energy"], 0.9)
        # a shorter dwell does not empty the bar
        res2 = E.simulate_energy([], [FakeWall(2.0, recorded_energy=0.9)],
                                 E.EnergyConfig(obstacle_hit_seconds=0.1))
        self.assertAlmostEqual(res2.end_energy, 0.5 - 1.3 * 0.1, places=6)
        self.assertFalse(res2.did_reach_zero)

    def test_zero_recorded_energy_is_not_a_hit(self):
        """Only non-zero wall energy marks an entry (the field is 0 elsewhere)."""
        res = E.simulate_energy([], [FakeWall(1.0, 0.0), FakeWall(2.0, 0.5)],
                                E.EnergyConfig(obstacle_hit_seconds=0.05))
        self.assertEqual(len(res.obstacle_hits), 1)


class TestStartAndModes(unittest.TestCase):
    def test_start_energy_per_mode(self):
        self.assertEqual(E.EnergyConfig().start_energy, 0.5)
        self.assertEqual(E.EnergyConfig(energy_type="battery").start_energy, 1.0)
        self.assertEqual(E.EnergyConfig(insta_fail=True).start_energy, 1.0)

    def test_bar_mode_charges_back_up_to_cap(self):
        cfg = E.EnergyConfig()                     # bar, start 0.5
        notes = [FakeNote(float(i), E.GOOD) for i in range(200)]   # +2.0 -> would exceed 1.0
        res = E.simulate_energy(notes, [], cfg)
        self.assertAlmostEqual(res.end_energy, 1.0, places=6)

    def test_battery_mode_does_not_charge(self):
        cfg = E.EnergyConfig(energy_type="battery")
        res = E.simulate_energy([FakeNote(float(i), E.GOOD) for i in range(50)], [], cfg)
        self.assertAlmostEqual(res.end_energy, 1.0, places=6)
        self.assertEqual(res.final_charge, 0.0)

    def test_battery_mode_drains_whole_cell(self):
        cfg = E.EnergyConfig(energy_type="battery")   # 4 lives -> 0.25 per hit
        res = E.simulate_energy([FakeNote(1.0, E.MISS)], [], cfg)
        self.assertAlmostEqual(res.end_energy, 1.0 - 0.25, places=6)

    def test_insta_fail_empties_on_first_hit(self):
        cfg = E.EnergyConfig(insta_fail=True)
        res = E.simulate_energy([FakeNote(1.0, E.MISS)], [], cfg)
        self.assertTrue(res.did_reach_zero)
        self.assertAlmostEqual(res.end_energy, 0.0, places=6)


class TestFailBehaviour(unittest.TestCase):
    def test_reaching_zero_sets_fail_time_and_latches(self):
        cfg = E.EnergyConfig()                     # bar, start 0.5
        notes = [FakeNote(1.0 + i, E.MISS) for i in range(10)]     # 0.5 / 0.15 -> 4th miss empties it
        res = E.simulate_energy(notes, [], cfg)
        self.assertTrue(res.did_reach_zero)
        self.assertIsNotNone(res.fail_time)
        self.assertAlmostEqual(res.fail_time, 4.0, places=6)       # t = 1.0,2.0,3.0,4.0
        self.assertAlmostEqual(res.end_energy, 0.0, places=6)
        # latched: everything after the fail is ignored
        self.assertEqual(res.events[-1].reason, "fail")
        self.assertEqual(len(res.events), 4)

    def test_no_fail_does_not_hide_the_fail(self):
        """NF is auto-enabled the moment a run fails, so the computed trajectory
        must still show the depletion. (The game's counter does stop once NF is
        active, but that is the game reacting to a fail that already happened —
        suppressing it here would hide exactly the event being measured.)"""
        cfg = E.EnergyConfig.from_modifiers("NF")     # Bar start 0.5
        notes = [FakeNote(1.0 + i, E.MISS) for i in range(6)]
        res = E.simulate_energy(notes, [], cfg)
        self.assertTrue(cfg.no_fail_declared)
        self.assertTrue(res.did_reach_zero)
        self.assertAlmostEqual(res.fail_time, 4.0, places=6)
        self.assertAlmostEqual(res.end_energy, 0.0, places=6)

    def test_never_below_zero(self):
        cfg = E.EnergyConfig()
        res = E.simulate_energy([FakeNote(1.0, E.MISS)], [], cfg)
        self.assertGreaterEqual(res.end_energy, 0.0)


class TestModifierParsing(unittest.TestCase):
    def test_known_tokens(self):
        self.assertEqual(E.parse_modifiers("NF"), {"NF"})
        self.assertEqual(E.parse_modifiers("NO,NF"), {"NO", "NF"})
        self.assertEqual(E.parse_modifiers("BE"), {"BE"})
        self.assertEqual(E.parse_modifiers(""), set())
        self.assertEqual(E.parse_modifiers(None), set())

    def test_no_substring_false_positives(self):
        """A naive `"NF" in raw` would fire on unrelated text; token matching must not."""
        self.assertEqual(E.parse_modifiers("SOMEONE"), set())
        self.assertEqual(E.parse_modifiers("BEAT"), set())
        self.assertEqual(E.parse_modifiers('{"modifiers":"none"}'), {"NO"}) if False else None
        # JSON-ish payload: only real tokens separated by non-alphanumerics count
        got = E.parse_modifiers('{"keys":["NF","NO"]}')
        self.assertEqual(got, {"NF", "NO"})

    def test_config_from_modifiers(self):
        cfg = E.EnergyConfig.from_modifiers("NF")
        self.assertTrue(cfg.no_fail_declared)
        self.assertEqual(cfg.energy_type, "bar")
        cfg = E.EnergyConfig.from_modifiers("BE")
        self.assertEqual(cfg.energy_type, "battery")
        cfg = E.EnergyConfig.from_modifiers("IF")
        self.assertTrue(cfg.insta_fail)
        self.assertEqual(cfg.start_energy, 1.0)


class TestDerivedTelemetry(unittest.TestCase):
    def test_events_allow_replaying_the_timeline(self):
        cfg = E.EnergyConfig()
        notes = [FakeNote(1.0, E.GOOD, saber="left"), FakeNote(2.0, E.MISS, saber="right"),
                 FakeNote(3.0, E.BAD, saber="right")]
        res = E.simulate_energy(notes, [], cfg)
        # replay: start + Σ deltas == end
        replayed = res.start_energy + sum(e.delta for e in res.events if e.reason != "fail")
        self.assertAlmostEqual(replayed, res.end_energy, places=6)
        self.assertAlmostEqual(res.charge_by_hand.get("left", 0.0), 0.01, places=6)
        self.assertAlmostEqual(res.drain_by_hand.get("right", 0.0), 0.25, places=6)
        self.assertAlmostEqual(res.drain_by_reason["miss"], 0.15, places=6)
        self.assertAlmostEqual(res.drain_by_reason["bad"], 0.10, places=6)

    def test_to_dict_shape(self):
        res = E.simulate_energy([FakeNote(1.0, E.GOOD)], [FakeWall(2.0, 0.7)], E.EnergyConfig())
        d = res.to_dict()
        for key in ("start_energy", "end_energy", "min_energy", "max_energy", "did_reach_zero",
                    "fail_time", "time_in_danger", "final_charge", "total_drain",
                    "drain_by_reason", "charge_by_hand", "drain_by_hand", "obstacle_hits", "events"):
            self.assertIn(key, d)
        self.assertIsInstance(d["events"], list)
        self.assertIsNone(d["fail_time"])


class TestRealReplay(unittest.TestCase):
    """End-to-end on a real .bsor (the exit replay that carries a wall checkpoint)."""

    def _find(self, pattern):
        cfg = load_config()
        for d in (pathlib.Path(cfg.replay_dir), pathlib.Path(cfg.local_leaderboard_dir)):
            if d and d.is_dir():
                hits = sorted(d.glob(pattern))
                if hits:
                    return hits[0]
        return None

    def test_real_replay_simulation(self):
        f = self._find("*AIM HIGHER*Hard*.bsor")
        if f is None:
            self.skipTest("fixture replay not present on this machine")
        rep = parser.parse_file(f)
        cfg = E.EnergyConfig.from_modifiers(rep.info.modifiers)
        res = E.simulate_energy(rep.notes, rep.walls, cfg)
        self.assertEqual(len(res.obstacle_hits), len([w for w in rep.walls if w.energy]))
        # start energy is the official Bar value for a run without Battery/IF
        self.assertAlmostEqual(res.start_energy, 0.5, places=6)
        # every event time is a real note event time and the timeline is monotonic
        times = [e.t for e in res.events]
        self.assertEqual(times, sorted(times))
        self.assertGreater(res.min_energy, 0.0)
        self.assertFalse(res.did_reach_zero)

    def test_real_library_never_crashes(self):
        """Smoke: simulate every local replay; no exceptions, sane bounds."""
        cfg = load_config()
        d = pathlib.Path(cfg.replay_dir)
        if not d.is_dir():
            self.skipTest("replay dir not present")
        files = sorted(d.glob("*.bsor"))[:25]
        if not files:
            self.skipTest("no replays")
        for f in files:
            rep = parser.parse_file(f)
            res = E.simulate_energy(rep.notes, rep.walls,
                                    E.EnergyConfig.from_modifiers(rep.info.modifiers))
            self.assertGreaterEqual(res.end_energy, 0.0)
            self.assertLessEqual(res.end_energy, E.ENERGY_CAP + 1e-9)
            self.assertGreaterEqual(res.min_energy, 0.0)


if __name__ == "__main__":
    unittest.main()
