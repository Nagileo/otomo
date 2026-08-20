"""Operator-maintained corrections for complex anime franchise ordering.

Bangumi relations remain the default source.  This small, auditable store is
only used for franchises where community edges cannot express a dependable
mainline (for example alternate cuts, recaps, or non-linear release order).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from threading import RLock
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .config import settings

OverrideNecessity = Literal["required", "recommended", "optional", "skip"]
OverrideRole = Literal["main", "optional", "alternate"]


class SeriesOverrideMember(BaseModel):
    subject_id: int = Field(..., ge=1)
    name: str = Field("", max_length=160)
    necessity: OverrideNecessity = "required"
    note: str = Field("", max_length=320)


class SeriesOverrideRule(BaseModel):
    id: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    title: str = Field(..., min_length=1, max_length=160)
    mainline: list[SeriesOverrideMember] = Field(..., min_length=1, max_length=80)
    optional: list[SeriesOverrideMember] = Field(default_factory=list, max_length=40)
    alternates: list[SeriesOverrideMember] = Field(default_factory=list, max_length=40)
    notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_members(self) -> "SeriesOverrideRule":
        ids = [item.subject_id for item in self.mainline + self.optional + self.alternates]
        if len(ids) != len(set(ids)):
            raise ValueError("同一条人工系列规则中 subject_id 不能重复")
        if not any(item.necessity == "required" for item in self.mainline):
            raise ValueError("人工主线至少需要一个 required 条目")
        self.notes = [note.strip() for note in self.notes if note.strip()]
        return self

    @property
    def subject_ids(self) -> set[int]:
        return {item.subject_id for item in self.mainline + self.optional + self.alternates}

    def member(self, subject_id: int) -> tuple[OverrideRole, SeriesOverrideMember] | None:
        for role, rows in (
            ("main", self.mainline),
            ("optional", self.optional),
            ("alternate", self.alternates),
        ):
            for row in rows:
                if row.subject_id == subject_id:
                    return role, row
        return None


class SeriesOverrideStore:
    """Atomic JSON store read on demand so multiple web workers see edits."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or settings.series_overrides_path)
        self._lock = RLock()

    def _load_unlocked(self) -> list[SeriesOverrideRule]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"人工系列规则无法读取：{exc}") from exc
        rows = payload.get("rules", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError("人工系列规则格式错误：rules 必须是数组")
        return [SeriesOverrideRule.model_validate(row) for row in rows]

    def _save_unlocked(self, rules: list[SeriesOverrideRule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = {
            "version": 1,
            "rules": [rule.model_dump(mode="json") for rule in sorted(rules, key=lambda row: row.id)],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def list(self) -> list[SeriesOverrideRule]:
        with self._lock:
            return self._load_unlocked()

    def get(self, rule_id: str) -> SeriesOverrideRule | None:
        normalized = rule_id.strip().lower()
        return next((rule for rule in self.list() if rule.id == normalized), None)

    def find_by_subject(self, subject_id: int) -> SeriesOverrideRule | None:
        matches = [rule for rule in self.list() if int(subject_id) in rule.subject_ids]
        if len(matches) > 1:
            ids = ", ".join(rule.id for rule in matches)
            raise RuntimeError(f"subject {subject_id} 同时出现在多条人工系列规则中：{ids}")
        return matches[0] if matches else None

    def upsert(self, rule: SeriesOverrideRule) -> SeriesOverrideRule:
        normalized = rule.model_copy(update={"id": rule.id.strip().lower()})
        # Re-validate model_copy updates and disallow an ID from being assigned
        # to two franchises, which would make routing non-deterministic.
        normalized = SeriesOverrideRule.model_validate(normalized.model_dump())
        with self._lock:
            rows = self._load_unlocked()
            other_ids = {
                sid
                for existing in rows
                if existing.id != normalized.id
                for sid in existing.subject_ids
            }
            overlaps = sorted(normalized.subject_ids & other_ids)
            if overlaps:
                raise ValueError(f"subject_id 已被其他规则占用：{', '.join(map(str, overlaps))}")
            kept = [existing for existing in rows if existing.id != normalized.id]
            kept.append(normalized)
            self._save_unlocked(kept)
        return normalized

    def delete(self, rule_id: str) -> bool:
        normalized = rule_id.strip().lower()
        with self._lock:
            rows = self._load_unlocked()
            kept = [rule for rule in rows if rule.id != normalized]
            if len(kept) == len(rows):
                return False
            self._save_unlocked(kept)
            return True

    def status(self) -> dict[str, object]:
        rows = self.list()
        return {
            "path": str(self.path),
            "rules": len(rows),
            "subjects": sum(len(row.subject_ids) for row in rows),
            "configured": bool(rows),
        }


def suggested_rule_id(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return value[:80] or "series-override"
