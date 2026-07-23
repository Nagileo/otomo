"""RL 轨迹飞轮：部署期把真实对话轨迹 + 用户反馈落盘，为未来 RL 阶段攒种子数据。

动机（docs/15 pre-RL 主线）：等 qwen3.5 级策略模型 + 算力到位时，最缺的是**匹配真实
分布的轨迹语料**。部署后的每一轮真实对话（完整 message 列表含工具调用/观察 + 最终
答案 + 真实 token 用量 + 👍👎 反馈）就是拒绝采样 / SFT / DPO 的原料——上线第一天开始攒。

格式：cache/trajectories/YYYY-MM-DD.jsonl 每行一轮；feedback.jsonl 每行一条评价，
按 turn_id 关联。owner 只存加盐哈希（伪匿名，导出时另有脱敏）。导出/清洗见
scripts/export_trajectories.py。
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

_LOCK = threading.Lock()
_MAX_CONTENT_CHARS = 6000  # 单条 message 内容截断（工具观察可能巨大；训练时通常也会截）


def _dir() -> Path:
    p = Path(settings.trajectory_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_hash(owner: str) -> str:
    return hashlib.sha256(f"otomo-traj:{owner}".encode()).hexdigest()[:12]


def _compact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m.get("role", "")}
        content = m.get("content")
        if isinstance(content, str) and len(content) > _MAX_CONTENT_CHARS:
            item["content"] = content[:_MAX_CONTENT_CHARS] + f"…[截断，原长 {len(content)}]"
        else:
            item["content"] = content
        for k in ("tool_calls", "tool_call_id", "name"):
            if m.get(k) is not None:
                item[k] = m[k]
        out.append(item)
    return out


def _append(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_turn(
    *,
    turn_id: str,
    session_id: str,
    owner: str,
    runner: str,
    user_message: str,
    final_answer: str,
    messages: list[dict[str, Any]] | None,
    tools_called: list[str],
    usage_tokens: int,
) -> None:
    """一轮对话完成后调用（chat 端点 finally）。失败静默——轨迹采集绝不影响服务。"""
    if not settings.trajectory_log_enabled:
        return
    try:
        record = {
            "turn_id": turn_id,
            "ts": _now_iso(),
            "session_id": session_id,
            "owner_hash": owner_hash(owner),
            "runner": runner,
            "user_message": user_message,
            "final_answer": final_answer,
            "tools_called": tools_called,
            "usage_tokens": usage_tokens,
            "messages": _compact_messages(messages or []),
        }
        _append(_dir() / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl", record)
    except Exception:  # noqa: BLE001
        pass


def record_feedback(
    *, turn_id: str, session_id: str, owner: str, rating: str, note: str = ""
) -> dict[str, Any]:
    """答案级 👍/👎/clear 事件；导出按 turn_id 采用最后一条。"""
    record = {
        "turn_id": turn_id,
        "ts": _now_iso(),
        "session_id": session_id,
        "owner_hash": owner_hash(owner),
        "rating": rating,
        "note": note[:500],
    }
    _append(_dir() / "feedback.jsonl", record)
    return record
