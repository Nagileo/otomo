"""LLM 接入：统一走 OpenAI 兼容接口，默认 DeepSeek，一键可换（改 LLM_BASE_URL）。

切本地 Qwen（RL 期）：把 LLM_BASE_URL 指向本地 vLLM 的 OpenAI 兼容端点即可，其余不变。
"""
from __future__ import annotations

from functools import lru_cache
import os

from openai import AsyncOpenAI

from .config import settings


@lru_cache(maxsize=1)
def get_llm() -> AsyncOpenAI:
    # 某些本地端点不校验 key，但 SDK 要求非空
    kwargs = {"base_url": settings.llm_base_url, "api_key": settings.llm_api_key or "EMPTY"}
    if settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            # Langfuse OpenAI wrapper 读取 os.environ；pydantic-settings 读到 .env
            # 不等于这些值已写入进程环境，故这里显式桥接，避免 public_key 误报为空。
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
            os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
            os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
            # 配了 Langfuse → 用其 OpenAI wrapper，LLM 调用自动上报（prompt/completion/token/延迟）
            from langfuse.openai import AsyncOpenAI as LangfuseOpenAI
            return LangfuseOpenAI(**kwargs)
        except ImportError:
            pass  # 没装 langfuse → 退普通（本地 trace JSONL 仍在）
    return AsyncOpenAI(**kwargs)
