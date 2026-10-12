"""i18n mapping consistency regression test (2026-09).

Background: the language tables contain two kinds of mapping that can fail silently —
neither one reports an error, which makes them hard to notice:

1. The keys of the `err` section are the **Chinese original of a backend message**,
   looked up and translated by `tErr(msg)`. As soon as a key and the backend text drift
   apart (editing the wording on only one side), `tErr()` falls back to "the original
   text": the UI reports no error, the translation just stops working silently.
2. Frontend code uses dotted keys (`t("err.offline")`, `data-i18n="..."`); when a key is
   missing from the table `t()` returns the key name itself, and the UI displays the key
   name directly.

This test pins both conventions down. Note Python's **implicit concatenation of adjacent
string literals** — the historical bug was exactly "the backend wrote a long message as
two concatenated lines while the i18n key was the single-line whole string", which is why
the literals are read through the AST here.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
I18N_DIR = PROJECT_ROOT / "frontend" / "i18n"
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_JS = PROJECT_ROOT / "frontend" / "app.js"
FRONTEND_HTML = PROJECT_ROOT / "frontend" / "index.html"
LANGS = ("zh-CN", "en-US", "ja-JP")
# nested message sections in the language tables (their keys are Chinese/English originals, not dotted keys)
MESSAGE_SECTIONS = ("err", "msg", "task.current")


def backend_chinese_literals() -> set[str]:
    """Every Chinese-containing string literal in backend sources.

    Parsed with `ast` so adjacent-literal concatenation becomes one string.
    f-string placeholders are normalized to a NUL marker so a `{param}` key in the
    i18n table can be template-matched against it.
    """
    out: set[str] = set()
    for path in BACKEND_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover - broken file
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(r"[\u4e00-\u9fff]", node.value):
                    out.add(node.value)
            elif isinstance(node, ast.JoinedStr):
                joined = "".join(
                    v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    else "\x00"
                    for v in node.values
                )
                if re.search(r"[\u4e00-\u9fff]", joined):
                    out.add(joined)
    return out


def template_hits(key: str, literals: set[str]) -> bool:
    """True when `key` (with `{param}` as a wildcard) matches a backend literal."""
    parts = [p for p in re.split(r"\{[^}]*\}", key) if p]
    if not parts:
        return False
    for cand in literals:
        pos = 0
        for part in parts:
            idx = cand.find(part, pos)
            if idx < 0:
                break
            pos = idx + len(part)
        else:
            return True
    return False


def referenced_dotted_keys() -> set[str]:
    """Dotted i18n keys referenced by app.js / index.html.

    Prefix-concatenation fragments (e.g. `t("task.kind." + kind)`) are excluded:
    they end with `_` or `.` and are not real table keys.
    """
    js = FRONTEND_JS.read_text(encoding="utf-8")
    html = FRONTEND_HTML.read_text(encoding="utf-8")
    used = set(re.findall(r"""\bt\(\s*["']([a-z][\w.]+)["']""", js))
    used |= set(re.findall(r'data-i18n(?:-placeholder|-title)?="([a-z][\w.]+)"', html))
    return {k for k in used if not k.endswith(("_", "."))}


def load(lang: str) -> dict:
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


class I18nMappingConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.literals = backend_chinese_literals()

    def setUp(self):
        self.tables = {lang: load(lang) for lang in LANGS}

    def test_backend_literals_found(self):
        """Sanity: the scanner actually sees backend messages (guards the test itself)."""
        self.assertGreater(len(self.literals), 50)

    def test_err_keys_match_backend_messages(self):
        """Every `err` key must correspond to a real backend message."""
        for lang in ("en-US", "ja-JP"):
            err = self.tables[lang].get("err") or {}
            self.assertTrue(err, f"{lang}: err section is empty")
            for key in err:
                with self.subTest(lang=lang, key=key[:40]):
                    self.assertTrue(
                        template_hits(key, self.literals),
                        f"{lang}: err 键在后端找不到对应原文（译文会静默失效）: {key}",
                    )

    def test_referenced_keys_exist_in_all_languages(self):
        """No dotted key used by the frontend may be missing from any language."""
        used = referenced_dotted_keys()
        self.assertGreater(len(used), 100)
        for lang in LANGS:
            table = self.tables[lang]
            for key in sorted(used):
                if (key in table
                        or key in (table.get("err") or {})
                        or key in (table.get("msg") or {})
                        or key in (table.get("task.current") or {})):
                    continue
                with self.subTest(lang=lang, key=key):
                    self.fail(f"{lang}: 前端引用了缺失的键（界面会显示键名）: {key}")

    def test_language_tables_share_the_same_key_set(self):
        """en/ja must cover every key of the zh baseline table."""
        baseline = set(self.tables["zh-CN"])
        for lang in ("en-US", "ja-JP"):
            missing = sorted(baseline - set(self.tables[lang]))
            with self.subTest(lang=lang):
                self.assertEqual(missing, [], f"{lang} 缺少基准键: {missing[:5]}")

    def test_language_tables_share_the_same_message_sections(self):
        """The err/msg/task.current sections must not drift apart between languages."""
        for section in MESSAGE_SECTIONS:
            sizes = {lang: len(self.tables[lang].get(section) or {}) for lang in ("en-US", "ja-JP")}
            with self.subTest(section=section):
                self.assertEqual(len(set(sizes.values())), 1,
                                 f"{section} 段语言间条数不一致: {sizes}")


if __name__ == "__main__":
    unittest.main()
