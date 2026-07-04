"""Rate limits and lightweight daily token quotas.

This module deliberately keeps the interface small: the in-process limiter is
good enough for the first single-worker deployment, while the quota store can
later be swapped for Redis/Postgres without touching the FastAPI route logic.
"""
from __future__ import annotations

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


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
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
            raise HTTPException(status_code=429, detail="今日全局 LLM/VLM 配额已用完，请明天再试")
        if settings.daily_token_budget_user > 0 and user_used >= settings.daily_token_budget_user:
            raise HTTPException(status_code=429, detail="你今日的 LLM/VLM 配额已用完，请明天再试")

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
