"""Static build for the SaberLab project website (GitHub Pages).

Standard library only, so the Pages workflow needs nothing but the runner's python3.

    python site/build.py            # build into site/dist
    python site/build.py --serve    # build, then preview at http://127.0.0.1:8000/SaberLab/

Layout:
    site/site.json          base URL, languages, pages
    site/templates/*.html   page templates with {{key}} placeholders
                            (a leading underscore marks a partial, not a page)
    site/i18n/<lang>.json   flat string tables, one per language
    site/assets/            copied verbatim to dist/assets/

A placeholder resolves against the page context (root, lang, canonical, ...) first,
then the language table. Table values are HTML-escaped unless the key ends in
`_html`; only `_html` values may themselves contain context placeholders.

The build is strict on purpose: a key missing from one language, a key no template
uses, an unknown placeholder or a broken relative link stops the build, so the Pages
workflow never publishes a half-translated or broken page.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE))
import site_charts  # noqa: E402  (next to this file; "site" itself is a stdlib name)

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
NOT_FOUND_BLOCK = "_not_found_block.html"


class BuildError(Exception):
    """Any problem that must stop a publish."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BuildError(f"missing file: {path}") from None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise BuildError(f"{path.name}: invalid JSON ({exc})") from None


def load_site(src: Path) -> dict:
    site = _read_json(src / "site.json")
    if not site["base_url"].endswith("/"):
        raise BuildError("site.json: base_url must end with '/'")
    langs = site["languages"]
    default = site["default_lang"]
    if default not in langs:
        raise BuildError(f"site.json: default_lang {default!r} is not in languages")
    if langs[default]["path"] != "":
        raise BuildError("site.json: the default language must live at the site root (path \"\")")
    for code, meta in langs.items():
        if meta["path"] and not meta["path"].endswith("/"):
            raise BuildError(f"site.json: path of {code} must end with '/'")
    return site


def load_tables(src: Path, site: dict) -> dict[str, dict[str, str]]:
    """One flat string table per language; every table must carry the same keys."""
    declared = set(site["languages"])
    present = {p.stem for p in (src / "i18n").glob("*.json")}
    if present - declared:
        raise BuildError(f"i18n files not declared in site.json: {sorted(present - declared)}")
    tables = {code: _read_json(src / "i18n" / f"{code}.json") for code in site["languages"]}
    reference = set(tables[site["default_lang"]])
    for code, table in tables.items():
        missing, extra = reference - set(table), set(table) - reference
        if missing or extra:
            raise BuildError(f"i18n/{code}.json: missing {sorted(missing)} extra {sorted(extra)}")
        empty = [k for k, v in table.items() if not isinstance(v, str) or not v.strip()]
        if empty:
            raise BuildError(f"i18n/{code}.json: empty or non-string values {empty}")
    return tables


def _page_url(site: dict, code: str, page: str) -> str:
    """Site-relative URL of a page in one language ("" / "zh/" / "zh/about.html")."""
    return site["languages"][code]["path"] + ("" if page == "index.html" else page)


def page_context(site: dict, code: str, page: str, absolute: bool) -> tuple[dict, set]:
    """Placeholders every template may use, plus the subset that is raw HTML.

    `root` is relative ("./", "../") so the build also works from disk; the 404 page is
    served for arbitrary URLs and therefore uses the absolute base path instead.
    """
    base_url, langs = site["base_url"], site["languages"]
    depth = langs[code]["path"].count("/")
    root = urlsplit(base_url).path if absolute else ("../" * depth or "./")
    alternates = [(c, base_url + _page_url(site, c, page)) for c in langs]
    alternates.append(("x-default", base_url + _page_url(site, site["default_lang"], page)))
    hreflang = "\n".join(
        f'  <link rel="alternate" hreflang="{c}" href="{html.escape(u, quote=True)}">'
        for c, u in alternates)
    switch = []
    for c, meta in langs.items():
        current = ' aria-current="true"' if c == code else ""
        href = html.escape(root + _page_url(site, c, page), quote=True)
        switch.append(f'<a href="{href}" hreflang="{c}" lang="{c}"{current}>'
                      f'{html.escape(meta["label"])}</a>')
    repo = site["repo_url"]
    ctx = {
        "lang": code,
        "root": root,
        # relative pages already sit in their language's folder
        "home": root + langs[code]["path"] if absolute else "./",
        "canonical": base_url + _page_url(site, code, page),
        "og_image": base_url + "assets/img/og.jpg",
        "og_locale": langs[code]["og_locale"],
        "repo_url": repo,
        "releases_url": repo + "/releases/latest",
        "license_url": repo + "/blob/main/LICENSE",
        "hreflang": hreflang,
        "lang_switch": "\n".join(switch),
    }
    return ctx, {"hreflang", "lang_switch"}


def render(template: str, ctx: dict, raw: set, table: dict, used: set, name: str) -> str:
    """Fill every {{key}}; record which table keys were used."""

    def from_ctx(key: str) -> str:
        value = ctx[key]
        return value if key in raw else html.escape(value, quote=True)

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key in ctx:
            return from_ctx(key)
        if key in table:
            used.add(key)
            value = table[key]
            if key.endswith("_html"):
                # only context placeholders may nest; a table key inside a table value
                # would make the translation order-dependent
                def nested(m: re.Match) -> str:
                    if m.group(1) not in ctx:
                        raise BuildError(f"{name}: {key} uses unknown placeholder {m.group(1)!r}")
                    return from_ctx(m.group(1))
                return PLACEHOLDER.sub(nested, value)
            return html.escape(value, quote=True)
        raise BuildError(f"{name}: unknown placeholder {{{{{key}}}}}")

    out = PLACEHOLDER.sub(sub, template)
    if "{{" in out:
        raise BuildError(f"{name}: malformed placeholder left in output")
    return out


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class _Refs(HTMLParser):
    """Collects href/src/srcset targets and element ids of one page."""

    def __init__(self):
        super().__init__()
        self.refs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value is None:
                continue
            if key == "id":
                self.ids.add(value)
            elif key in ("href", "src"):
                self.refs.append(value)
            elif key == "srcset":
                self.refs.extend(part.strip().split()[0] for part in value.split(",") if part.strip())


