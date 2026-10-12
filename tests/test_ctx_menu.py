"""Context-menu regressions: the replay entry's menu composition and its copy helpers.

Two layers, both DOM-free:

* the pure helpers (`songAuthorText`, `beatSaverMapUrl`, `ctxData`, `copyText`) run in a Node
  sandbox with the production bodies extracted from app.js — this is where the clipboard *text*
  and the fallback chain are decided;
* the registered `.replay-item` entry is evaluated through small stubs, so the items, their
  order and their disabled states are asserted on the real `items()` code rather than on its
  source text.

Real rendering, window focus behaviour and the clipboard round trip stay in the standalone
WebView2 harness (`run.bat`), which is the only place the app's own window can be verified.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "frontend" / "app.js"
I18N_DIR = ROOT / "frontend" / "i18n"
NODE = shutil.which("node")
LANGS = ("zh-CN", "en-US", "ja-JP")

# A registration is a `.push({...})` literal ending at the first line that is exactly `});`
# (the brace-column convention the file uses for its registrations). Slicing them apart lets a
# test run ONE scenario, since the entries reference functions belonging to their own page.
REGISTRATION_RE = re.compile(r"CTX_MENUS\.push\(\{.*?\n\}\);", re.DOTALL)
CTX_DATA_RE = re.compile(r"const REPLAY_CTX_DATA = \{.*?\n\};", re.DOTALL)


def registrations() -> list[str]:
    found = REGISTRATION_RE.findall(JS_PATH.read_text(encoding="utf-8"))
    if not found:
        raise AssertionError("no CTX_MENUS registration found in app.js")
    out = []
    for part in found:
        for prefix in ("CTX_MENUS.push({", "CTX_MENUS.push( {"):
            if part.startswith(prefix):
                part = "{ " + part[len(prefix):]
                break
        out.append(part)
    return out


def registration_for(selector: str) -> str:
    for part in registrations():
        if f'match: "{selector}"' in part:
            return part
    raise AssertionError(f"no registration matches {selector}")


def function_source(name: str) -> str:
    """One production function, extracted without replacing its body."""
    lines = JS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith((f"function {name}(", f"async function {name}(")):
            end = index + 1
            while lines[end].rstrip("\r\n") != "}":
                end += 1
            return "".join(lines[index:end + 1])
    raise AssertionError(f"production function not found: {name}")


PRELUDE = """
const labels = [];
const toasts = [];
const actions = [];
const focus = { calls: 0 };
function ctxMenuItem(item) { return item; }
function ctxSeparator() { return { sep: true }; }
function t(key) { return key; }
const openDetail = id => actions.push("detail:" + id);
const showSameMapHistory = song => actions.push("history:" + song);
const openMapDetail = hash => actions.push("map:" + hash);
const revealMapFolder = hash => actions.push("revealmap:" + hash);
const confirmRecycleMap = (hash, song) => actions.push("recyclemap:" + hash);
const revealReplayFile = id => actions.push("reveal:" + id);
const confirmRecycleReplay = (id, song) => actions.push("recycle:" + id + ":" + song);
function toast(message, kind = "error") { toasts.push(kind + ":" + message); }
const navigator = { clipboard: null };
const window = { focus() { focus.calls++; } };
const harness = {
  clipboard: null,      // test-set: { async writeText(v) {...} } or null
  legacy: true,         // test-set: what execCommand returns
  async copy(value, okKey) { return await copyText(value, okKey); },
};
const document = {
  createElement() {
    return { value: "", style: { cssText: "" }, setAttribute() {}, select() {},
             remove() {} };
  },
  body: { appendChild() {} },
  execCommand() { return harness.legacy; },
};
"""


def run_node(body: str, selector: str = ".replay-item", extra: str = "") -> str:
    """Run `body` against the real .replay-item registration plus the copy helpers."""
    script = PRELUDE
    script += "\n".join(function_source(name) for name in (
        "songAuthorText", "beatSaverMapUrl", "ctxData", "copyText"))
    ctx_data = CTX_DATA_RE.search(JS_PATH.read_text(encoding="utf-8"))
    if ctx_data is None:
        raise AssertionError("REPLAY_CTX_DATA not found in app.js")
    script += "\n" + ctx_data.group(0) + "\n"
    script += "const CTX_MENUS = [];\nCTX_MENUS.push("
    script += registration_for(selector)
    script += "\n" + extra
    script += ("\n(async () => {\n" + body + "\n})()"
               ".catch(e => { console.error(e); process.exitCode = 1; });\n")
    result = subprocess.run([NODE, "-"], input=script, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                            check=False)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def menu_of(dataset: dict) -> str:
    """Body that prints the menu for one card dataset: separators as ---, disabled as !."""
    return (f"const el = {{ dataset: {json.dumps(dataset)} }};\n"
            "const items = CTX_MENUS[0].items(el).filter(Boolean);\n"
            "console.log(JSON.stringify(items.map(i => i.sep ? '---'"
            "  : i.label + (i.disabled ? '!' : ''))));")


FULL_CARD = {"id": "R1", "song": "Cyaegha", "songAuthor": "Kobaryo",
             "key": "2f1a9", "mapHash": "abc", "inLibrary": "1", "fileAvailable": "1"}


@unittest.skipUnless(NODE, "Node is required for frontend state regression tests")
class ReplayCtxMenuTest(unittest.TestCase):
    def test_replay_menu_order_matches_the_agreed_layout(self):
        labels = json.loads(run_node(menu_of(FULL_CARD)))
        self.assertEqual(labels, [
            "ctx.open_detail",
            "ctx.same_map_history",
            "ctx.view_map",
            "---",
            "ctx.copy_song",
            "ctx.copy_beatsaver",
            "---",
            "ctx.reveal_file",
            "ctx.recycle_file",
        ])

    def test_view_map_is_absent_for_a_map_that_is_not_in_the_library(self):
        card = dict(FULL_CARD, inLibrary="0")
        labels = json.loads(run_node(menu_of(card)))
        self.assertNotIn("ctx.view_map", labels)
        self.assertEqual(labels[2], "---")            # the separator closes up behind it

    def test_missing_download_key_greys_out_only_that_item(self):
        card = {"id": "R1", "song": "WIP", "mapHash": "abc", "inLibrary": "1",
                "fileAvailable": "1"}
        labels = json.loads(run_node(menu_of(card)))
        self.assertIn("ctx.copy_beatsaver!", labels)
        self.assertIn("ctx.copy_song", labels)        # the name is still copyable

    def test_missing_file_greys_out_the_two_file_items(self):
        card = {"id": "R1", "song": "WIP", "fileAvailable": "0"}
        labels = json.loads(run_node(menu_of(card)))
        self.assertIn("ctx.reveal_file!", labels)
        self.assertIn("ctx.recycle_file!", labels)

    def test_card_without_a_replay_id_has_no_menu(self):
        out = run_node("const el = { dataset: {} };\n"
                       "console.log(JSON.stringify(CTX_MENUS[0].items(el)));")
        self.assertEqual(json.loads(out), [])

    def test_copy_items_write_the_agreed_text(self):
        """Title+artist for the name, the plain BeatSaver URL (never a hash) for the link."""
        script = f"""
        const el = {{ dataset: {json.dumps(FULL_CARD)} }};
        harness.clipboard = {{ written: [], async writeText(v) {{ this.written.push(v); }} }};
        navigator.clipboard = harness.clipboard;
        for (const item of CTX_MENUS[0].items(el)) {{
          if (item.sep) continue;
          if (item.label === "ctx.copy_song" || item.label === "ctx.copy_beatsaver") {{
            await item.action();
          }}
        }}
        console.log(JSON.stringify({{ written: harness.clipboard.written, toasts }}));
        """
        data = json.loads(run_node(script))
        self.assertEqual(data["written"],
                         ["Cyaegha - Kobaryo", "https://beatsaver.com/maps/2f1a9"])
        self.assertEqual(data["toasts"],
                         ["success:ctx.copied", "success:ctx.copied_link"])

    def test_other_items_still_work_and_target_the_right_replay(self):
        script = f"""
        const el = {{ dataset: {json.dumps(FULL_CARD)} }};
        for (const item of CTX_MENUS[0].items(el)) {{
          if (item.sep) continue;
          if (item.label.startsWith("ctx.copy")) continue;
          await item.action();
        }}
        console.log(JSON.stringify(actions));
        """
        self.assertEqual(json.loads(run_node(script)), [
            "detail:R1", "history:Cyaegha", "map:abc", "reveal:R1", "recycle:R1:Cyaegha",
        ])

    def test_copy_helpers_edge_cases(self):
        out = run_node("""
        console.log(JSON.stringify({
          no_author: songAuthorText("Cyaegha", ""),
          blank_author: songAuthorText("Cyaegha", "   "),
          missing_author: songAuthorText("Cyaegha", null),
          no_song: songAuthorText("", "Kobaryo"),
          trimmed: songAuthorText(" Cyaegha ", " Kobaryo "),
          trimmed_key: beatSaverMapUrl(" 2f1a9 "),
          empty_key: beatSaverMapUrl(""),
          null_key: beatSaverMapUrl(null),
          undefined_key: beatSaverMapUrl(undefined),
        }));
        """)
        data = json.loads(out)
        self.assertEqual(data["no_author"], "Cyaegha")
        self.assertEqual(data["blank_author"], "Cyaegha")
        self.assertEqual(data["missing_author"], "Cyaegha")
        self.assertEqual(data["no_song"], "Kobaryo")
        self.assertEqual(data["trimmed"], "Cyaegha - Kobaryo")
        self.assertEqual(data["trimmed_key"], "https://beatsaver.com/maps/2f1a9")
        self.assertEqual(data["empty_key"], "")
        self.assertEqual(data["null_key"], "")
        self.assertEqual(data["undefined_key"], "")

    def test_context_attributes_keep_unknown_distinct_from_empty(self):
        out = run_node("""
        const el = { dataset: { id: "R1", song: "", key: "", inLibrary: "0" } };
        console.log(JSON.stringify(ctxData(el, REPLAY_CTX_DATA)));
        """)
        data = json.loads(out)
        self.assertEqual(data["song"], "")
        self.assertIsNone(data["songAuthor"])
        self.assertIsNone(data["fileAvailable"])
        self.assertEqual(data["inLibrary"], "0")

    def test_copy_falls_back_when_the_api_is_refused(self):
        out = run_node("""
        const result = {};
        harness.clipboard = { async writeText() { throw new Error("Document is not focused"); } };
        navigator.clipboard = harness.clipboard;
        result.refused = { ok: await harness.copy("A - B", "ctx.copied"), toasts: [...toasts] };
        toasts.length = 0;
        navigator.clipboard = null;
        harness.clipboard = null;
        harness.legacy = false;
        result.dead_end = { ok: await harness.copy("A - B", "ctx.copied"), toasts: [...toasts] };
        toasts.length = 0;
        result.empty = { ok: await harness.copy("", "ctx.copied"), toasts: [...toasts] };
        console.log(JSON.stringify(result));
        """)
        data = json.loads(out)
        # a refused Clipboard API falls through to the textarea + execCommand path
        self.assertTrue(data["refused"]["ok"])
        self.assertEqual(data["refused"]["toasts"], ["success:ctx.copied"])
        # nothing left to try: an explicit failure notice, never a silent no-op
        self.assertFalse(data["dead_end"]["ok"])
        self.assertEqual(data["dead_end"]["toasts"], ["error:ctx.copy_failed"])
        # an empty value is reported instead of claiming success
        self.assertFalse(data["empty"]["ok"])
        self.assertEqual(data["empty"]["toasts"], ["error:ctx.copy_empty"])


class MapCtxMenuTest(unittest.TestCase):
    """The map-management card reuse: same order, no "view map", plus the two folder actions."""

    CARD = {"song": "Cyaegha", "songAuthor": "Kobaryo", "key": "2f1a9",
            "mapHash": "abc", "inLibrary": "1", "folderAvailable": "1"}

    def test_map_menu_layout(self):
        labels = json.loads(run_node(menu_of(self.CARD), selector=".map-item"))
        self.assertEqual(labels, [
            "ctx.open_detail",
            "ctx.same_map_history",
            "---",
            "ctx.copy_song",
            "ctx.copy_beatsaver",
            "---",
            "ctx.reveal_map_folder",
            "ctx.recycle_map",
        ])
        self.assertNotIn("ctx.view_map", labels)
        self.assertNotIn("ctx.reveal_file", labels)
        self.assertNotIn("ctx.recycle_file", labels)

    def test_map_menu_actions_target_the_card(self):
        script = f"""
        const el = {{ dataset: {json.dumps(self.CARD)} }};
        harness.clipboard = {{ written: [], async writeText(v) {{ this.written.push(v); }} }};
        navigator.clipboard = harness.clipboard;
        for (const item of CTX_MENUS[0].items(el)) {{
          if (item.sep) continue;
          await item.action();
        }}
        console.log(JSON.stringify({{ written: harness.clipboard.written, actions }}));
        """
        data = json.loads(run_node(script, selector=".map-item"))
        self.assertEqual(data["actions"],
                         ["map:abc", "history:Cyaegha", "revealmap:abc", "recyclemap:abc"])
        self.assertEqual(data["written"],
                         ["Cyaegha - Kobaryo", "https://beatsaver.com/maps/2f1a9"])

    def test_delete_map_is_marked_dangerous(self):
        out = run_node("""
        const el = { dataset: %s };
        const item = CTX_MENUS[0].items(el).find(i => i && i.label === "ctx.recycle_map");
        console.log(JSON.stringify({ danger: !!item.danger, disabled: !!item.disabled }));
        """ % json.dumps(self.CARD), selector=".map-item")
        self.assertEqual(json.loads(out), {"danger": True, "disabled": False})

    def test_map_menu_greys_out_a_missing_download_link(self):
        labels = json.loads(run_node(menu_of({"song": "WIP", "mapHash": "abc",
                                              "inLibrary": "1", "folderAvailable": "1"}),
                                     selector=".map-item"))
        self.assertIn("ctx.copy_beatsaver!", labels)
        self.assertIn("ctx.copy_song", labels)

    def test_map_menu_greys_out_the_folder_actions_when_the_folder_is_gone(self):
        card = dict(self.CARD, folderAvailable="0")
        labels = json.loads(run_node(menu_of(card), selector=".map-item"))
        self.assertIn("ctx.reveal_map_folder!", labels)
        self.assertIn("ctx.recycle_map!", labels)

    def test_the_map_registration_comes_first(self):
        """A .map-item is also a .replay-item: closest() takes the first registered match."""
        selectors = [re.search(r'match: "([^"]+)"', part).group(1) for part in registrations()]
        self.assertEqual(selectors[0], ".map-item", selectors)
        self.assertIn(".replay-item", selectors)


class CtxMenuI18nTest(unittest.TestCase):
    """Every menu label and copy notice must exist in all three language tables."""

    KEYS = ("ctx.open_detail", "ctx.same_map_history", "ctx.view_map", "ctx.copy_song",
            "ctx.copy_beatsaver", "ctx.copied", "ctx.copied_link", "ctx.copy_empty",
            "ctx.copy_failed")

    def test_keys_present_in_every_language(self):
        for lang in LANGS:
            table = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
            for key in self.KEYS:
                with self.subTest(lang=lang, key=key):
                    self.assertIn(key, table)

    def test_chinese_labels_are_the_agreed_wording(self):
        table = json.loads((I18N_DIR / "zh-CN.json").read_text(encoding="utf-8"))
        self.assertEqual(table["ctx.open_detail"], "查看详情")
        self.assertEqual(table["ctx.same_map_history"], "查看游玩记录")
        self.assertEqual(table["ctx.view_map"], "查看谱面")
        self.assertEqual(table["ctx.copy_song"], "复制歌曲名称")
        self.assertEqual(table["ctx.copy_beatsaver"], "复制下载链接")
        self.assertEqual(table["ctx.copied"], "已复制")
        self.assertEqual(table["ctx.copied_link"], "已经复制BeatSaver下载链接")


if __name__ == "__main__":
    unittest.main()
