"""文件式长期记忆（A4 第一刀）。

跨会话持久化用户画像等，键→JSON 文件（gitignored cache/ltm）。后续可换 Postgres（C 阶段）。
插入点（docs/03 §6）：任务开始检索注入、任务结束写回；当前由工具在算出画像时写入、查询时读取。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .consolidate import now_iso
from .models import UserMemory

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "cache" / "ltm"


def _safe_key(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", key)[:80] or "default"


class LongTermMemory:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or _DEFAULT_DIR
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        return self.base / f"{_safe_key(namespace)}__{_safe_key(key)}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._path(namespace, key).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_user(self, username: str) -> UserMemory:
        raw = self.get("user_memory", username)
        if raw is None:
            return UserMemory(username=username)
        try:
            return UserMemory.model_validate(raw)
        except Exception:  # noqa: BLE001
            return UserMemory(username=username)

    def save_user(self, mem: UserMemory) -> None:
        mem.updated_at = now_iso()
        self.set("user_memory", mem.username, mem.model_dump(mode="json", exclude_none=True))
