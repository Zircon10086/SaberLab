"""Settings toggle "Use AI for Reports" (2026-08): ai.ai_report_enabled.

Checked  -> run_ai_report calls the LLM.
Unchecked -> deterministic rule report, LLM never called (status rule_based).
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai.report import run_ai_report  # noqa: E402
from backend.config import Config  # noqa: E402
from backend.db.repository import Repository  # noqa: E402


class _FakeLLM:
    """Records whether chat() was ever called (must not be for rule-only)."""
    def __init__(self):
        self.called = False
        self.provider = "fake"
        self.model = "fake-model"
        self.configured = True

    def chat(self, messages, temperature=None, max_tokens=None):
        self.called = True
        return "# Fake AI report"


class _FakeRepo:
    def __init__(self):
        self.saved = []

    def save_report(self, rep):
        self.saved.append(rep)


class TestAiReportToggle(unittest.TestCase):
    def setUp(self):
        self.repo = _FakeRepo()
        self.ctx = {"replay": {"song_name": "T", "difficulty": "Expert",
                               "score": 1, "score_recomputed": 1, "accuracy": 0.5,
                               "good": 1, "bad": 0, "miss": 0, "full_combo": True},
                    "hands": {"left": {}, "right": {}},
                    "fatigue": {}, "history_same_map": []}

    def test_unchecked_uses_rule_report_without_llm(self):
        cfg = Config(ai_report_enabled=False)
        client = _FakeLLM()
        rep = run_ai_report(self.repo, cfg, "r1", client, self.ctx, lang="zh-CN")
        self.assertEqual(rep["status"], "rule_based")
        self.assertFalse(client.called, "LLM must NOT be called when the toggle is off")
        self.assertEqual(self.repo.saved[0]["status"], "rule_based")
        self.assertIn("规则报告", self.repo.saved[0]["report_md"])

    def test_checked_calls_llm(self):
        cfg = Config(ai_report_enabled=True)
        client = _FakeLLM()
        rep = run_ai_report(self.repo, cfg, "r1", client, self.ctx, lang="en-US")
        self.assertEqual(rep["status"], "ok")
        self.assertTrue(client.called, "LLM must be called when the toggle is on")
        self.assertEqual(self.repo.saved[0]["report_md"], "# Fake AI report")

    def test_unchecked_rule_report_language(self):
        """Rule report still follows the UI language when the toggle is off."""
        cfg = Config(ai_report_enabled=False)
        client = _FakeLLM()
        run_ai_report(self.repo, cfg, "r1", client, self.ctx, lang="ja-JP")
        self.assertFalse(client.called)
        self.assertIn("ルールベースレポート", self.repo.saved[0]["report_md"])


if __name__ == "__main__":
    unittest.main()
