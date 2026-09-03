"""SliceDetails port tests (v1.6.0): tile/cell aggregation, circular angle
mean, scoring with special notes, signed offsets, exclusions, empty tiles."""
import unittest

from backend.bsor.models import NoteEvent, NoteCutInfo, GOOD
from backend.analysis.slicedetails import (
    analyze_slice_details, TILE_COUNT, CELLS_PER_TILE, _cut_angle, _circular_mean,
)

# noteID = scoringType*10000 + lineIndex*1000 + noteLineLayer*100 + colorType*10 + cutDirection
def note(scoring, line, layer, color, cut_dir, normal=(1.0, 0.0, 0.0),
         dist=0.1, cut_point=(0.0, 0.0, 0.0), before=1.0, after=1.0,
         event_type=GOOD):
    note_id = scoring * 10000 + line * 1000 + layer * 100 + color * 10 + cut_dir
    cut = NoteCutInfo(cut_normal=normal, cut_distance_to_center=dist,
                      cut_point=cut_point,
                      before_cut_rating=before, after_cut_rating=after)
    return NoteEvent(note_id=note_id, event_time=1.0, spawn_time=0.0,
                     event_type=event_type, raw_event_type=event_type, cut=cut)


def analyze(notes, height=1.8, left_handed=False):
    return analyze_slice_details(notes, height=height, left_handed=left_handed)


def cell(res, tile_idx, color, slot):
    return res["tiles"][tile_idx]["cells"][color * 9 + slot]


class CutAngleTest(unittest.TestCase):
    def test_angle_formula(self):
        # cutDirection = (-n.y, n.x); angle = atan2(dy, dx) + 180
        self.assertAlmostEqual(_cut_angle((1.0, 0.0, 0.0)), 270.0)   # normal +x
        self.assertAlmostEqual(_cut_angle((0.0, 1.0, 0.0)), 360.0)   # normal +y
        self.assertAlmostEqual(_cut_angle((0.0, -1.0, 0.0)), 180.0)  # normal -y
        self.assertAlmostEqual(_cut_angle((-1.0, 0.0, 0.0)), 90.0)   # normal -x

    def test_circular_mean(self):
        # atan2 returns -180..180 (same as C# Mathf.Atan2); 270 averages to -90
        self.assertAlmostEqual(_circular_mean([270.0]), -90.0)
        # 350 and 10 average to 0 (not 180)
        self.assertAlmostEqual(_circular_mean([350.0, 10.0]), 0.0, places=2)


class AggregationTest(unittest.TestCase):
    def test_tile_index_mapping(self):
        # layer*4 + line: (layer=2, line=3) -> tile 11, top-right of grid
        res = analyze([note(3, 3, 2, 0, 8)])
        tiles = res["tiles"]
        self.assertEqual(len(tiles), TILE_COUNT)
        self.assertEqual(tiles[11]["count"], 1)
        self.assertEqual(sum(t["count"] for t in tiles), 1)
        # (layer=0, line=0) -> tile 0, bottom-left
        res = analyze([note(3, 0, 0, 0, 8)])
        self.assertEqual(res["tiles"][0]["count"], 1)

    def test_direction_slot_mapping(self):
        # Up(0) -> slot 1; Any(8) -> slot 4 (center); Down(1) -> slot 7
        for cut_dir, slot in ((0, 1), (8, 4), (1, 7), (4, 0), (7, 8)):
            res = analyze([note(3, 1, 1, 0, cut_dir)])
            cells = res["tiles"][5]["cells"]
            self.assertEqual(cells[slot]["count"], 1, f"dir {cut_dir} -> slot {slot}")
            self.assertEqual(sum(c["count"] for c in cells), 1)

    def test_color_mapping(self):
        # color 0 -> cells 0..8 (left hand), color 1 -> cells 9..17 (right hand)
        res = analyze([note(3, 1, 1, 0, 8), note(3, 1, 1, 1, 8)])
        cells = res["tiles"][5]["cells"]
        self.assertEqual(cells[0 + 4]["count"], 1)
        self.assertEqual(cells[9 + 4]["count"], 1)
        self.assertEqual(sum(c["count"] for c in cells), 2)

    def test_scores(self):
        # before=1.0 -> 70, after=1.0 -> 30, dist=0.1 -> 15*(1-0.1/0.3)=10
        res = analyze([note(3, 1, 1, 0, 8, dist=0.1)])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["pre"], 70.0)
        self.assertEqual(c["post"], 30.0)
        self.assertEqual(c["acc"], 10.0)
        self.assertEqual(c["total"], 110.0)
        self.assertEqual(res["tiles"][5]["score_avg"], 110.0)

    def test_score_average_over_multiple(self):
        # two notes: (70,30,10)=110 and (70,30,15)=115 -> avg total 112.5
        res = analyze([
            note(3, 1, 1, 0, 8, dist=0.1),
            note(3, 1, 1, 0, 8, dist=0.0),
        ])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["count"], 2)
        self.assertEqual(c["total"], 112.5)
        self.assertEqual(c["pre"], 70.0)
        self.assertEqual(c["acc"], 12.5)
        self.assertEqual(res["tiles"][5]["score_avg"], 112.5)

    def test_offset_sign_rule(self):
        # line=1, layer=1 -> center (-0.3, 1.4); normal +x
        # cut point right of center -> dot > 0 -> negative offset (back-side cut)
        res = analyze([note(3, 1, 1, 0, 8, dist=0.1, cut_point=(-0.2, 1.4, 0.0))])
        self.assertEqual(cell(res, 5, 0, 4)["offset"], -0.1)
        # cut point left of center -> dot < 0 -> positive offset
        res = analyze([note(3, 1, 1, 0, 8, dist=0.1, cut_point=(-0.4, 1.4, 0.0))])
        self.assertEqual(cell(res, 5, 0, 4)["offset"], 0.1)

    def test_offset_mean_mixed_sides(self):
        res = analyze([
            note(3, 1, 1, 0, 8, dist=0.2, cut_point=(-0.2, 1.4, 0.0)),   # -0.2
            note(3, 1, 1, 0, 8, dist=0.4, cut_point=(-0.4, 1.4, 0.0)),   # +0.4
        ])
        self.assertEqual(cell(res, 5, 0, 4)["offset"], 0.1)

    def test_offset_left_handed_mirror(self):
        # left-handed mirrors x: line 1 -> 3-1=2 -> center (0.3, 1.4)
        # cut point (0.4, 1.4) with normal +x -> dot > 0 -> negative
        res = analyze([note(3, 1, 1, 0, 8, dist=0.1, cut_point=(0.4, 1.4, 0.0))],
                      left_handed=True)
        self.assertEqual(cell(res, 5, 0, 4)["offset"], -0.1)

    def test_offset_height_offset(self):
        # height 2.0 -> hoff 0.1 -> center y 1.5; normal +y, cut below center
        # -> dot < 0 -> positive offset
        res = analyze([note(3, 1, 1, 0, 8, dist=0.1, normal=(0.0, 1.0, 0.0),
                            cut_point=(-0.3, 1.4, 0.0))], height=2.0)
        self.assertEqual(cell(res, 5, 0, 4)["offset"], 0.1)

    def test_angle_circular_mean_in_cell(self):
        # 350 and 10 average to ~0 (270/90 are opposite directions; their
        # circular mean is undefined due to floating-point noise)
        res = analyze([
            note(3, 1, 1, 0, 8, normal=(0.1736, 0.9848, 0.0)),    # angle 350
            note(3, 1, 1, 0, 8, normal=(-0.1736, 0.9848, 0.0)),   # angle 10
        ])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["count"], 2)
        self.assertAlmostEqual(c["angle"], 0.0, places=1)


