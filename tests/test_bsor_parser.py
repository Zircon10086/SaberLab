"""BSOR Parser 黄金回归测试（设计文档 §19.1 Fixture #001）。

使用真实 SECRET BOSS Expert Replay。文件不存在时跳过（CI 无游戏环境）。
运行:  .venv\\Scripts\\python.exe -m unittest tests.test_bsor_parser -v
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.bsor.parser import parse_file, parse_bytes, UnsupportedFormatError, BsorError  # noqa: E402
from backend.bsor.models import NoteParams  # noqa: E402
from backend.analysis.scoring import compute_score, cut_scores  # noqa: E402
from backend.analysis.accuracy import analyze_accuracy  # noqa: E402

# 黄金夹具已本地化：复制自 LocalLeaderboard/Replays 的 1786976654 版本
# （原 BeatLeader/Replays 路径下的同名文件已被新记录覆盖）
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "secret-boss-expert.bsor"


class TestBsorParserFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest(f"fixture 不存在: {FIXTURE}")
        cls.replay = parse_file(FIXTURE)

    def test_magic_info(self):
        info = self.replay.info
        self.assertEqual(info.player_id, "76561199673091080")
        self.assertEqual(info.song_name, "SECRET BOSS")
        self.assertEqual(info.difficulty, "Expert")
        self.assertEqual(info.map_hash.upper(),
                         "807E71EB310B8AEBA98A643C3E8C390E24E89A80")
        self.assertEqual(info.score, 1384913)
        self.assertTrue(info.won)
        # 非 ASCII 玩家名必须正确解码（官方编码器长度 bug 场景）
        self.assertTrue(len(info.player_name) > 0)

    def test_counts(self):
        r = self.replay
        self.assertEqual(r.frame_count, 32796)
        self.assertEqual(len(r.notes), 2069)
        c = r.summary_counts()
        self.assertEqual(c["good"], 1993)
        self.assertEqual(c["bad"], 44)
        self.assertEqual(c["miss"], 32)

    def test_controller_offsets_present(self):
        self.assertIsNotNone(self.replay.controller_offsets)

    def test_golden_metrics(self):
        """设计文档 §19.1 断言（Pre/Center/Post 左右手）。"""
        acc = analyze_accuracy(self.replay)
        left, right = acc["hands"]["left"], acc["hands"]["right"]
        self.assertAlmostEqual(left["center_score_avg"], 2.57, delta=0.02)
        self.assertAlmostEqual(right["center_score_avg"], 2.97, delta=0.02)
        self.assertAlmostEqual(left["pre_score_avg"], 68.05, delta=0.02)
        self.assertAlmostEqual(right["pre_score_avg"], 69.13, delta=0.02)
        self.assertAlmostEqual(left["post_score_avg"], 27.69, delta=0.02)
        self.assertAlmostEqual(right["post_score_avg"], 29.62, delta=0.02)

    def test_recomputed_score_matches_recorded(self):
        """官方计分移植：重算总分必须等于 Replay 记录的总分。"""
        result = compute_score(self.replay)
        self.assertEqual(result.total_score, self.replay.info.score)


class TestNoteIdDecode(unittest.TestCase):
    def test_standard_note(self):
        # scoringType=3(Normal), lineIndex=2, layer=1, color=0, dir=5 -> 3*10000+2*1000+1*100+0*10+5
        p = NoteParams.decode(32105)
        self.assertEqual((p.scoring_type, p.line_index, p.note_line_layer,
                          p.color_type, p.cut_direction), (3, 2, 1, 0, 5))
        self.assertEqual(p.saber, "left")

    def test_bomb_detection(self):
        self.assertTrue(NoteParams.decode(32109).cut_direction == 9)
        from backend.bsor.models import NoteEvent
        n = NoteEvent(32109, 1.0, 0.0, 3, 0, None)
        self.assertTrue(n.is_bomb)


class TestParserErrors(unittest.TestCase):
    def test_bad_magic(self):
        with self.assertRaises(UnsupportedFormatError):
            parse_bytes(b"XXXXX" + b"\x00" * 20)

    def test_quest_magic(self):
        import struct
        data = struct.pack("<i", 0x443D3D38) + b"\x00"
        with self.assertRaises(UnsupportedFormatError):
            parse_bytes(data)

    def test_truncated(self):
        import struct
        data = struct.pack("<i", 0x442D3D69) + b"\x01" + b"\x00"  # info section, 截断
        with self.assertRaises(BsorError):
            parse_bytes(data)


if __name__ == "__main__":
    unittest.main()
