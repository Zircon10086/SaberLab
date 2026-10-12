"""Architecture invariant tests (2026-09, turning the AGENTS §21 red lines into
machine checks).

These constraints used to be held by human memory and code review; there is no
violation today, but every change had to be re-remembered by a person — these tests
pin them down so a regression turns red immediately. Style follows
`tests/test_i18n_mapping.py`: assert on AST/file facts, never on text-matching
guesses.

Two classes of red line are covered:

1. **Layer ownership**: database access lives only in `backend/db/`; the analysis
   layer has no network, no process and no randomness source.
2. **Request-origin guard**: every state-changing endpoint sits under the guard;
   the guard has no exemption list; a read endpoint with side effects must be
   registered explicitly — adding one means editing this test, and it may not
   appear silently.
"""
from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess
import unittest

from fastapi.testclient import TestClient

import backend.main as m

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
AGENTS_MD = PROJECT_ROOT / "AGENTS.md"

# The analysis layer must stay purely deterministic: modules that may not appear.
ANALYSIS_FORBIDDEN = {
    "urllib", "http", "requests", "socket", "pywebview", "subprocess",
    "random", "uuid",
}

# Read endpoints that change state: they must be registered explicitly, and they
# can only be reached through the tightened enable gate. Detection is based on
# whether the handler body calls a function named with a "verb prefix" (see
# _mutating_calls).
# `/api/maps/{map_hash}/cover` triggers a targeted scan and writes to the DB when
# the row exists but the path is stale, so it is not a pure read endpoint; the
# "refuse cross-site marker" rule is what stops subresource-load-driven side
# effects.
SIDE_EFFECTING_GET_PATHS = {"/api/maps/{map_hash}/cover"}

