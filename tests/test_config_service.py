"""Config Service 单元测试（developrules.md §22）。"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_config
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
        self.assertTrue(results[0].ok)          # 根目录
        self.assertEqual(len(results), 5)       # 根 + 4 派生（含可选 LL 目录，2026-09）
        self.assertTrue(all(r.ok for r in results))

    def test_invalid_root(self):
        results = check_paths("D:/__nonexistent_root__")
        self.assertFalse(results[0].ok)
        self.assertEqual(len(results), 1)       # 根失败则不再检查派生


class TestConfigService(unittest.TestCase):
    def test_load_view(self):
        svc = ConfigService()
        view = svc.view()
        self.assertTrue(isinstance(view.instance_root, str))
        self.assertTrue(isinstance(view.replay_dir, str))
        # secret 不返回
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
        """临时副本上验证原子写入与 round-trip（不动真实 config.yaml）。"""
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
            # 回读验证
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(
                raw["game"]["instance_root"],
                "D:/BSManager/BSInstances/1.40.8")
            # 原子写入不留 tmp 残渣
            self.assertFalse((tmpdir / "config.yaml.tmp").exists())
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestSchema(unittest.TestCase):
    def test_schema_structure(self):
        schema = get_schema()
        self.assertGreater(len(schema), 10)
        keys = [s["key"] for s in schema]
        # 每个 item 必须有完整元数据
        for s in schema:
            for field in ("key", "label", "type", "description",
                          "restart_required", "required", "sensitive", "group"):
                self.assertIn(field, s, f"{s.get('key')} 缺 {field}")
        # 关键路径项存在
        self.assertIn("game.instance_root", keys)
        self.assertIn("game.replay_dir", keys)
        self.assertIn("game.custom_levels_dir", keys)
        self.assertIn("ai.api_key", keys)
        # secret 项标记敏感
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
        # secret 返回的是 {configured, masked}，不是明文
        self.assertIsInstance(values["ai.api_key"], dict)
        self.assertIn("configured", values["ai.api_key"])
        self.assertNotIn("ai_api_key", values)

    def test_save_unknown_key_rejected(self):
        svc = ConfigService()
        res = svc.save_values({"no.such_key": "x"})
        self.assertFalse(res["saved"])
        self.assertIn("未知配置项", res["error"])

    def test_save_analysis_values(self):
        """临时副本上保存分析参数（不需要重启），验证 round-trip。"""
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
            # 分析参数不需要重启
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
        """损坏的 config.yaml：原内容必须备份，而非被后续保存静默清空（P0-2.2）。"""
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
            # 触发读取（解析失败 → 备份）+ 保存新值；the expected
            # "[config] Failed to parse..." warning is silenced here (it is
            # test noise, not a real corruption — the temp file is deliberate)
            with contextlib.redirect_stdout(io.StringIO()):
                res = svc.save_values({"analysis.window_step_seconds": "2.0"})
            self.assertTrue(res["saved"])
            # 原损坏内容被备份（可人工恢复），且存在带时间戳的备份文件
            backups = list(tmpdir.glob("config.yaml.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), corrupt_text)
            # 保存后的新文件是合法 yaml
            raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
            self.assertEqual(raw["analysis"]["window_step_seconds"], 2.0)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestStarPalette(unittest.TestCase):
    def test_schema_item(self):
        """player.star_palette：enum 下拉、默认 community、无需重启。"""
        schema = get_schema()
        item = next(s for s in schema if s["key"] == "player.star_palette")
        self.assertEqual(item["type"], "enum")
        self.assertIn("community", item["enum"])
        self.assertEqual(item["default"], "community")
        self.assertFalse(item["restart_required"])
        self.assertEqual(item["group"], "玩家")

    def test_palette_definitions_valid(self):
        """STAR_PALETTES：id 唯一、tiers 升序、None=∞ 仅限末档、cls 合法。"""
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
        """社区惯例 5 档：灰/绿/黄/红/紫（<3 / <5 / <7 / <9 / 9+）。"""
        pal = get_star_palette("community")
        self.assertIsNotNone(pal)
        self.assertEqual(
            [(t["max"], t["cls"]) for t in pal["tiers"]],
            [(3.0, "star-gray"), (5.0, "star-green"), (7.0, "star-yellow"),
             (9.0, "star-red"), (None, "star-purple")])
        self.assertIsNone(get_star_palette("no_such_palette"))

    def test_save_palette_roundtrip(self):
        """临时副本保存 player.star_palette：round-trip + 无需重启 + Config 回读。"""
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


if __name__ == "__main__":
    unittest.main()
