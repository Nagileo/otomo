"""Small, user-visible execution summaries persisted with assistant messages."""
from __future__ import annotations

from typing import Any


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def trace_item_from_event(event: Any) -> dict[str, Any] | None:
    """Convert a streamed event into the stable trace shape consumed by the web UI."""
    event_type = _text(getattr(event, "type", ""), 40)
    if event_type == "plan":
        return {"kind": "note", "text": f"📋 {_text(getattr(event, 'summary', ''))}"}
    if event_type == "reflect":
        note = "完整" if bool(getattr(event, "complete", False)) else _text(getattr(event, "note", ""))
        return {"kind": "note", "text": f"↺ 反思：{note}"}
    if event_type == "tool_call":
        args = getattr(event, "args", {})
        return {
            "kind": "call",
            "name": _text(getattr(event, "name", ""), 120),
            "args": args if isinstance(args, dict) else {},
        }
    if event_type == "progress":
        item: dict[str, Any] = {
            "kind": "progress",
            "tool": _text(getattr(event, "tool", ""), 120),
            "summary": _text(getattr(event, "summary", "")),
        }
        current = getattr(event, "current", None)
        total = getattr(event, "total", None)
        note = _text(getattr(event, "note", ""))
        if current is not None:
            item["current"] = current
        if total is not None:
            item["total"] = total
        if note:
            item["note"] = note
        return item
    if event_type == "observation":
        return {
            "kind": "obs",
            "name": _text(getattr(event, "name", ""), 120),
            "ok": bool(getattr(event, "ok", False)),
            "summary": _text(getattr(event, "summary", "")),
        }
    if event_type == "claim_check":
        supported = int(getattr(event, "supported_count", 0) or 0)
        unsupported = int(getattr(event, "unsupported_count", 0) or 0)
        if supported + unsupported:
            rate = float(getattr(event, "support_rate", 0) or 0)
            text = f"证据校验：support {rate * 100:.0f}% · unsupported {unsupported}"
        else:
            text = "证据校验：无强 canonical 硬事实需要自动回退"
        return {"kind": "note", "text": text}
    if event_type == "error":
        return {
            "kind": "obs",
            "name": "error",
            "ok": False,
            "summary": _text(getattr(event, "message", "运行失败")),
        }
    return None


def step_from_event(event: Any) -> str:
    """Return the concise line shown in the collapsed per-message process view."""
    event_type = _text(getattr(event, "type", ""), 40)
    if event_type == "plan":
        return f"规划：{_text(getattr(event, 'summary', ''), 160)}"
    if event_type == "progress":
        return _text(getattr(event, "summary", ""), 160)
    if event_type == "observation":
        mark = "✓" if bool(getattr(event, "ok", False)) else "✗"
        return f"{mark} {_text(getattr(event, 'summary', ''), 156)}"
    if event_type == "error":
        return f"✗ {_text(getattr(event, 'message', '运行失败'), 156)}"
    return ""