def check_links(out: Path, base_path: str) -> int:
    """Every local href/src/srcset in the output must point at a built file."""
    problems, checked = [], 0
    for page in sorted(out.rglob("*.html")):
        parser = _Refs()
        parser.feed(page.read_text(encoding="utf-8"))
        rel = page.relative_to(out).as_posix()
        for ref in parser.refs:
            parts = urlsplit(ref)
            if parts.scheme or parts.netloc:
                continue                                   # external: not ours to check
            if not parts.path:
                if parts.fragment and parts.fragment not in parser.ids:
                    problems.append(f"{rel}: #{parts.fragment} has no matching id")
                continue
            path = unquote(parts.path)
            if path.startswith("/"):
                if not path.startswith(base_path):
                    problems.append(f"{rel}: {ref} is outside the site base {base_path}")
                    continue
                target = out / path[len(base_path):]
            else:
                target = page.parent / path
            if path.endswith("/"):
                target = target / "index.html"
            target = target.resolve()
            if not target.is_file() or out.resolve() not in target.parents:
                problems.append(f"{rel}: broken link {ref}")
            checked += 1
    if problems:
        raise BuildError("broken links:\n  " + "\n  ".join(problems))
    return checked


MARKER = ".saberlab-site-build"


def build(src: Path = SITE, out: Path | None = None) -> tuple[Path, int]:
    """Render every page in every language (plus the 404) into `out`."""
    out = (out or src / "dist").resolve()
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise BuildError(f"refusing to clear {out}: not a previous site build")
    site = load_site(src)
    tables = load_tables(src, site)
    try:
        run_ctx, run_raw = site_charts.render(_read_json(src / "data" / "run.json"))
    except site_charts.RunDataError as exc:
        raise BuildError(str(exc)) from None
    clash = sorted(set(run_ctx) & set(tables[site["default_lang"]]))
    if clash:
        raise BuildError(f"run data keys shadow i18n keys: {clash}")

    def context(code: str, page: str, absolute: bool) -> tuple[dict, set]:
        ctx, raw = page_context(site, code, page, absolute)
        return {**ctx, **run_ctx}, raw | run_raw

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src / "assets", out / "assets")
    (out / MARKER).write_text("generated by site/build.py\n", encoding="utf-8")
    used = {code: set() for code in site["languages"]}

    for page in site["pages"]:
        template = _read(src / "templates" / page)
        for code, meta in site["languages"].items():
            ctx, raw = context(code, page, absolute=False)
            _write(out / meta["path"] / page,
                   render(template, ctx, raw, tables[code], used[code], f"{code}/{page}"))

    # GitHub Pages serves one root 404.html for every language: it carries a block per
    # language, each rendered from its own table, around a default-language shell.
    nf_page = site["not_found_page"]
    block = _read(src / "templates" / NOT_FOUND_BLOCK)
    blocks = []
    for code in site["languages"]:
        ctx, raw = context(code, "index.html", absolute=True)
        blocks.append(render(block, ctx, raw, tables[code], used[code], f"{code}/{NOT_FOUND_BLOCK}"))
    default = site["default_lang"]
    ctx, raw = context(default, "index.html", absolute=True)
    ctx["not_found_blocks"] = "\n".join(blocks)
    raw = raw | {"not_found_blocks"}
    _write(out / nf_page, render(_read(src / "templates" / nf_page), ctx, raw,
                                 tables[default], used[default], nf_page))

    for code, table in tables.items():
        unused = sorted(set(table) - used[code])
        if unused:
            raise BuildError(f"i18n/{code}.json: keys no template uses {unused}")
    return out, check_links(out, urlsplit(site["base_url"]).path)


def serve(out: Path, base_path: str, port: int) -> None:
    """Preview the build under the same sub-path GitHub Pages uses (loopback only)."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        # the Windows registry can map these wrongly, and browsers are strict about CSS
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                          ".css": "text/css", ".js": "text/javascript",
                          ".svg": "image/svg+xml", ".webp": "image/webp",
                          ".html": "text/html; charset=utf-8"}

        def translate_path(self, path):
            clean = urlsplit(path).path
            if clean.startswith(base_path):
                return super().translate_path("/" + clean[len(base_path):])
            return str(out / "__outside_base__")        # -> 404, as on Pages

        def send_error(self, code, message=None, explain=None):
            page = out / "404.html"
            if code != 404 or not page.is_file():
                return super().send_error(code, message, explain)
            body = page.read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(out)))
    print(f"preview: http://127.0.0.1:{port}{base_path}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the SaberLab website.")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: site/dist)")
    parser.add_argument("--serve", action="store_true", help="preview the build locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        out, links = build(out=args.out)
    except BuildError as exc:
        print(f"site build failed: {exc}", file=sys.stderr)
        return 1
    pages = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html"))
    print(f"built {len(pages)} pages ({', '.join(pages)}), {links} local links verified -> {out}")
    if args.serve:
        serve(out, urlsplit(load_site(SITE)["base_url"]).path, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
