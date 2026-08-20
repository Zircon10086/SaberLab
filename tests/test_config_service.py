"""Config Service 单元测试（developrules.md §22）。"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_config
from backend.config.service import (ConfigService, check_paths, derive_paths,
                                    normalize_path)
from backend.config.schema import get_schema


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
        self.assertEqual(len(results), 4)       # 根 + 3 派生
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
        cfg_copy = tmpdir / "config.yaml"
        cfg_copy.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        svc = ConfigService(config_path=cfg_copy)
        res = svc.save_values({"analysis.window_step_seconds": "2.5"})
        self.assertTrue(res["saved"])
        # 分析参数不需要重启
        self.assertFalse(res["restart_required"])
        raw = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
        self.assertEqual(raw["analysis"]["window_step_seconds"], 2.5)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_corrupt_yaml_backed_up_not_silently_wiped(self):
        """损坏的 config.yaml：原内容必须备份，而非被后续保存静默清空（P0-2.2）。"""
        import tempfile
        import yaml
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        try:
            cfg_copy = tmpdir / "config.yaml"
            corrupt_text = "game:\n  instance_root: [unclosed"
            cfg_copy.write_text(corrupt_text, encoding="utf-8")

            svc = ConfigService(config_path=cfg_copy)
            # 触发读取（解析失败 → 备份）+ 保存新值
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


if __name__ == "__main__":
    unittest.main()
