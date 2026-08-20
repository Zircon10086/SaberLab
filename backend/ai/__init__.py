"""AI 分析层：确定性指标 -> LLM 解释与建议（设计文档 §15）。"""
from .provider import LLMClient, LLMError
from .context import build_context
from .report import run_ai_report

__all__ = ["LLMClient", "LLMError", "build_context", "run_ai_report"]
