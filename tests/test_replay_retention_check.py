"""BeatLeader replay-retention check in the game-path checklist (2026-09).

The row reports the mod's "keep latest only" setting (`OverrideOldReplays`),
which deletes older .bsor files of the same map & difficulty on every save
(root cause of the vanished replays, HANDOFF §4.25). Requirements covered here:
the mapping true -> red, false -> green, and a neutral state for everything that
must NOT be reported as a problem (mod absent, key absent, unreadable config);
plus the invariant that this advisory row never changes `valid`.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import service as svc  # noqa: E402
import backend.main as m  # noqa: E402

CONFIG_REL = "UserData/BeatLeader.json"


class RetentionCheckTestCase(unittest.TestCase):
    """Builds a throwaway game root with the pieces check_paths() inspects."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        # Minimal valid instance: root + CustomLevels + replay dir + SongCore cache
        for rel in ("Beat Saber_Data/CustomLevels", "UserData/BeatLeader/Replays"):
            (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        (self.tmp / "UserData/SongCore").mkdir(parents=True, exist_ok=True)
        (self.tmp / "UserData/SongCore/SongHashData.dat").write_bytes(b"x")

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            try:
                p.unlink() if p.is_file() else p.rmdir()
            except OSError:
                pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def write_bl_config(self, text: str) -> None:
        path = self.tmp / CONFIG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def row(self, key="replay_retention"):
        rows = [r for r in svc.check_paths(str(self.tmp)) if r.key == key]
        self.assertEqual(len(rows), 1, f"expected exactly one {key} row")
        return rows[0]


class TestRetentionStates(RetentionCheckTestCase):
    def test_true_is_reported_as_danger(self):
        """The real key name must map to a red row (this is the whole feature)."""
        self.write_bl_config(json.dumps({
            "ConfigVersion": "1.0", "OverrideOldReplays": True,
            "SaveLocalReplays": True, "ReplaySavingOptions": "Exit, Fail, ZeroScore",
        }))
        row = self.row()
        self.assertEqual(row.status, "bad")
        self.assertFalse(row.ok)
        # Wording per AGENTS.md §9.1 (2026-09): lead with the outcome ("older
        # replays will be deleted"), then the action (turn off the in-game option).
        self.assertIn("旧回放会被删除", row.note)
        self.assertIn("keep latest only", row.note)
        self.assertIn("关闭", row.note)
        # the row shows a path inside the game root, matching the other checks
        self.assertEqual(row.label.find("keep latest only") > 0, True)
        self.assertTrue(row.path.endswith(CONFIG_REL))

    def test_false_is_reported_as_ok(self):
        self.write_bl_config(json.dumps({"OverrideOldReplays": False}))
        row = self.row()
        self.assertEqual(row.status, "ok")
        self.assertTrue(row.ok)
        self.assertIn("已关闭", row.note)

    def test_key_absent_is_neutral_not_a_warning(self):
        """Missing key = mod default. The mod has always kept old replays when the
        key is absent (no deletion warnings in the logs), so this must not be red."""
        self.write_bl_config(json.dumps({"ConfigVersion": "1.0",
                                         "SaveLocalReplays": True}))
        row = self.row()
        self.assertEqual(row.status, "note")
        self.assertFalse(row.ok)

    def test_config_absent_is_neutral(self):
        """BeatLeader not installed: nothing to warn about (neutral note, never red)."""
        row = self.row()
        self.assertEqual(row.status, "note")
        self.assertFalse(row.ok)
        # Wording per AGENTS.md §9.1 (2026-09): state the outcome ("can be ignored")
        # rather than the cause ("the mod is not installed").
        self.assertIn("可忽略", row.note)

    def test_unreadable_config_is_neutral(self):
        self.write_bl_config("[[[not: valid: yaml")
        row = self.row()
        self.assertEqual(row.status, "note")
        self.assertIn("无法解析", row.note)

    def test_empty_instance_root_is_neutral(self):
        row = svc.beatleader_replay_deletion_check("")
        self.assertEqual(row.status, "note")
        self.assertIn("游戏根目录", row.note)

    def test_non_boolean_truthy_value_counts_as_enabled(self):
        """YAML `yes`/1 must be treated as enabled: the mod reads it as a bool."""
        for raw in ('OverrideOldReplays: yes', 'OverrideOldReplays: 1'):
            with self.subTest(raw=raw):
                self.write_bl_config(raw)
                self.assertEqual(self.row().status, "bad")

    def test_quoted_false_string_is_not_enabled(self):
        self.write_bl_config('OverrideOldReplays: "False"')
        self.assertEqual(self.row().status, "ok")


class TestChecklistInvariants(RetentionCheckTestCase):
    def test_row_is_last_in_the_checklist(self):
        """Requirement: the warning sits at the bottom of the game-path card."""
        keys = [r.key for r in svc.check_paths(str(self.tmp))]
        self.assertEqual(keys[-1], "replay_retention")
        self.assertEqual(keys[0], "instance_root")

    def test_row_never_touches_validity(self):
        """Advisory only: `valid` depends on root + CustomLevels, so a deletion
        warning must not flip the badge to "invalid" (the path is fine; the fix
        happens inside the game)."""
        self.write_bl_config(json.dumps({"OverrideOldReplays": True}))
        rows = svc.check_paths(str(self.tmp))
        by_key = {r.key: r for r in rows}
        self.assertFalse(by_key["replay_retention"].ok)
        valid = bool(by_key["instance_root"].ok and by_key["custom_levels_dir"].ok)
        self.assertTrue(valid)
        # and the endpoint derives valid the same way
        with mock.patch.object(m, "cfg", mock.Mock(instance_root=str(self.tmp))):
            with mock.patch.object(m, "check_paths",
                                   side_effect=lambda root: svc.check_paths(str(self.tmp))):
                out = m.api_settings_validate(None)
        self.assertTrue(out["valid"])
        self.assertEqual(out["results"][-1]["status"], "bad")
        self.assertIn("status", out["results"][-1])

    def test_missing_root_short_circuits(self):
        rows = svc.check_paths(str(self.tmp / "nope"))
        self.assertEqual([r.key for r in rows], ["instance_root"])
        self.assertEqual(rows[0].status, "bad")

    def test_derived_path_states(self):
        rows = {r.key: r for r in svc.check_paths(str(self.tmp))}
        self.assertEqual(rows["instance_root"].status, "ok")
        self.assertEqual(rows["custom_levels_dir"].status, "ok")
        self.assertEqual(rows["replay_dir"].status, "ok")
        # optional LocalLeaderboard dir missing -> neutral, not red
        self.assertEqual(rows["local_leaderboard_dir"].status, "note")
        # a required folder missing -> red
        (self.tmp / "UserData/BeatLeader/Replays").rmdir()
        rows = {r.key: r for r in svc.check_paths(str(self.tmp))}
        self.assertEqual(rows["replay_dir"].status, "bad")


class TestRealInstance(unittest.TestCase):
    """Read the machine's actual BeatLeader.json through the same code path.

    Skipped when the game config is absent, so the suite stays portable.
    """

    def test_real_config_is_readable(self):
        from backend.config import load_config
        root = load_config().instance_root
        cfg_path = pathlib.Path(root) / CONFIG_REL if root else None
        if not cfg_path or not cfg_path.exists():
            self.skipTest("BeatLeader.json not present on this machine")
        row = svc.beatleader_replay_deletion_check(root)
        self.assertIn(row.status, ("ok", "bad"))
        # the note must always explain what the state means for the user
        self.assertTrue(row.note)


if __name__ == "__main__":
    unittest.main()
