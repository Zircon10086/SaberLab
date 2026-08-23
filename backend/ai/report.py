"""AI report executor: calls the LLM; falls back to the deterministic rule
report when the LLM is not configured (or fails)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..config import Config
from ..db.repository import Repository
from .context import build_context
from .prompts import USER_PROMPT_TEMPLATE, build_system_prompt
from .provider import LLMClient, LLMError
from .fallback import rule_based_report


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def run_ai_report(repo: Repository, cfg: Config, replay_id: str,
                  client: LLMClient | None = None,
                  context: dict | None = None,
                  lang: str = "zh-CN") -> dict:
    """Generate (or fall back on) an AI report for one replay.

    `lang`: UI language code (zh-CN / en-US / ...) — appended to the system
    prompt as an explicit output-language instruction (see prompts.py).
    """
    if context is None:
        context = build_context(repo, cfg, replay_id)
    ctx_json = __import__("json").dumps(context, ensure_ascii=False, indent=1)

    report_id = uuid.uuid4().hex[:12]
    base = {"report_id": report_id, "replay_id": replay_id,
            "created_at": _now(), "context_json": ctx_json}

    if client is None:
        client = LLMClient(cfg)

    if not cfg.ai_report_enabled:
        # "Use AI for reports" is unchecked (settings → AI) — deterministic
        # rule report, never call the LLM (2026-08). Status stays
        # "rule_based" so the frontend renders the rule-report banner.
        md = rule_based_report(context, lang=lang)
        repo.save_report({**base, "provider": "rule-based", "model": "-",
                          "status": "rule_based", "report_md": md, "error": None})
        return {"status": "rule_based", "report_id": report_id}

    if not client.configured:
        md = rule_based_report(context, lang=lang)
        repo.save_report({**base, "provider": "rule-based", "model": "-",
                          "status": "rule_based", "report_md": md, "error": None})
        return {"status": "rule_based", "report_id": report_id}

    try:
        import json as _json
        content = client.chat([
            {"role": "system", "content": build_system_prompt(lang)},
            {"role": "user",
             "content": USER_PROMPT_TEMPLATE.format(context_json=ctx_json)},
        ])
        repo.save_report({**base, "provider": client.provider,
                          "model": client.model, "status": "ok",
                          "report_md": content, "error": None})
        return {"status": "ok", "report_id": report_id}
    except LLMError as e:
        md = rule_based_report(context, lang=lang)
        repo.save_report({**base, "provider": client.provider,
                          "model": client.model, "status": "error",
                          "report_md": md,
                          "error": f"LLM 调用失败，已回退规则报告: {e}"})
        return {"status": "error", "report_id": report_id, "error": str(e)}
