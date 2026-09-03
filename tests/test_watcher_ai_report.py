"""Regression guard: the analysis pipeline NEVER generates reports.

History:
- 2026-08 (v2.0.1): watcher.process_file passed the build_context FUNCTION
  into run_ai_report's context slot; json.dumps crashed before any try/except,
  silently killing every pipeline-generated report (batch / one-click refresh /
  analyze-latest). The fix made pipeline reports actually run.
- 2026-09 (v2.1.0): that fix exposed a design collision — a post-cache-clear
  full batch called the LLM once per replay (~20s each, hours for 300+ plays,
  the task appeared stuck at "batch analysis"). User decision: the analysis
  pipeline no longer generates reports AT ALL. Reports are generated
  exclusively on demand via /api/ai/analyze/{id} (ai/report.run_ai_report; the
  settings toggle ai.ai_report_enabled decides LLM vs rule there).

These tests pin the new contract: analysis must not call the LLM report layer
and must not write ai_reports rows.
"""
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.watcher import ReplayPipeline  # noqa: E402


def _fake_replay():
    info = SimpleNamespace(
        map_hash="AABBCC", song_name="Song", mapper="M", difficulty="Expert",
        mode="Standard", environment="Default", modifiers="", score=100,
        jump_distance=1.0, left_handed=False, height=1.7, start_time=0.0,
        fail_time=0.0, speed=1.0, won=True, timestamp_int=0, player_id="1",
        player_name="P", platform="", tracking_system="", hmd="",
        controller="", game_version="", version="")
    return SimpleNamespace(file_sha256="deadbeef", info=info,
                           controller_offsets=None, notes=[], frame_count=0)


SUMMARY = {
    "score_recomputed": 100, "score_effective": 100, "has_nf": False,
    "accuracy": 1.0, "max_combo": 10, "full_combo": True, "duration": 1.0,
    "fps_median": 90.0, "frame_count": 0, "note_count": 0,
    "good_count": 0, "bad_count": 0, "miss_count": 0, "bomb_count": 0,
    "completion_status": "completed",
}


class TestPipelineNeverGeneratesReports(unittest.TestCase):
    def test_process_file_report_free(self):
        """process_file must never touch run_ai_report / save_report."""
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "_tmp") as td:
            p = pathlib.Path(td) / "a.bsor"
            p.write_bytes(b"x")
            repo = MagicMock()
            repo.get_replay.return_value = None
            pipeline = ReplayPipeline(MagicMock(), repo, MagicMock())
            calls = []

            def fake_report(*args, **kwargs):
                calls.append(args)
                return {"status": "rule_based", "report_id": "r1"}

            with patch("backend.watcher.parse_file",
                       return_value=_fake_replay()), \
                 patch("backend.watcher.analyze_replay",
                       return_value={"summary": SUMMARY}), \
                 patch("backend.ai.run_ai_report",
                       side_effect=fake_report):   # watcher binds the package attr
                out = pipeline.process_file(str(p))

            self.assertEqual(calls, [])                     # LLM layer untouched
            repo.save_report.assert_not_called()            # no ai_reports row
            self.assertNotIn("ai_report", out)              # no report in the result


if __name__ == "__main__":
    unittest.main()
