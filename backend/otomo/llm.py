"""LLM 接入：统一走 OpenAI 兼容接口，默认 DeepSeek，一键可换（改 LLM_BASE_URL）。

切本地 Qwen（RL 期）：把 LLM_BASE_URL 指向本地 vLLM 的 OpenAI 兼容端点即可，其余不变。
所有 chat.completions 调用经 UsageTrackingLLM 代理，把真实 token 用量记进请求级账本
（quota.add_usage_from_response），供每日配额熔断使用；不在请求上下文时为 no-op。
"""
from __future__ import annotations

from functools import lru_cache
import os
from typing import Any, AsyncIterator

import openai
from openai import AsyncOpenAI

from .config import settings
from .quota import add_usage_from_response


async def _track_stream(stream: Any) -> AsyncIterator[Any]:
    # include_usage 时最后一个 chunk 携带 usage 且 choices 为空；下游循环均已兼容空 choices。
    async for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            add_usage_from_response(chunk)
        yield chunk


class _CompletionsProxy:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def create(self, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            merged = {"include_usage": True, **(kwargs.get("stream_options") or {})}
            try:
                stream = await self._inner.create(**{**kwargs, "stream_options": merged})
            except openai.BadRequestError:
                # 个别兼容端点不认识 stream_options——去掉重试，用量退回估算兜底
                stream = await self._inner.create(**kwargs)
            return _track_stream(stream)
        resp = await self._inner.create(**kwargs)
        add_usage_from_response(resp)
        return resp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _ChatProxy:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def completions(self) -> _CompletionsProxy:
        return _CompletionsProxy(self._inner.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class UsageTrackingLLM:
    """透明代理 AsyncOpenAI：只拦 chat.completions.create 记账，其余属性原样透传。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def chat(self) -> _ChatProxy:
        return _ChatProxy(self._inner.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _build_client() -> Any:
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


@lru_cache(maxsize=1)
def get_llm() -> UsageTrackingLLM:
    return UsageTrackingLLM(_build_client())
