"""AI analysis layer: deterministic metrics -> LLM interpretation & advice
(design docs §15)."""
from .provider import LLMClient, LLMError
from .context import build_context
from .report import run_ai_report

__all__ = ["LLMClient", "LLMError", "build_context", "run_ai_report"]
