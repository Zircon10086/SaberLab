"""Config Service unit tests (developrules.md §22)."""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import LOOPBACK_HOSTS, _loopback_host, load_config
from backend.config.service import (ConfigService, check_paths, derive_paths,
                                    normalize_path)
from backend.config.schema import (STAR_PALETTES, get_schema,
                                   get_star_palette)


class TestDerivePaths(unittest.TestCase):
    def test_derive_from_root(self):
        d = derive_paths("D:/BSManager/BSInstances/1.40.8")
        self.assertEqual(
            d["custom_levels_dir"],
            "D:/BSManager/BSInstances/1.40.8/Beat Saber_Data/CustomLevels")
        self.assertEqual(
            d["replay_dir"],
            "D:/BSManager/BSInstances/1.40.8/UserData/BeatLeader/Replays")
        self.assertEqual(
            d["songcore_cache"],
            "D:/BSManager/BSInstances/1.40.8/UserData/SongCore/SongHashData.dat")
        self.assertEqual(
            d["local_leaderboard_dir"],
            "D:/BSManager/BSInstances/1.40.8/UserData/LocalLeaderboard/Replays")

    def test_derive_empty(self):
        d = derive_paths("")
        self.assertEqual(d["replay_dir"], "")

    def test_normalize(self):
        self.assertEqual(normalize_path("D:\\A\\B"), "D:/A/B")
        self.assertEqual(normalize_path(""), "")


class TestCheckPaths(unittest.TestCase):
    def test_valid_root(self):
        root = r"D:\BSManager\BSInstances\1.40.8"
        if not pathlib.Path(root).exists():
            self.skipTest("开发机路径不存在")
        results = check_paths(root)
        self.assertTrue(results[0].ok)          # the root directory
        # root + 4 derived (including the optional LL directory, 2026-09) + the replay
        # retention check row (2026-09, not a path item)
        self.assertEqual(len(results), 6)
        self.assertEqual(results[-1].key, "replay_retention")
        self.assertTrue(all(r.ok for r in results[:-1]))
        # that row is an "advisory-level" warning: it depends on the local mod's own
        # settings, can be note/bad, and does not feed into valid
        self.assertIn(results[-1].status, ("ok", "bad", "note"))

    def test_invalid_root(self):
        results = check_paths("D:/__nonexistent_root__")
        self.assertFalse(results[0].ok)
        self.assertEqual(len(results), 1)       # if the root fails, the derived paths are not checked


class TestConfigService(unittest.TestCase):
    def test_load_view(self):
        svc = ConfigService()
        view = svc.view()
        self.assertTrue(isinstance(view.instance_root, str))
        self.assertTrue(isinstance(view.replay_dir, str))
        # secrets are not returned
        self.assertFalse(hasattr(view, "ai_api_key"))
        d = view.to_dict()
        self.assertNotIn("ai_api_key", d)
        self.assertIn("ai_configured", d)

    def test_validate_existing(self):
        cfg = load_config()
        results = check_paths(cfg.instance_root)
        self.assertTrue(results[0].ok)

    def test_save_rejects_empty(self):
        svc = ConfigService()
        res = svc.save_instance_root("")
        self.assertFalse(res["saved"])
        self.assertIn("不能为空", res["error"])

    def test_save_rejects_nonexistent(self):
        svc = ConfigService()
        res = svc.save_instance_root("D:/__nonexistent_root__")
        self.assertFalse(res["saved"])

    def test_save_valid(self):
        """Verify atomic write and round-trip on a temporary copy (the real config.yaml is never touched)."""
        import tempfile
        import yaml
        src = PROJECT_ROOT / "config" / "config.yaml"
        if not src.exists():
            self.skipTest("真实 config 不存在")
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        try:
            cfg_copy = tmpdir / "config.yaml"
            cfg_copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            svc = ConfigService(config_path=cfg_copy)
            res = svc.save_instance_root(r"D:\BSManager\BSInstances\1.40.8")
            self.assertTrue(res["saved"])
            self.assertTrue(res["restart_required"])
            # verified by reading it back
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(
                raw["game"]["instance_root"],
                "D:/BSManager/BSInstances/1.40.8")
            # the atomic write leaves no .tmp leftovers
            self.assertFalse((tmpdir / "config.yaml.tmp").exists())
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestSchema(unittest.TestCase):
    def test_schema_structure(self):
        schema = get_schema()
        self.assertGreater(len(schema), 10)
        keys = [s["key"] for s in schema]
        # every item must carry complete metadata
        for s in schema:
            for field in ("key", "label", "type", "description",
                          "restart_required", "required", "sensitive", "group"):
                self.assertIn(field, s, f"{s.get('key')} 缺 {field}")
        # the key path items exist
        self.assertIn("game.instance_root", keys)
        self.assertIn("game.replay_dir", keys)
        self.assertIn("game.custom_levels_dir", keys)
        self.assertIn("ai.api_key", keys)
        # the secret item is flagged sensitive
        api_key = next(s for s in schema if s["key"] == "ai.api_key")
        self.assertTrue(api_key["sensitive"])
        self.assertEqual(api_key["type"], "secret")

    def test_enum_has_options(self):
        schema = get_schema()
        for s in schema:
            if s["type"] == "enum":
                self.assertTrue(s.get("enum"), f"{s['key']} 缺 enum 选项")


