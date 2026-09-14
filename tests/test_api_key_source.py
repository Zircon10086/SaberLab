"""AI key resolution: the LOCAL config file is the only source (2026-09 user decision).

Background: a machine-wide `DEEPSEEK_API_KEY` (user-scope registry variable, set for
another tool) reached double-click launches, so a never-configured install displayed a
key that was not its own. The app therefore reads the key from the local `.env` next to
its config and **ignores the process environment entirely**.

Key-shaped probe values are assembled at runtime (a realistic literal would be refused by
any secret scanner and is indistinguishable from a leak).
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Config, read_env_file
from backend.config.schema import get_schema
from backend.config.service import ConfigService

ENV_NAME = "DEEPSEEK_API_KEY"
# runtime-assembled probes: "sk-" + a patterned body, never a source literal
PROBE = "sk-" + ("".join(chr(97 + (i * 7) % 26) for i in range(28))) + "1234"
OTHER = "sk-" + ("".join(chr(98 + (i * 5) % 24) for i in range(28))) + "9999"


class KeyResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.env_path = self.tmp / ".env"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self) -> Config:
        return Config(dotenv_path=self.env_path)

    # ---- the decision itself ------------------------------------------------
    def test_environment_variable_is_ignored(self):
        """An ambient DEEPSEEK_API_KEY must NOT configure the app."""
        with mock.patch.dict(os.environ, {ENV_NAME: PROBE}, clear=False):
            cfg = self.config()
            self.assertEqual(cfg.ai_api_key, "")
            self.assertEqual(cfg.ai_api_key_source(), "")
            self.assertEqual(cfg.ai_api_key_source_name(), "")

    def test_local_file_is_used(self):
        self.env_path.write_text(f"# comment\n{ENV_NAME}={PROBE}\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = self.config()
            self.assertEqual(cfg.ai_api_key, PROBE)
            self.assertEqual(cfg.ai_api_key_source(), "env_file")
            self.assertEqual(cfg.ai_api_key_source_name(), ".env")

    def test_local_file_beats_environment_variable(self):
        """Both present -> the file wins (it is the install's own configuration)."""
        self.env_path.write_text(f"{ENV_NAME}={OTHER}\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {ENV_NAME: PROBE}, clear=False):
            self.assertEqual(self.config().ai_api_key, OTHER)

    def test_quotes_and_spacing_are_stripped(self):
        self.env_path.write_text(f'  {ENV_NAME} = "{PROBE}"  \n', encoding="utf-8")
        self.assertEqual(self.config().ai_api_key, PROBE)

    def test_empty_or_missing_file_is_unconfigured(self):
        self.assertEqual(self.config().ai_api_key, "")      # no file at all
        self.env_path.write_text(f"{ENV_NAME}=\n", encoding="utf-8")
        self.assertEqual(self.config().ai_api_key, "")      # empty value
        self.env_path.write_text(f"# {ENV_NAME}={PROBE}\n", encoding="utf-8")
        self.assertEqual(self.config().ai_api_key, "")      # commented out

    def test_release_layout_uses_the_exe_directory(self):
        """A release has `<exe>/.env` and `<exe>/config/config.yaml`.

        The secrets file is NOT inside config/: an earlier revision looked for
        `config/.env` and silently read nothing in a real install (caught by the frozen
        acceptance run).

        The config file is written inline instead of copied from
        `config/config.yaml.example`, which is not part of the repository (that directory
        is kept local on purpose), so this test also runs on a fresh clone.
        """
        root = self.tmp / "app"
        (root / "config").mkdir(parents=True)
        (root / "config" / "config.yaml").write_text(
            "game:\n  instance_root: ''\nserver:\n  port: 6980\n", encoding="utf-8")
        (root / ".env").write_text(f"{ENV_NAME}={PROBE}\n", encoding="utf-8")
        from backend.config import load_config
        cfg = load_config(root / "config" / "config.yaml")
        self.assertEqual(cfg.dotenv_path, root / ".env")
        self.assertEqual(cfg.ai_api_key, PROBE)

    def test_reads_fresh_on_every_access(self):
        """A saved key applies without a restart; a removed one disappears."""
        self.env_path.write_text(f"{ENV_NAME}={PROBE}\n", encoding="utf-8")
        cfg = self.config()
        self.assertEqual(cfg.ai_api_key, PROBE)
        self.env_path.write_text(f"{ENV_NAME}={OTHER}\n", encoding="utf-8")
        self.assertEqual(cfg.ai_api_key, OTHER)
        self.env_path.unlink()
        self.assertEqual(cfg.ai_api_key, "")

    def test_read_env_file_helper(self):
        self.env_path.write_text(f"{ENV_NAME}={PROBE}\nOTHER=value\n", encoding="utf-8")
        self.assertEqual(read_env_file(self.env_path), {ENV_NAME: PROBE, "OTHER": "value"})
        self.assertEqual(read_env_file(self.env_path, {ENV_NAME}), {ENV_NAME: PROBE})
        self.assertEqual(read_env_file(self.tmp / "missing.env"), {})

    # ---- settings-page contract --------------------------------------------
    def test_settings_secret_configured_contract(self):
        self.env_path.write_text(f"{ENV_NAME}={PROBE}\n", encoding="utf-8")
        item = next(s for s in get_schema() if s["key"] == "ai.api_key")
        val = ConfigService()._get_value(self.config(), item)
        self.assertTrue(val["configured"])
        self.assertEqual(val["masked"], PROBE[:5] + "••••••••" + PROBE[-4:])
        self.assertEqual(val["source"], "env_file")
        self.assertEqual(val["source_name"], ".env")

    def test_settings_secret_unconfigured_contract(self):
        with mock.patch.dict(os.environ, {ENV_NAME: PROBE}, clear=False):
            item = next(s for s in get_schema() if s["key"] == "ai.api_key")
            val = ConfigService()._get_value(self.config(), item)
            self.assertEqual(val, {"configured": False, "masked": None,
                                   "source": "", "source_name": ""})

    def test_masking_hides_short_values(self):
        short = "sk-" + "z" * 8
        self.env_path.write_text(f"{ENV_NAME}={short}\n", encoding="utf-8")
        item = next(s for s in get_schema() if s["key"] == "ai.api_key")
        val = ConfigService()._get_value(self.config(), item)
        self.assertTrue(val["configured"])
        self.assertNotIn(short, val["masked"])


if __name__ == "__main__":
    unittest.main()
