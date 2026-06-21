"""LLM 接入：统一走 OpenAI 兼容接口，默认 DeepSeek，一键可换（改 LLM_BASE_URL）。

切本地 Qwen（RL 期）：把 LLM_BASE_URL 指向本地 vLLM 的 OpenAI 兼容端点即可，其余不变。
"""
from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from .config import settings


@lru_cache(maxsize=1)
def get_llm() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        # 某些本地端点不校验 key，但 SDK 要求非空
        api_key=settings.llm_api_key or "EMPTY",
    )
