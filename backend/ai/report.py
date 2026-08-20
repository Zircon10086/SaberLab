"""AI 报告执行器：调用 LLM；未配置时使用确定性规则报告兜底。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..config import Config
from ..db.repository import Repository
from .context import build_context
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .provider import LLMClient, LLMError
from .fallback import rule_based_report


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def run_ai_report(repo: Repository, cfg: Config, replay_id: str,
                  client: LLMClient | None = None,
                  context: dict | None = None) -> dict:
    if context is None:
        context = build_context(repo, cfg, replay_id)
    ctx_json = __import__("json").dumps(context, ensure_ascii=False, indent=1)

    report_id = uuid.uuid4().hex[:12]
    base = {"report_id": report_id, "replay_id": replay_id,
            "created_at": _now(), "context_json": ctx_json}

    if client is None:
        client = LLMClient(cfg)

    if not client.configured:
        md = rule_based_report(context)
        repo.save_report({**base, "provider": "rule-based", "model": "-",
                          "status": "rule_based", "report_md": md, "error": None})
        return {"status": "rule_based", "report_id": report_id}

    try:
        import json as _json
        content = client.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": USER_PROMPT_TEMPLATE.format(context_json=ctx_json)},
        ])
        repo.save_report({**base, "provider": client.provider,
                          "model": client.model, "status": "ok",
                          "report_md": content, "error": None})
        return {"status": "ok", "report_id": report_id}
    except LLMError as e:
        md = rule_based_report(context)
        repo.save_report({**base, "provider": client.provider,
                          "model": client.model, "status": "error",
                          "report_md": md,
                          "error": f"LLM 调用失败，已回退规则报告: {e}"})
        return {"status": "error", "report_id": report_id, "error": str(e)}