MUTATING_PREFIXES = (
    "ensure_", "save_", "update_", "delete_", "insert_", "set_", "remove_",
    "write_", "create_", "recycle",
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _iter_backend_modules() -> list[pathlib.Path]:
    return sorted(p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Top-level module names imported by a source file (AST, not text match)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
    return out


def _call_names(node: ast.AST) -> set[str]:
    """Every called function/method name inside a handler body."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Attribute):
                out.add(sub.func.attr)
            elif isinstance(sub.func, ast.Name):
                out.add(sub.func.id)
    return out


def _declared_routes() -> list[tuple[str, set[str], str, list[str]]]:
    """(path, methods, handler name, mutating-looking calls) for every app route."""
    tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    routes: list[tuple[str, set[str], str, list[str]]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = []
        for deco in node.decorator_list:
            if (isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and isinstance(deco.func.value, ast.Name)
                    and deco.func.value.id == "app"
                    and deco.args):
                try:
                    decorators.append((deco.func.attr.upper(),
                                       ast.literal_eval(deco.args[0])))
                except ValueError:                      # pragma: no cover
                    continue
        if not decorators:
            continue
        methods = {method for method, _ in decorators}
        path = next((p for _, p in decorators if isinstance(p, str)), None)
        if path is None:
            continue
        muts = sorted(c for c in _call_names(node) if c.startswith(MUTATING_PREFIXES))
        routes.append((path, methods, node.name, muts))
    return routes


class LayerOwnershipTest(unittest.TestCase):
    """AGENTS §2 / §21: layer ownership."""

    def test_sqlite3_is_imported_only_by_the_db_layer(self):
        offenders = [
            str(p.relative_to(PROJECT_ROOT))
            for p in _iter_backend_modules()
            if "sqlite3" in _imported_modules(p) and p.parent.name != "db"
        ]
        self.assertEqual(offenders, [],
                         f"数据库访问必须留在 backend/db/ 内: {offenders}")

    def test_analysis_layer_has_no_network_or_process_imports(self):
        offenders = {}
        for path in sorted((BACKEND / "analysis").glob("*.py")):
            bad = sorted(_imported_modules(path) & ANALYSIS_FORBIDDEN)
            if bad:
                offenders[path.name] = bad
        self.assertEqual(offenders, {},
                         f"分析层必须可离线、可复现: {offenders}")

    def test_analysis_layer_is_deterministic(self):
        """A randomness source would make the same replay produce different conclusions (AGENTS §1.2)."""
        offenders = {}
        for path in sorted((BACKEND / "analysis").glob("*.py")):
            bad = sorted(_imported_modules(path) & {"random", "uuid", "secrets"})
            if bad:
                offenders[path.name] = bad
        self.assertEqual(offenders, {}, f"分析层不得引入随机源: {offenders}")


class RepositoryHygieneTest(unittest.TestCase):
    """AGENTS §20: no secrets committed."""

    def test_env_file_is_not_tracked(self):
        if shutil.which("git") is None:                 # pragma: no cover
            self.skipTest("git 不可用")
        out = subprocess.run(["git", "ls-files"], cwd=PROJECT_ROOT,
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:                         # pragma: no cover
            self.skipTest(f"git ls-files 失败: {out.stderr.strip()}")
        tracked = {line.strip().replace("\\", "/") for line in out.stdout.splitlines()}
        self.assertNotIn(".env", tracked, "密钥文件不得进入版本控制")
        self.assertNotIn("config/config.yaml", tracked, "用户配置不得进入版本控制")

    def test_agents_md_has_no_escape_pollution(self):
        """AGENTS.md was once escape-polluted (runs of backslashes), which made the §24 checklist
        render wrong twice."""
        text = AGENTS_MD.read_text(encoding="utf-8")
        runs = [m.group(0) for m in re.finditer(r"\\{8,}", text)]
        self.assertEqual(runs, [], "AGENTS.md 含连续 8 个以上反斜杠（转义污染）")
        # The 12 checklist items of §24 must stay in checkable form
        self.assertEqual(len(re.findall(r"(?m)^- \[ \] ", text)), 12,
                         "AGENTS.md §24 完成清单的 12 条 checkbox 形态被破坏")


class RequestGuardCoverageTest(unittest.TestCase):
    """Structural guarantee for the origin guard: miss one endpoint and it turns red, with no
    hand-maintained list."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(m.app, base_url="http://127.0.0.1:6980")
        cls.routes = _declared_routes()

    def test_route_scan_actually_sees_routes(self):
        self.assertGreater(len(self.routes), 40, "路由扫描失效（应看到实装端点）")
        state_changing = [r for r in self.routes if not r[1] <= SAFE_METHODS]
        self.assertGreater(len(state_changing), 20, "会改动状态的端点数量异常")

    def test_guard_middleware_is_installed(self):
        dispatches = [mw.kwargs.get("dispatch") for mw in m.app.user_middleware]
        self.assertIn(m._local_request_guard, dispatches,
                      "来源闸门中间件未挂载，所有端点将失去保护")

    def test_every_state_changing_route_is_refused_on_cross_site_request(self):
        """A cross-site request hitting any state-changing endpoint must be stopped by the guard."""
        refused, allowed = [], []
        for path, methods, name, _ in self.routes:
            if methods <= SAFE_METHODS:
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            for method in sorted(methods):
                resp = self.client.request(
                    method, url, headers={"sec-fetch-site": "cross-site"})
                (refused if resp.status_code == 403 else allowed).append(
                    f"{method} {path} ({name}) -> {resp.status_code}")
        self.assertEqual(allowed, [],
                         "以下改状态端点未被闸门覆盖:\n" + "\n".join(allowed))
        self.assertGreater(len(refused), 20)

    def test_guard_has_no_exemption_list(self):
        """Once the guard grows a path-exemption branch, "full coverage" stops being a structural guarantee."""
        import inspect
        source = inspect.getsource(m._local_request_guard)
        self.assertNotIn("/api", source, "闸门内不得出现按路径的分支")
        self.assertNotIn("url.path", source, "闸门不得读取请求路径做判定")

    def test_side_effecting_get_registry_is_current(self):
        """A read endpoint with side effects must be registered explicitly: adding one means
        editing this test.

        The heuristic only recognises calls named with a "verb prefix" (enough to catch
        the current single case); if a reviewer finds a new side-effecting read endpoint
        whose name has no verb prefix, it should still be written into
        SIDE_EFFECTING_GET_PATHS."""
        detected = {
            path for path, methods, _, muts in self.routes
            if methods <= SAFE_METHODS and muts
        }
        self.assertEqual(detected, SIDE_EFFECTING_GET_PATHS,
                         "带副作用的读取端点登记表与实际不符（新增/删除都要更新）")
        declared_paths = {path for path, _, _, _ in self.routes}
        for path in SIDE_EFFECTING_GET_PATHS:
            self.assertIn(path, declared_paths,
                          f"登记表里的端点已不存在，请更新登记表: {path}")


if __name__ == "__main__":
    unittest.main()
