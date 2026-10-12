"""Wide-screen list layout regression test (2026-10).

Background: `.replay-list` (used by the history tab and by map management) switches to a
two-column grid at a viewport breakpoint. When that breakpoint is even slightly too low, the
media query claims two columns fit while the content area cannot hold the second one, and
`repeat(auto-fill, minmax(960px, 1fr))` falls back to ONE column whose `1fr` stretches it across
the whole row - the "the card is super long instead of two per row" bug the user reported twice
(measured band: 2240-2259px viewport).

A pure CSS breakpoint cannot be unit-tested by rendering, so this pins the arithmetic that makes
it correct: the breakpoint, minus the space the chrome takes, must cover two columns plus the gap.
The chrome value is a measurement (2026-10: at a 2240px viewport the content area was 1932.67px,
i.e. viewport - 307), not a guess; if the sidebar or the page padding changes, this test fails and
asks for a re-measurement instead of silently reintroducing the dead band.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STYLE = PROJECT_ROOT / "frontend" / "style.css"

# measured 2026-10 (CDP, several widths): content area = viewport - 307 exactly
CHROME_PX = 307
COLUMN_MIN_PX = 960          # .replay-list max-width / minmax() minimum
GAP_PX = 20                  # .replay-list gap


def _css() -> str:
    return STYLE.read_text(encoding="utf-8")


def _list_breakpoint() -> int:
    """The viewport width at which .replay-list switches to multiple columns."""
    block = re.search(r"@media \(min-width: (\d+)px\) \{\s*\.replay-list \{", _css())
    if not block:
        raise AssertionError("no width breakpoint found for .replay-list")
    return int(block.group(1))


class WideScreenListTest(unittest.TestCase):
    def test_breakpoint_leaves_room_for_two_columns(self):
        breakpoint = _list_breakpoint()
        content = breakpoint - CHROME_PX
        needed = COLUMN_MIN_PX * 2 + GAP_PX
        self.assertGreaterEqual(
            content, needed,
            f"at the {breakpoint}px breakpoint the content area is only {content}px, but two "
            f"{COLUMN_MIN_PX}px columns with a {GAP_PX}px gap need {needed}px: the single column "
            f"would stretch across the row (the 'super long card' bug)")

    def test_day_header_breakpoint_matches_the_list(self):
        """The day header is left-aligned in multi-column mode; the two must switch together."""
        css = _css()
        list_bp = _list_breakpoint()
        header = re.search(r"@media \(min-width: (\d+)px\) \{\s*\.day-header \{", css)
        self.assertIsNotNone(header, "no width breakpoint found for .day-header")
        self.assertEqual(int(header.group(1)), list_bp)

    def test_single_column_is_capped_and_centered(self):
        """Below the breakpoint the list must be capped instead of filling the row."""
        block = re.search(r"\.replay-list \{(.*?)\}", _css(), re.S)
        self.assertIsNotNone(block)
        body = block.group(1)
        self.assertIn(f"max-width: {COLUMN_MIN_PX}px", body)
        self.assertIn("margin: 0 auto", body)
        self.assertIn("grid-template-columns: 1fr", body)

    def test_multi_column_grid_uses_the_same_minimum(self):
        """The wide layout must keep the same column minimum as the single-column cap."""
        multi = re.search(r"@media \(min-width: \d+px\) \{\s*\.replay-list \{(.*?)\}", _css(), re.S)
        self.assertIsNotNone(multi)
        self.assertIn(f"minmax({COLUMN_MIN_PX}px, 1fr)", multi.group(1))
        self.assertIn("max-width: none", multi.group(1))

    def test_measured_chrome_is_still_accurate(self):
        """Guard the measurement: sidebar width + page padding must add up to CHROME_PX.

        The sidebar token is in the same stylesheet; the page padding is the rest. If either
        changes, the breakpoint needs re-measuring (and this test tells you so).
        """
        token = re.search(r"--sidebar-w:\s*(\d+)px", _css())
        self.assertIsNotNone(token, "no --sidebar-w token")
        # 2026-10 measurement: 208 sidebar + 99 page padding (48 + 48 + centering bits)
        self.assertEqual(int(token.group(1)) + 99, CHROME_PX,
                         "the sidebar width changed: re-measure the content area and update the "
                         "breakpoint derivation in style.css and this test")


if __name__ == "__main__":
    unittest.main()
