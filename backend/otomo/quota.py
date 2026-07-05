"""Rate limits and lightweight daily token quotas.

This module deliberately keeps the interface small: the in-process limiter is
good enough for the first single-worker deployment, while the quota store can
later be swapped for Redis/Postgres without touching the FastAPI route logic.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
import json
import time
from threading import Lock
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from .config import settings


def estimate_tokens(*texts: str) -> int:
    """Cheap server-side usage proxy for providers that do not stream usage.

    Chinese and mixed ACGN answers are usually denser than English prose, so
    len/2.4 is intentionally conservative enough for cost guardrails.
    """
    chars = sum(len(x or "") for x in texts)
    return max(1, int(chars / 2.4) + 8)


# 请求级 LLM/VLM 真实用量累加器。值是可变 list——SSE generator 与工具子 task 的
# context fork 拷贝的是同一 list 引用，所以任意深度的调用都记到同一账本上。
_USAGE_LEDGER: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "otomo_llm_usage_ledger",
    default=None,
)


def begin_usage_ledger() -> None:
    """在请求入口开一本新账；不在请求上下文里时其余函数均为 no-op。"""
    _USAGE_LEDGER.set([])


def add_usage(prompt_tokens: int, completion_tokens: int) -> None:
    ledger = _USAGE_LEDGER.get()
    if ledger is None:
        return
    total = max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))
    if total:
        ledger.append(total)


def add_usage_from_response(resp: Any) -> None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    # DeepSeek 的前缀缓存命中计费约为原价 1/10（system prompt + 73 个工具 schema
    # 每轮几乎全命中）；按折算计入，让配额数字接近真实成本比例，避免误熔断。
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if cache_hit is None:
        extra = getattr(usage, "model_extra", None) or {}
        cache_hit = extra.get("prompt_cache_hit_tokens")
    if cache_hit:
        hit = min(int(cache_hit), prompt)
        prompt = (prompt - hit) + hit // 10
    add_usage(prompt, completion)


def collected_usage() -> int:
    ledger = _USAGE_LEDGER.get()
    return sum(ledger) if ledger else 0


def client_ip(request: Request) -> str:
    # 反代（Caddy）已把 X-Forwarded-For 覆盖为真实 client ip；即便上游漏配，
    # 取最右值也只信任离服务最近的一跳，客户端预置的伪造链排在左侧不采信。
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@dataclass
class _Bucket:
    timestamps: list[float]


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        if not settings.rate_limit_enabled or limit <= 0:
            return
        now = time.monotonic()
        cutoff = now - window_seconds
        bucket = self._buckets.setdefault(key, _Bucket([]))
        bucket.timestamps = [x for x in bucket.timestamps if x >= cutoff]
        if len(bucket.timestamps) >= limit:
            retry_after = max(1, int(bucket.timestamps[0] + window_seconds - now))
            raise HTTPException(
                status_code=429,
                detail=f"请求过快，请 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.timestamps.append(now)

    def cleanup(self) -> None:
        now = time.monotonic()
        stale = [key for key, bucket in self._buckets.items() if not bucket.timestamps or max(bucket.timestamps) < now - 7200]
        for key in stale:
            self._buckets.pop(key, None)


class TokenQuotaStore:
    def __init__(self, path: str | None = None) -> None:
        raw = Path(path or settings.quota_store_path)
        self.path = raw if raw.is_absolute() else Path(__file__).resolve().parents[2] / raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"date": self._today(), "global": 0, "users": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"date": self._today(), "global": 0, "users": {}}
        if data.get("date") != self._today():
            data = {"date": self._today(), "global": 0, "users": {}}
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def check(self, user_key: str) -> None:
        if settings.daily_token_budget_user <= 0 and settings.daily_token_budget_global <= 0:
            return
        with self._lock:
            data = self._load()
        user_used = int((data.get("users") or {}).get(user_key, 0) or 0)
        global_used = int(data.get("global", 0) or 0)
        if settings.daily_token_budget_global > 0 and global_used >= settings.daily_token_budget_global:
            raise HTTPException(
                status_code=429,
                detail=f"今日全局 LLM/VLM 配额已用完（已计 {global_used:,}/{settings.daily_token_budget_global:,}），请明天再试",
            )
        if settings.daily_token_budget_user > 0 and user_used >= settings.daily_token_budget_user:
            raise HTTPException(
                status_code=429,
                detail=f"你今日的 LLM/VLM 配额已用完（已计 {user_used:,}/{settings.daily_token_budget_user:,}），请明天再试",
            )

    def record(self, user_key: str, tokens: int) -> dict[str, int]:
        if tokens <= 0:
            return {"user": 0, "global": 0}
        with self._lock:
            data = self._load()
            users = data.setdefault("users", {})
            users[user_key] = int(users.get(user_key, 0) or 0) + int(tokens)
            data["global"] = int(data.get("global", 0) or 0) + int(tokens)
            self._save(data)
            return {"user": int(users[user_key]), "global": int(data["global"])}
