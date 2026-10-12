"""Project website build (site/build.py) regression tests.

The Pages workflow publishes whatever the build produces, so the build's own checks
are the only gate: these tests prove each check actually fires (a tampered copy of
site/ must fail), not just that the real site passes.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = PROJECT_ROOT / "site"
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.scoring import cut_scores  # noqa: E402
from backend.bsor.models import SCORING_NORMAL  # noqa: E402


def _load_builder():
    # "site" is also a stdlib module name, so load the script by path
    spec = importlib.util.spec_from_file_location("saberlab_site_build", SITE_SRC / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()
charts = builder.site_charts


class ReadoutFollowsOfficialScoringTest(unittest.TestCase):
    """The hero readout prints one illustrative cut. Its points, meters and total must
    be what the official scoring rules give for its data-* geometry (the same geometry
    intro.js animates), so the page can never show an impossible cut."""

    def test_printed_points_match_the_scoring_rules(self):
        page = (SITE_SRC / "templates" / "index.html").read_text(encoding="utf-8")
        tag = re.search(r'<aside class="readout[^>]*>', page).group(0)
        pre = float(re.search(r'data-pre-deg="([\d.]+)"', tag).group(1))
        center_cm = float(re.search(r'data-center-cm="([\d.]+)"', tag).group(1))
        post = float(re.search(r'data-post-deg="([\d.]+)"', tag).group(1))
        # the game's ratings: pre-swing counts against 100°, follow-through against 60°
        cut = SimpleNamespace(before_cut_rating=min(pre / 100, 1.0), after_cut_rating=min(post / 60, 1.0),
                              cut_distance_to_center=center_cm / 100)
        note = SimpleNamespace(params=SimpleNamespace(scoring_type=SCORING_NORMAL), cut=cut)
        expected = dict(zip(("pre", "center", "post"), cut_scores(note), strict=True))
        maxima = {"pre": 70, "center": 15, "post": 30}
        measures = {"pre": f"{pre:g}°", "center": f"{center_cm:.1f} cm", "post": f"{post:g}°"}
        for row, points in expected.items():
            block = re.search(rf'data-row="{row}">(.*?)</div>', page, re.S).group(1)
            self.assertIn(f'<span class="rr-measure mono">{measures[row]}</span>', block)
            self.assertIn(f"<b>{points}</b>/{maxima[row]}", block)
            fill = float(re.search(r"--fill:([\d.]+)", block).group(1))
            self.assertTrue(math.isclose(fill, points / maxima[row], abs_tol=1e-3), row)
        total = sum(expected.values())
        self.assertIn(f'<b id="readout-score">{total}</b>', page)
        self.assertIn(f"<b>−{115 - total}</b>", page)


class RunChartTest(unittest.TestCase):
    def test_weakest_window_counts_misses_as_zero_and_is_deterministic(self):
        notes = [[i * 0.5, "l" if i % 2 else "r", 70, 14, 30, "g"] for i in range(60)]
        for i in (30, 31, 33):
            notes[i][5] = "m"
        first, last, mean, count = charts.weakest_window(notes)
        self.assertEqual(count, 20)                       # 10 s at 0.5 s spacing
        self.assertLessEqual(first, 15.0)
        self.assertGreaterEqual(last, 16.5)               # the window holds every miss
        self.assertAlmostEqual(mean, 114 * 17 / 20)
        self.assertEqual(charts.weakest_window(notes), (first, last, mean, count))

    def test_real_run_renders_chart_tiles_and_hover_data(self):
        run = json.loads((SITE_SRC / "data" / "run.json").read_text(encoding="utf-8"))
        ctx, raw = charts.render(run)
        self.assertTrue(ctx["run.svg"].startswith('<svg class="run-svg"'))
        self.assertEqual(ctx["run.tiles"].count('class="tile'), 12)
        self.assertEqual(ctx["run.tiles"].count("tile-weak"), 1)
        hover = json.loads(ctx["run.hover"])
        self.assertEqual(len(hover["n"]), len(run["notes"]))
        self.assertNotIn("</", ctx["run.hover"])          # safe inside <script>
        self.assertEqual(raw, {"run.svg", "run.labels", "run.hover", "run.tiles"})


class ScriptSyntaxTest(unittest.TestCase):
    def test_site_scripts_parse(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        for name in ("site.js", "intro.js"):
            result = subprocess.run([node, "--check", str(SITE_SRC / "assets" / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


# Runs the built page's inline head script against a stubbed browser and prints where it
# navigates (or "stay") and whether it armed the intro.
_HEAD_HARNESS = r"""
const vm = require("vm");
const [src, c] = [process.argv[1], JSON.parse(process.argv[2])];   // node -e: args start at 1
let went = "stay", cls = [];
const store = c.pref === null ? {} : { "saberlab.lang": c.pref };
const ctx = {
  document: { referrer: c.referrer, documentElement: { style: {}, classList: {
    add: (k) => cls.push(k), contains: (k) => cls.includes(k), remove() {} } } },
  location: { href: c.href, search: "", hash: c.hash, replace: (u) => { went = u; } },
  navigator: { languages: [c.lang], language: c.lang },
  localStorage: { getItem: (k) => (k in store ? store[k] : null) },
  sessionStorage: { getItem: () => null },
  matchMedia: () => ({ matches: false }),
  setTimeout: () => 0, URL,
};
vm.runInNewContext(src, ctx);
console.log(JSON.stringify({ went, intro: cls.includes("intro") }));
"""


class LanguagePickTest(unittest.TestCase):
    """The site root sends Chinese browsers to /zh/, unless the visitor came from this
    site or chose a language in the switcher; /zh/ itself never redirects."""
    ROOT = "https://zircon10086.github.io/SaberLab/"

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not installed")
        cls._tmp = tempfile.TemporaryDirectory()
        out, _ = builder.build(src=SITE_SRC, out=Path(cls._tmp.name) / "out")
        head = re.compile(r"<head>.*?<script>\n(.*?)</script>", re.S)
        cls.scripts = {name: head.search((out / name).read_text(encoding="utf-8")).group(1)
                       for name in ("index.html", "zh/index.html")}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_head(self, page="index.html", lang="zh-CN", pref=None, referrer="", hash=""):
        href = self.ROOT + ("zh/" if page != "index.html" else "") + hash
        case = {"lang": lang, "pref": pref, "referrer": referrer, "href": href, "hash": hash}
        result = subprocess.run([self.node, "-e", _HEAD_HARNESS, self.scripts[page], json.dumps(case)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_chinese_browsers_go_to_zh(self):
        for lang in ("zh-CN", "zh-TW", "zh-HK", "zh-Hans-CN", "zh"):
            with self.subTest(lang=lang):
                self.assertEqual(self.run_head(lang=lang), {"went": "./zh/", "intro": False})

    def test_other_browsers_stay_and_get_the_intro(self):
        for lang in ("en-US", "en-GB", "ja-JP", "zu", "zhx-yy", ""):   # zhx: a prefix, not zh
            with self.subTest(lang=lang):
                self.assertEqual(self.run_head(lang=lang), {"went": "stay", "intro": True})

    def test_the_switcher_choice_wins_over_the_browser(self):
        self.assertEqual(self.run_head(lang="zh-CN", pref="en-US")["went"], "stay")
        self.assertEqual(self.run_head(lang="en-US", pref="zh-CN")["went"], "./zh/")
        self.assertEqual(self.run_head(lang="zh-CN", pref="xx-XX")["went"], "stay")   # stale value

    def test_arriving_from_this_site_stays(self):
        for ref in (self.ROOT + "zh/", self.ROOT + "no-such-page", self.ROOT):
            with self.subTest(referrer=ref):
                self.assertEqual(self.run_head(lang="zh-CN", referrer=ref)["went"], "stay")
        self.assertEqual(self.run_head(lang="zh-CN", referrer="https://github.com/")["went"], "./zh/")

    def test_deep_link_keeps_its_anchor(self):
        self.assertEqual(self.run_head(lang="zh-TW", hash="#download")["went"], "./zh/#download")

    def test_zh_page_never_redirects(self):
        for pref in (None, "en-US"):
            with self.subTest(pref=pref):
                self.assertEqual(self.run_head(page="zh/index.html", lang="en-US", pref=pref),
                                 {"went": "stay", "intro": True})


class SiteBuildTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "site"
        shutil.copytree(SITE_SRC, self.src, ignore=shutil.ignore_patterns("dist", "__pycache__"))
        self.out = self.tmp / "out"
        self.addCleanup(self._tmp.cleanup)

    def _build(self):
        return builder.build(src=self.src, out=self.out)

    def _edit_json(self, rel: str, change) -> None:
        path = self.src / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ---------- the real site builds, in every language ----------

    def test_real_site_builds_every_language(self):
        out, links = self._build()
        self.assertGreater(links, 20)
        en = (out / "index.html").read_text(encoding="utf-8")
        zh = (out / "zh" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en-US">', en)
        self.assertIn('<html lang="zh-CN">', zh)
        for page in (en, zh):
            self.assertNotIn("{{", page)
            self.assertEqual(page.count('rel="alternate" hreflang='), 3)   # en, zh, x-default
        self.assertIn('href="../assets/site.css"', zh)                      # relative from zh/
        self.assertIn('src="../assets/intro.js"', zh)
        for page in (en, zh):
            self.assertEqual(page.count('<svg class="run-svg"'), 1)
            self.assertIn('<script type="application/json" id="run-data">', page)

    def test_404_uses_absolute_paths_and_carries_every_language(self):
        out, _ = self._build()
        page = (out / "404.html").read_text(encoding="utf-8")
        self.assertIn('href="/SaberLab/assets/site.css"', page)
        self.assertIn('href="/SaberLab/zh/"', page)
        self.assertIn('lang="en-US"', page)
        self.assertIn('lang="zh-CN"', page)

    def test_only_the_default_language_page_picks_a_language(self):
        out, _ = self._build()
        en = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('})(document, {"home": "./", "urls": {"zh-CN": "./zh/"}, "detect": {"zh": "zh-CN"}});', en)
        self.assertIn("})(document, null);", (out / "zh" / "index.html").read_text(encoding="utf-8"))

    def test_detect_list_on_the_default_language_fails(self):
        self._edit_json("site.json", lambda d: d["languages"]["en-US"].__setitem__("detect", ["en"]))
        with self.assertRaisesRegex(builder.BuildError, "default language"):
            self._build()

    def test_detect_prefix_must_be_a_lowercase_tag(self):
        self._edit_json("site.json", lambda d: d["languages"]["zh-CN"].__setitem__("detect", ["ZH"]))
        with self.assertRaisesRegex(builder.BuildError, "lowercase"):
            self._build()

    def test_build_is_deterministic(self):
        first, _ = builder.build(src=self.src, out=self.tmp / "a")
        second, _ = builder.build(src=self.src, out=self.tmp / "b")
        files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
        self.assertEqual(files, sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file()))
        for rel in files:
            self.assertEqual((first / rel).read_bytes(), (second / rel).read_bytes(), str(rel))

    # ---------- every gate must actually fire ----------

    def test_key_missing_from_one_language_fails(self):
        self._edit_json("i18n/zh-CN.json", lambda d: d.pop("hero.title"))
        with self.assertRaisesRegex(builder.BuildError, "missing .*hero.title"):
            self._build()

    def test_key_no_template_uses_fails(self):
        for lang in ("en-US", "zh-CN"):
            self._edit_json(f"i18n/{lang}.json", lambda d: d.__setitem__("orphan.key", "x"))
        with self.assertRaisesRegex(builder.BuildError, "orphan.key"):
            self._build()

    def test_unknown_placeholder_fails(self):
        tpl = self.src / "templates" / "index.html"
        tpl.write_text(tpl.read_text(encoding="utf-8").replace("</main>", "{{no.such.key}}</main>"),
                       encoding="utf-8")
        with self.assertRaisesRegex(builder.BuildError, "no.such.key"):
            self._build()

    def test_undeclared_language_file_fails(self):
        shutil.copy(self.src / "i18n" / "en-US.json", self.src / "i18n" / "ja-JP.json")
        with self.assertRaisesRegex(builder.BuildError, "ja-JP"):
            self._build()

    def test_broken_srcset_image_fails(self):
        (self.src / "assets" / "img" / "replay-1920.webp").unlink()
        with self.assertRaisesRegex(builder.BuildError, "replay-1920.webp"):
            self._build()

    def test_broken_in_page_anchor_fails(self):
        tpl = self.src / "templates" / "index.html"
        tpl.write_text(tpl.read_text(encoding="utf-8").replace('href="#features"', 'href="#nowhere"'),
                       encoding="utf-8")
        with self.assertRaisesRegex(builder.BuildError, "#nowhere"):
            self._build()

    def test_table_values_are_escaped_unless_marked_html(self):
        for lang in ("en-US", "zh-CN"):
            self._edit_json(f"i18n/{lang}.json", lambda d: d.__setitem__("hero.title", "<b>x</b>"))
        out, _ = self._build()
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", (out / "index.html").read_text(encoding="utf-8"))

    def test_incomplete_run_data_fails(self):
        self._edit_json("data/run.json", lambda d: d.pop("tiles"))
        with self.assertRaisesRegex(builder.BuildError, "tiles"):
            self._build()

    def test_run_data_may_not_shadow_an_i18n_key(self):
        for lang in ("en-US", "zh-CN"):
            self._edit_json(f"i18n/{lang}.json", lambda d: d.__setitem__("run.song", "x"))
        with self.assertRaisesRegex(builder.BuildError, "shadow"):
            self._build()

    def test_refuses_to_clear_a_directory_it_did_not_build(self):
        self.out.mkdir()
        keep = self.out / "important.txt"
        keep.write_text("user data", encoding="utf-8")
        with self.assertRaisesRegex(builder.BuildError, "refusing to clear"):
            self._build()
        self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()