class TestSchemaReadWrite(unittest.TestCase):
    def test_get_all_values_secret_masked(self):
        svc = ConfigService()
        values = svc.get_all_values()
        self.assertIn("ai.api_key", values)
        # a secret returns {configured, masked}, never the plaintext
        self.assertIsInstance(values["ai.api_key"], dict)
        self.assertIn("configured", values["ai.api_key"])
        self.assertNotIn("ai_api_key", values)

    def test_save_unknown_key_rejected(self):
        svc = ConfigService()
        res = svc.save_values({"no.such_key": "x"})
        self.assertFalse(res["saved"])
        self.assertIn("未知配置项", res["error"])

    def test_save_analysis_values(self):
        """Save analysis parameters on a temporary copy (no restart needed) and verify the round-trip."""
        import tempfile
        import yaml
        src = PROJECT_ROOT / "config" / "config.yaml"
        if not src.exists():
            self.skipTest("真实 config 不存在")
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        try:
            cfg_copy = tmpdir / "config.yaml"
            cfg_copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            svc = ConfigService(config_path=cfg_copy)
            res = svc.save_values({"analysis.window_step_seconds": "2.5"})
            self.assertTrue(res["saved"])
            # analysis parameters need no restart
            self.assertFalse(res["restart_required"])
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(raw["analysis"]["window_step_seconds"], 2.5)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestChangedKeys(unittest.TestCase):
    """save_values must report which keys actually changed (2026-08 fix).

    The settings form submits every visible field on each save; side effects
    keyed on submission (analysis cache reset, restart hint) previously fired
    for unchanged values, wiping the analysis cache on every save.
    """

    def setUp(self):
        import tempfile
        src = PROJECT_ROOT / "config" / "config.yaml"
        if not src.exists():
            self.skipTest("真实 config 不存在")
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        cfg_copy = self.tmpdir / "config.yaml"
        cfg_copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        self.svc = ConfigService(config_path=cfg_copy)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_unchanged_resubmission_reports_no_changes(self):
        """Saving the same values twice: the second save changes nothing."""
        values = self.svc.get_all_values()
        first = self.svc.save_values({
            "analysis.slope_group_notes": values["analysis.slope_group_notes"],
            "player.player_name_fallback": "Tester",
        })
        self.assertTrue(first["saved"])
        self.assertIn("player.player_name_fallback", first["changed"])
        second = self.svc.save_values({
            "analysis.slope_group_notes": values["analysis.slope_group_notes"],
            "player.player_name_fallback": "Tester",
        })
        self.assertTrue(second["saved"])
        self.assertEqual(second["changed"], [])
        self.assertFalse(second["restart_required"])

    def test_string_int_coercion_not_counted_as_change(self):
        """Form sends strings; stored value may be typed: "50" vs 50 is no change."""
        values = self.svc.get_all_values()
        raw_n = values["analysis.slope_group_notes"]
        res = self.svc.save_values({"analysis.slope_group_notes": str(raw_n)})
        self.assertEqual(res["changed"], [])

    def test_restart_required_only_for_changed_keys(self):
        """Resubmitting an unchanged restart-required key must not set the flag."""
        values = self.svc.get_all_values()
        res = self.svc.save_values({"server.host": values["server.host"]})
        self.assertTrue(res["saved"])
        self.assertEqual(res["changed"], [])
        self.assertFalse(res["restart_required"])
        res2 = self.svc.save_values({"server.host": "127.0.0.2"})
        self.assertIn("server.host", res2["changed"])
        self.assertTrue(res2["restart_required"])

    def test_secret_always_counts_as_changed(self):
        """A typed secret is a change by definition (stored one is unreadable)."""
        res = self.svc.save_values({"ai.api_key": "sk-test-1234567890"})
        self.assertTrue(res["saved"])
        self.assertIn("ai.api_key", res["changed"])

    def test_analysis_change_detected(self):
        """An actually-changed analysis key is reported (drives cache reset)."""
        res = self.svc.save_values({"analysis.fatigue_edge_seconds": "42.5"})
        self.assertIn("analysis.fatigue_edge_seconds", res["changed"])

    def test_corrupt_yaml_backed_up_not_silently_wiped(self):
        """A corrupt config.yaml: the original content must be backed up rather than silently
        wiped by a later save (P0-2.2)."""
        import contextlib
        import io
        import tempfile
        import yaml
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        try:
            cfg_copy = tmpdir / "config.yaml"
            corrupt_text = "game:\n  instance_root: [unclosed"
            cfg_copy.write_text(corrupt_text, encoding="utf-8")

            svc = ConfigService(config_path=cfg_copy)
            # trigger a read (parse failure → backup) and save a new value; the expected
            # "[config] Failed to parse..." warning is silenced here (it is
            # test noise, not a real corruption — the temp file is deliberate)
            with contextlib.redirect_stdout(io.StringIO()):
                res = svc.save_values({"analysis.window_step_seconds": "2.0"})
            self.assertTrue(res["saved"])
            # the original corrupt content is backed up (recoverable by hand) and a
            # timestamped backup file exists
            backups = list(tmpdir.glob("config.yaml.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), corrupt_text)
            # the file written by the save is valid yaml
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(raw["analysis"]["window_step_seconds"], 2.0)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestStarPalette(unittest.TestCase):
    def test_schema_item(self):
        """player.star_palette: enum dropdown, default community, no restart required."""
        schema = get_schema()
        item = next(s for s in schema if s["key"] == "player.star_palette")
        self.assertEqual(item["type"], "enum")
        self.assertIn("community", item["enum"])
        self.assertEqual(item["default"], "community")
        self.assertFalse(item["restart_required"])
        self.assertEqual(item["group"], "玩家")

    def test_palette_definitions_valid(self):
        """STAR_PALETTES: unique ids, tiers ascending, None=∞ only in the last tier, cls valid."""
        ids = [p["id"] for p in STAR_PALETTES]
        self.assertEqual(len(ids), len(set(ids)))
        valid_cls = {"star-gray", "star-green", "star-yellow",
                     "star-red", "star-purple"}
        for p in STAR_PALETTES:
            tiers = p["tiers"]
            self.assertTrue(tiers, f"{p['id']} 无档位")
            prev = -1.0
            for i, t in enumerate(tiers):
                if t.get("max") is None:
                    self.assertEqual(i, len(tiers) - 1,
                                     f"{p['id']} 的 max=None 不在末档")
                else:
                    self.assertGreater(t["max"], prev,
                                       f"{p['id']} tiers 未升序")
                    prev = t["max"]
                self.assertIn(t["cls"], valid_cls, f"{p['id']} cls 非法")

    def test_community_tiers_order(self):
        """The community convention of 5 tiers: gray/green/yellow/red/purple (<3 / <5 / <7 / <9 / 9+)."""
        pal = get_star_palette("community")
        self.assertIsNotNone(pal)
        self.assertEqual(
            [(t["max"], t["cls"]) for t in pal["tiers"]],
            [(3.0, "star-gray"), (5.0, "star-green"), (7.0, "star-yellow"),
             (9.0, "star-red"), (None, "star-purple")])
        self.assertIsNone(get_star_palette("no_such_palette"))

    def test_save_palette_roundtrip(self):
        """Save player.star_palette on a temporary copy: round-trip + no restart + read back through Config."""
        import tempfile
        import yaml
        src = PROJECT_ROOT / "config" / "config.yaml"
        if not src.exists():
            self.skipTest("真实 config 不存在")
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        cfg_copy = tmpdir / "config.yaml"
        cfg_copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            svc = ConfigService(config_path=cfg_copy)
            res = svc.save_values({"player.star_palette": "community"})
            self.assertTrue(res["saved"])
            self.assertFalse(res["restart_required"])
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(raw["player"]["star_palette"], "community")
            self.assertEqual(load_config(cfg_copy).star_palette, "community")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestServerHost(unittest.TestCase):
    """server.host may only be a loopback name (2026-09 hardening).

    The origin guard refuses every non-loopback Host, so a listen address pointing at
    another network card would leave the UI completely unable to connect, with no hint
    at all — the setting was therefore tightened, and an out-of-range value in an old
    config falls back at load time."""

    def test_schema_offers_loopback_only(self):
        item = next(s for s in get_schema() if s["key"] == "server.host")
        self.assertEqual(item["type"], "enum")
        self.assertEqual(list(item["enum"]), list(LOOPBACK_HOSTS))
        self.assertEqual(item["default"], LOOPBACK_HOSTS[0])
        self.assertTrue(item["restart_required"])

    def test_guard_uses_the_same_set(self):
        """The values the setting can offer must be exactly the values the guard accepts."""
        import backend.main as m
        self.assertEqual(m._LOOPBACK_HOSTS, frozenset(LOOPBACK_HOSTS))

    def test_non_loopback_value_falls_back(self):
        for bad in ("0.0.0.0", "192.168.1.5", "::", "evil.example", "", None, "  "):
            with self.subTest(value=bad):
                self.assertEqual(_loopback_host(bad), LOOPBACK_HOSTS[0])

    def test_loopback_values_are_kept(self):
        for good in LOOPBACK_HOSTS:
            with self.subTest(value=good):
                self.assertEqual(_loopback_host(good), good)
        self.assertEqual(_loopback_host(" 127.0.0.1 "), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