class SpecialNoteTest(unittest.TestCase):
    def test_slider_head_pre_only(self):
        # SliderHead (4): pre counts, post must NOT be averaged in
        res = analyze([note(4, 1, 1, 0, 8)])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["count"], 1)
        self.assertEqual(c["pre"], 70.0)
        self.assertEqual(c["post"], 0.0)     # post_n == 0 -> excluded
        self.assertEqual(c["total"], 70.0 + c["acc"])

    def test_slider_tail_post_only(self):
        res = analyze([note(5, 1, 1, 0, 8)])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["post"], 30.0)
        self.assertEqual(c["pre"], 0.0)

    def test_slider_head_does_not_dilute_post_mean(self):
        # One normal (post 30) + one slider head (post excluded):
        # post mean must stay 30, not 15.
        res = analyze([
            note(3, 1, 1, 0, 8, dist=0.1),
            note(4, 1, 1, 0, 8, dist=0.1),
        ])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["count"], 2)
        self.assertEqual(c["post"], 30.0)
        self.assertEqual(c["pre"], 70.0)

    def test_burst_slider_head_pre_only(self):
        res = analyze([note(6, 1, 1, 0, 8)])
        c = cell(res, 5, 0, 4)
        self.assertEqual(c["post"], 0.0)
        self.assertEqual(c["pre"], 70.0)


class ExclusionTest(unittest.TestCase):
    def test_burst_element_excluded(self):
        res = analyze([note(7, 1, 1, 0, 8)])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)

    def test_me_notes_out_of_grid_excluded(self):
        # line 4 / layer 3 exceed the 4x3 grid (mapper notes)
        res = analyze([note(3, 4, 1, 0, 8), note(3, 1, 3, 0, 8)])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)

    def test_non_good_events_excluded(self):
        from backend.bsor.models import BAD, MISS, BOMB
        res = analyze([
            note(3, 1, 1, 0, 8, event_type=BAD),
            note(3, 1, 1, 0, 8, event_type=MISS),
            note(3, 1, 1, 0, 8, event_type=BOMB),
        ])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)

    def test_color_type_2_excluded(self):
        res = analyze([note(3, 1, 1, 2, 8)])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)

    def test_cut_missing_excluded(self):
        ev = note(3, 1, 1, 0, 8)
        ev.cut = None
        res = analyze([ev])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)

    def test_empty_replay(self):
        res = analyze([])
        self.assertEqual(len(res["tiles"]), TILE_COUNT)
        for t in res["tiles"]:
            self.assertEqual(t["count"], 0)
            self.assertEqual(t["score_avg"], 0.0)
            self.assertEqual(len(t["cells"]), CELLS_PER_TILE)
            self.assertEqual(t["cells"][0]["count"], 0)

    def test_direction_none_excluded(self):
        # cutDirection 9 (None) has no compass slot
        res = analyze([note(3, 1, 1, 0, 9)])
        self.assertEqual(sum(t["count"] for t in res["tiles"]), 0)


if __name__ == "__main__":
    unittest.main()
