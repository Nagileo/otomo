"""Runtime registry for versioned, media-specific collaborative models."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import settings


class CFModelStatus(BaseModel):
    subject_type: str
    available: bool = False
    stale: bool = False
    path: str = ""
    model: str = ""
    built_at: str = ""
    age_days: float | None = None
    n_users: int = 0
    n_items: int = 0
    n_interactions: int = 0
    version: str = ""
    warnings: list[str] = Field(default_factory=list)


class CFModelRegistry:
    def __init__(self, directory: str | None = None, max_age_days: int | None = None) -> None:
        self.directory = Path(directory or settings.cf_i2i_dir)
        self.max_age_days = max_age_days or settings.cf_model_max_age_days
        self._cache: dict[str, tuple[float, dict[str, Any], CFModelStatus]] = {}

    def load(self, subject_type: str) -> tuple[dict[str, Any], CFModelStatus]:
        path = self.directory / f"i2i_{subject_type}.json"
        if not path.exists():
            return {"items": {}, "meta": {}}, CFModelStatus(
                subject_type=subject_type,
                path=str(path),
                warnings=["该媒介尚未发布协同模型；本轮跳过 CF 召回。"],
            )
        mtime = path.stat().st_mtime
        cached = self._cache.get(subject_type)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        warnings: list[str] = []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload.get("items"), dict):
                raise ValueError("items must be an object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            status = CFModelStatus(
                subject_type=subject_type, path=str(path),
                warnings=[f"协同模型损坏，已跳过：{exc}"],
            )
            return {"items": {}, "meta": {}}, status
        meta = payload.get("meta") or {}
        built_at = str(meta.get("built_at") or "")
        age_days: float | None = None
        if built_at:
            try:
                built = datetime.fromisoformat(built_at.replace("Z", "+00:00"))
                if built.tzinfo is None:
                    built = built.replace(tzinfo=timezone.utc)
                age_days = max((datetime.now(timezone.utc) - built).total_seconds() / 86400, 0)
            except ValueError:
                warnings.append("模型 built_at 无法解析。")
        stale = age_days is not None and age_days > self.max_age_days
        if stale:
            warnings.append(f"协同模型已超过 {self.max_age_days} 天未更新，信号将降权。")
        status = CFModelStatus(
            subject_type=subject_type,
            available=bool(payload["items"]),
            stale=stale,
            path=str(path),
            model=str(meta.get("model") or ""),
            built_at=built_at,
            age_days=round(age_days, 1) if age_days is not None else None,
            n_users=int(meta.get("n_users") or 0),
            n_items=int(meta.get("n_items") or len(payload["items"])),
            n_interactions=int(meta.get("n_interactions") or 0),
            version=str(meta.get("version") or built_at or path.stat().st_mtime_ns),
            warnings=warnings,
        )
        self._cache[subject_type] = (mtime, payload, status)
        return payload, status

    def statuses(self) -> list[CFModelStatus]:
        return [self.load(kind)[1] for kind in ("anime", "book", "music", "game", "real")]


cf_model_registry = CFModelRegistry()
