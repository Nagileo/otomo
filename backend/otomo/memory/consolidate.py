"""Memory consolidation helpers.

The key rule is: memory writes are not append-only. A new explicit preference may
update an existing item, remove the opposite item, or be ignored as a lower
confidence duplicate.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from .models import MemSource, MemoryItem, UserMemory

Action = Literal["ADD", "UPDATE", "DELETE", "NOOP"]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in value.strip() if not ch.isspace())


def match_item(items: list[MemoryItem], value: str) -> MemoryItem | None:
    v = _norm(value)
    if not v:
        return None
    for item in items:
        cur = _norm(item.value)
        if cur == v or (len(v) >= 2 and v in cur) or (len(cur) >= 2 and cur in v):
            return item
    return None


def remove_item(items: list[MemoryItem], value: str) -> bool:
    item = match_item(items, value)
    if item is None:
        return False
    items.remove(item)
    return True


def consolidate_preference(
    mem: UserMemory,
    polarity: Literal["like", "dislike"],
    value: str,
    source: MemSource = "explicit_user",
    confidence: float = 0.8,
) -> tuple[Action, bool]:
    value = value.strip()
    if not value:
        return "NOOP", False
    confidence = max(0.0, min(float(confidence), 1.0))
    same = mem.likes if polarity == "like" else mem.dislikes
    opposite = mem.dislikes if polarity == "like" else mem.likes

    removed_opposite = remove_item(opposite, value)
    current = match_item(same, value)
    ts = now_iso()
    if current:
        if source == "explicit_user" or confidence > current.confidence:
            current.value = value
            current.confidence = max(current.confidence, confidence)
            current.source = source
            current.ts = ts
            return "UPDATE", True
        return ("DELETE", True) if removed_opposite else ("NOOP", False)

    same.append(MemoryItem(value=value, source=source, confidence=confidence, ts=ts))
    return "ADD", True
