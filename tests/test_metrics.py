"""计分与指标单元测试（合成数据，不依赖游戏文件）。"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from backend.bsor.models import (  # noqa: E402
    Replay, ReplayInfo, NoteEvent, NoteCutInfo, GOOD, BAD, MISS, BOMB,
    SCORING_NORMAL,
)
from backend.bsor.parser import FRAME_DTYPE  # noqa: E402
from backend.analysis.scoring import compute_score, cut_scores  # noqa: E402
from backend.config import Config  # noqa: E402
from backend.analysis.engine import analyze_replay  # noqa: E402


def note_id(scoring=3, line=1, layer=1, color=1, direction=1):
    return scoring * 10000 + line * 1000 + layer * 100 + color * 10 + direction


def make_cut(before=1.0, after=1.0, dist=0.0, saber=1, speed=20.0, tdev=0.0):
    return NoteCutInfo(
        speed_ok=True, direction_ok=True, saber_type_ok=True,
        was_cut_too_soon=False, saber_speed=speed,
        saber_dir=(0.0, -1.0, 0.0), saber_type=saber,
        time_deviation=tdev, cut_dir_deviation=0.0,
        cut_point=(0.0, 1.2, 0.5), cut_normal=(0.0, 0.0, -1.0),
        cut_distance_to_center=dist, cut_angle=90.0,
        before_cut_rating=before, after_cut_rating=after)


def good(t, before=1.0, after=1.0, dist=0.0, color=1, saber=1, speed=20.0, tdev=0.0):
    return NoteEvent(note_id(color=color), t, t - 1.0, GOOD, GOOD,
                     make_cut(before, after, dist, saber, speed, tdev))


def make_replay(notes, duration=60.0):
    r = Replay()
    r.info = ReplayInfo(score=0, fail_time=-1.0)
    r.notes = notes
    n = int(duration * 120)
    frames = np.zeros(n, dtype=FRAME_DTYPE)
    frames["time"] = np.arange(n) / 120.0
    frames["fps"] = 120
    # 手在 x 轴匀速移动，制造非零速度
    frames["pose"][:, 7] = np.arange(n) * 0.01    # left x
    frames["pose"][:, 14] = np.arange(n) * 0.012  # right x
    frames["pose"][:, 3:7] = [0, 0, 0, 1]
    frames["pose"][:, 10:14] = [0, 0, 0, 1]
    frames["pose"][:, 17:21] = [0, 0, 0, 1]
    r.frames = frames
    return r


class TestCutScores(unittest.TestCase):
    def test_perfect_note(self):
        n = good(1.0)
        self.assertEqual(cut_scores(n), (70, 15, 30))

    def test_center_distance_scoring(self):
        # dist=0.3 -> center=0；dist=0.15 -> center=round(15*0.5)=8（银行家舍入）
        n1 = good(1.0, dist=0.3)
        n2 = good(1.0, dist=0.15)
        self.assertEqual(cut_scores(n1)[1], 0)
        self.assertIn(cut_scores(n2)[1], (7, 8))

    def test_uncapped_ratings_clamped(self):
        n = good(1.0, before=1.5, after=2.0)
        b, c, a = cut_scores(n)
        self.assertEqual(b, 70)
        self.assertEqual(a, 30)


class TestMultiplierAndAccuracy(unittest.TestCase):
    def test_three_perfect_notes(self):
        r = make_replay([good(1.0), good(2.0), good(3.0)])
        res = compute_score(r)
        # mult: 1,2,2 -> 115+230+230
        self.assertEqual(res.total_score, 575)
        self.assertAlmostEqual(res.accuracy, 1.0, places=6)
        self.assertEqual(res.max_combo, 3)

    def test_miss_breaks_combo_and_multiplier(self):
        miss = NoteEvent(note_id(color=1), 2.0, 1.0, MISS, MISS, None)
        r = make_replay([good(1.0), miss, good(3.0)])
        res = compute_score(r)
        # max: 115*1 + 115*2 + 115*2 = 575; score: 115*1 + 0 + 115*1 = 230
        self.assertEqual(res.total_score, 230)
        self.assertAlmostEqual(res.accuracy, 230 / 575, places=6)
        self.assertEqual(res.max_combo, 1)

    def test_cut_bomb_breaks_full_combo(self):
        """A cut bomb breaks the combo (like the real game) -> no FC badge.

        counts["bomb"] counts only bombs actually CUT: untouched bombs produce
        no event in the BSOR scoring stream (2026-08 semantics decision).
        """
        bomb = NoteEvent(-1, 2.0, 1.0, BOMB, BOMB, None)   # noteID -1 = bomb rule
        r = make_replay([good(1.0), bomb, good(3.0)])
        res = analyze_replay(r, Config(fatigue_edge_seconds=20), repo=None,
                             save=False)
        s = res["summary"]
        self.assertEqual(s["bomb_count"], 1)
        self.assertFalse(s["full_combo"])
        self.assertEqual(s["good_count"], 2)

    def test_no_bomb_events_keeps_full_combo(self):
        """A clean run (no bad/miss/cut-bomb events) is still a full combo."""
        r = make_replay([good(1.0), good(2.0), good(3.0)])
        res = analyze_replay(r, Config(fatigue_edge_seconds=20), repo=None,
                             save=False)
        self.assertTrue(res["summary"]["full_combo"])

    def test_block_accuracy_official_style(self):
        """block_accuracy = 官方口径 per-block 曲线（2026-08 修正）。

        good(1.0) → miss(2.0) → good(3.0)：
        - t=1.0: score=115, max=115 → 1.0
        - t=2.0(miss): score 不变 115, max=345 → 115/345 ≈ 0.3333（惩罚点下降）
        - t=3.0: score=230, max=575 → 0.4
        终点 0.4 == replay 记录 accuracy（score/maxScore 官方口径）。
        """
        miss = NoteEvent(note_id(color=1), 2.0, 1.0, MISS, MISS, None)
        r = make_replay([good(1.0), miss, good(3.0)])
        res = compute_score(r)
        self.assertEqual([round(t, 1) for t, _ in res.block_accuracy], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(res.block_accuracy[0][1], 1.0, places=4)
        self.assertAlmostEqual(res.block_accuracy[1][1], 115 / 345, places=4)
        self.assertAlmostEqual(res.block_accuracy[2][1], 230 / 575, places=4)
        # 曲线终点 == accuracy 字段（与 replay 记录对齐的关键）
        self.assertAlmostEqual(res.block_accuracy[-1][1], res.accuracy, places=6)

    def test_recomputed_matches_fixture(self):
        """真实 fixture（若存在）：重算分 == 记录分。"""
        from tests.test_bsor_parser import FIXTURE
        if not FIXTURE.exists():
            self.skipTest("fixture 不存在")
        from backend.bsor.parser import parse_file
        replay = parse_file(FIXTURE)
        self.assertEqual(compute_score(replay).total_score, replay.info.score)


class TestEngineEndToEnd(unittest.TestCase):
    def test_analyze_replay_synthetic(self):
        notes = []
        # 前 30 秒右手为主、后 30 秒加入左手并制造失误
        for i in range(30):
            notes.append(good(1.0 + i, color=1, saber=1, speed=25.0))
        for i in range(30):
            color = 0 if i % 3 == 0 else 1
            saber = 0 if color == 0 else 1
            notes.append(good(32.0 + i, color=color, saber=saber, speed=20.0))
        notes.append(NoteEvent(note_id(color=1), 61.0, 60.0, MISS, MISS, None))
        r = make_replay(notes, duration=65.0)
        cfg = Config(window_seconds=30, window_step_seconds=10,
                     fatigue_edge_seconds=20)
        result = analyze_replay(r, cfg, repo=None, save=False)
        s = result["summary"]
        self.assertEqual(s["good_count"], 60)
        self.assertEqual(s["miss_count"], 1)
        self.assertFalse(s["full_combo"])
        # 固定窗口退役（2026）：引擎不再产出 windows，改产出 note_groups
        self.assertNotIn("windows", result)
        self.assertTrue(result["note_groups"])
        self.assertIn("reversal", result["motion"])
        self.assertIn("left", result["motion"]["reversal"])
        deltas = result["fatigue"].get("deltas", {}) if result["fatigue"].get("available") else {}
        self.assertIn("center", deltas)
        self.assertIn("miss_rate", deltas)


if __name__ == "__main__":
    unittest.main()
