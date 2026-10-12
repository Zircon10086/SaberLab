"""Chart density time series regression test (2026-10).

The map detail's time-series card needs density over time for ONE chart, with no replay involved:
overall / left hand / right hand / bombs. It is computed from the chart file on demand
(`maps/resolver.read_chart_timeseries`), so these tests build a tiny chart with known object
positions and check the accounting exactly:

  * every cuttable object lands in the histogram, buckets are 1s apart, left+right = total;
  * bombs are counted separately and never inflate the note density (the replay timeline excludes
    them from its density for the same reason);
  * arcs/chains count as cuts, and a v4 chart whose data lives in a side file is reported as
    unavailable instead of "no notes".
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.maps.resolver import read_chart_timeseries  # noqa: E402

BPM = 120.0          # 1 beat = 0.5s, so beat 2 = 1s, beat 4 = 2s ...


def _write(folder: pathlib.Path, name: str, payload: dict) -> None:
    (folder / name).write_text(json.dumps(payload), encoding="utf-8")


class ChartTimeseriesTest(unittest.TestCase):
    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_density_counts_every_cut_and_splits_hands(self):
        # Beats 0 and 1 are left (x<2), beats 2 and 3 are right; all within the first 1.5s.
        _write(self.folder, "e.dat", {
            "colorNotes": [{"b": 0, "x": 0}, {"b": 1, "x": 1}, {"b": 2, "x": 2}, {"b": 3, "x": 3}],
            "bombNotes": [{"b": 1}, {"b": 3}],
        })
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertTrue(ts["available"])
        self.assertEqual(ts["counts"]["density"], 4)
        self.assertEqual(ts["counts"]["left"], 2)
        self.assertEqual(ts["counts"]["right"], 2)
        self.assertEqual(ts["counts"]["bombs"], 2)
        # buckets are 1s apart and start at the first object's own time
        times = ts["density_t"]
        self.assertEqual(times[0], 0.0)
        self.assertEqual(round(times[1] - times[0], 6), 1.0)
        self.assertEqual(len(times), len(ts["density"]))
        self.assertEqual(len(ts["density"]), len(ts["left"]))

    def test_beats_land_in_their_real_second_at_multiple_bpms(self):
        for bpm in (60, 90, 120, 200, 240):
            with self.subTest(bpm=bpm):
                _write(self.folder, "e.dat", {"colorNotes": [
                    {"b": second * bpm / 60, "x": 0} for second in range(4)
                ]})
                ts = read_chart_timeseries(self.folder, "e.dat", bpm)
                self.assertEqual(ts["density_t"], [0.0, 1.0, 2.0, 3.0])
                self.assertEqual(ts["bucket_counts"]["density"], [1, 1, 1, 1])
                self.assertEqual(ts["counts"]["density"], 4)

    def test_nonzero_first_beat_and_empty_seconds_keep_their_positions(self):
        # At 120 BPM these notes occur at 5, 6 and 8 seconds; second 7 is empty.
        _write(self.folder, "e.dat", {"colorNotes": [
            {"b": beat, "x": 0} for beat in (10, 12, 16)
        ]})
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertEqual(ts["density_t"], [5.0, 6.0, 7.0, 8.0])
        self.assertEqual(ts["bucket_counts"]["density"], [1, 1, 0, 1])

    def test_bucket_width_preserves_time_and_rate_units(self):
        # Half-second buckets: one note per bucket is exactly two notes per second.
        _write(self.folder, "e.dat", {"colorNotes": [
            {"b": beat, "x": 2} for beat in range(4)
        ]})
        ts = read_chart_timeseries(self.folder, "e.dat", BPM, bucket_seconds=0.5)
        self.assertEqual(ts["density_t"], [0.0, 0.5, 1.0, 1.5])
        self.assertEqual(ts["bucket_counts"]["density"], [1, 1, 1, 1])
        self.assertEqual(ts["density"], [2.0] * 4)
        self.assertEqual(ts["right"], ts["density"])
        self.assertEqual(ts["left"], [0.0] * 4)

    def test_smoothed_burst_stays_at_its_actual_time(self):
        _write(self.folder, "e.dat", {"colorNotes":
            [{"b": 2 * second, "x": 0} for second in range(21)]
            + [{"b": 20, "x": 2} for _ in range(20)]})
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertEqual(ts["bucket_counts"]["density"][10], 21)
        self.assertEqual(ts["bucket_counts"]["density"][-1], 1)
        self.assertEqual(sum(ts["bucket_counts"]["density"]), 41)
        peak = max(ts["density"])
        peak_times = [t for t, value in zip(ts["density_t"], ts["density"], strict=True)
                      if value == peak]
        self.assertEqual(peak_times, [8.0, 9.0, 10.0, 11.0, 12.0])

    def test_bombs_do_not_inflate_note_density(self):
        """A bomb burst must not raise the note density (bombs are never cut).

        Checked on the raw per-bucket counts - the smoothed series is a view over them - because
        that is where "bombs are a separate quantity" is decided.
        """
        _write(self.folder, "e.dat", {
            "colorNotes": [{"b": 0, "x": 0}, {"b": 8, "x": 2}],
            # At 120 BPM, beats 4.0-4.4 occur at 2.0-2.2 seconds (bucket #2).
            "bombNotes": [{"b": 4.0}, {"b": 4.2}, {"b": 4.4}, {"b": 8}],
        })
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        buckets = ts["bucket_counts"]
        self.assertEqual(buckets["density"][2], 0)      # no note in that bucket...
        self.assertEqual(buckets["bombs"][2], 3)        # ...only bombs
        self.assertEqual(ts["counts"]["density"], 2)
        self.assertEqual(ts["counts"]["bombs"], 4)

    def test_arcs_and_chains_count_as_cuts(self):
        """Arcs and chains are cut too, so they belong to the note density and to a hand.

        The hand comes from the object's head column: an arc leading from a right column is a
        right-hand cut.
        """
        _write(self.folder, "e.dat", {
            "colorNotes": [{"b": 0, "x": 0}, {"b": 1, "x": 1}],
            "arcs": [{"b": 2, "x": 3}],
            "chains": [{"b": 3, "x": 2}],
        })
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertEqual(ts["counts"]["density"], 4)
        self.assertEqual(ts["counts"]["left"], 2)
        self.assertEqual(ts["counts"]["right"], 2)

    def test_side_file_chart_is_unavailable_not_empty(self):
        """v4 keeps note data in a separate file: that is "cannot draw", not "no notes"."""
        _write(self.folder, "e.dat", {"version": "4.0.0", "colorNotes": [0, 1, 2],
                                      "colorNotesData": [{"b": 0, "x": 0}]})
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertFalse(ts["available"])
        self.assertEqual(ts["density"], [])

    def test_missing_file_and_zero_bpm_are_unavailable(self):
        self.assertFalse(read_chart_timeseries(self.folder, "nope.dat", BPM)["available"])
        _write(self.folder, "e.dat", {"colorNotes": [{"b": 0, "x": 0}]})
        self.assertFalse(read_chart_timeseries(self.folder, "e.dat", 0)["available"])

    def test_empty_chart_is_unavailable(self):
        _write(self.folder, "e.dat", {"colorNotes": []})
        self.assertFalse(read_chart_timeseries(self.folder, "e.dat", BPM)["available"])

    def test_smoothing_spreads_a_burst_without_inventing_notes(self):
        """A chord burst shows as a peak, and smoothing never adds notes to the counts."""
        notes = [{"b": b, "x": 0} for b in range(0, 40)]          # 40 notes over 20s
        notes += [{"b": 20, "x": 2} for _ in range(20)]           # 20-note chord at 10s
        _write(self.folder, "e.dat", {"colorNotes": notes})
        ts = read_chart_timeseries(self.folder, "e.dat", BPM)
        self.assertEqual(ts["counts"]["density"], 60)             # exactly the chart's objects
        peak = max(ts["density"])
        self.assertGreater(peak, ts["density"][0] * 2)            # the burst is visible
