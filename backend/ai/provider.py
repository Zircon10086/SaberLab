"""LLM Provider abstraction (design docs §6: vendors are not hardcoded).

All providers go through the OpenAI-compatible /chat/completions endpoint:
- deepseek: https://api.deepseek.com/v1
- qwen:     https://dashscope.aliyuncs.com/compatible-mode/v1
- openai:   https://api.openai.com/v1
- custom:   base_url filled in config.yaml (local gateway / other compatible services)
"""
from __future__ import annotations

import json
import urllib.request

from ..config import Config


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.provider = cfg.ai_provider
        self.base_url = (cfg.ai_base_url or "").rstrip("/")
        self.model = cfg.ai_model
        self.api_key = cfg.ai_api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.provider != "off")

    def chat(self, messages: list[dict], temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        if not self.configured:
            raise LLMError("LLM 未配置（缺少 API key 或 base_url）")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.ai_temperature if temperature is None else temperature,
            "max_tokens": self.cfg.ai_max_tokens if max_tokens is None else max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "SaberLab/1.0",
            },
            method="POST",
        )
        if self.cfg.proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {"http": self.cfg.proxy, "https": self.cfg.proxy}))
        else:
            opener = urllib.request.build_opener()
        try:
            with opener.open(req, timeout=max(60.0, self.cfg.timeout_seconds * 4)) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"LLM 请求失败: {e}") from e
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 返回格式异常: {json.dumps(data)[:500]}") from e
