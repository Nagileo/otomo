"""Operator-maintained corrections for complex anime franchise ordering.

Bangumi relations remain the default source.  This small, auditable store is
only used for franchises where community edges cannot express a dependable
mainline (for example alternate cuts, recaps, or non-linear release order).
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Iterator, Literal

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


_BUILTIN_RULE_PAYLOADS: tuple[dict[str, object], ...] = (
    {
        "id": "monogatari-main-release-order",
        "title": "〈物语〉系列（首次观看推荐顺序）",
        "mainline": [
            {"subject_id": 1671, "name": "化物语", "necessity": "required", "note": "系列动画入口。"},
            {"subject_id": 7707, "name": "伤物语〈I 铁血篇〉", "necessity": "recommended", "note": "接在《化物语》后补齐阿良良木与忍的前史。"},
            {"subject_id": 148036, "name": "伤物语〈II 热血篇〉", "necessity": "recommended", "note": "伤物语三部曲第二部。"},
            {"subject_id": 148037, "name": "伤物语〈III 冷血篇〉", "necessity": "recommended", "note": "伤物语三部曲完结。"},
            {"subject_id": 23161, "name": "伪物语", "necessity": "required", "note": "主线发行顺序第二部。"},
            {"subject_id": 56117, "name": "猫物语（黑）", "necessity": "required", "note": "承接羽川线并进入第二季。"},
            {"subject_id": 68812, "name": "物语系列 第二季", "necessity": "required", "note": "包含猫白、倾、囮、鬼、恋等篇章。"},
            {"subject_id": 82322, "name": "花物语", "necessity": "recommended", "note": "发行时独立播出，建议在第二季后观看。"},
            {"subject_id": 115932, "name": "凭物语", "necessity": "required", "note": "Final Season 开端。"},
            {"subject_id": 138829, "name": "终物语", "necessity": "required", "note": "Final Season 主线。"},
            {"subject_id": 146104, "name": "历物语", "necessity": "recommended", "note": "末两话直接衔接《终物语（下）》。"},
            {"subject_id": 175596, "name": "终物语（下）", "necessity": "required", "note": "主线结局。"},
            {"subject_id": 233926, "name": "续·终物语", "necessity": "recommended", "note": "主线后日谈。"},
            {"subject_id": 475354, "name": "物语系列 外传季&怪物季", "necessity": "recommended", "note": "2024 年续篇，放在《续·终物语》之后。"},
        ],
        "optional": [],
        "alternates": [],
        "notes": [
            "这是一条首次观看推荐顺序，不冒充严格的动画播出顺序；《伤物语》因制作延期，按原作刊行脉络放在《化物语》之后。",
            "条目 ID 已按 Bangumi API 实际条目核对；运营者仍可用同 ID 规则整体覆盖。",
        ],
    },
    {
        "id": "fate-stay-night-core-routes",
        "title": "Fate/stay night 核心动画路线",
        "mainline": [
            {"subject_id": 95225, "name": "Fate/stay night [Unlimited Blade Works]", "necessity": "required", "note": "Ufotable TV 版第一季，作为默认现代动画入口。"},
            {"subject_id": 109386, "name": "Fate/stay night [Unlimited Blade Works] 第二季", "necessity": "required", "note": "必须在第一季之后观看。"},
            {"subject_id": 10639, "name": "Fate/Zero", "necessity": "recommended", "note": "时间线是前传，但默认放在 UBW 后以减少本篇谜底剧透。"},
        ],
        "optional": [
            {"subject_id": 109375, "name": "Heaven's Feel I.presage flower", "necessity": "recommended", "note": "HF 独立路线第一章；不要与 UBW 当成第三季。"},
            {"subject_id": 175599, "name": "Heaven's Feel II.lost butterfly", "necessity": "recommended", "note": "HF 第二章。"},
            {"subject_id": 175600, "name": "Heaven's Feel III.spring song", "necessity": "recommended", "note": "HF 第三章。"},
        ],
        "alternates": [
            {"subject_id": 290, "name": "Fate/stay night（2006）", "necessity": "optional", "note": "以 Fate 线为主的旧版改编，可选，不阻塞 UBW。"},
            {"subject_id": 3484, "name": "Unlimited Blade Works（2010 剧场版）", "necessity": "skip", "note": "与 TV 版是同一路线的压缩改编，默认不重复观看。"},
        ],
        "notes": [
            "Fate 各路线不是第一季/第二季的单线关系；HF、2006 版与 UBW 必须显式分支。",
            "本规则只覆盖 stay night 核心路线，不把 Grand Order、伊莉雅等独立世界观混入主线。",
        ],
    },
)


def builtin_series_rules() -> list[SeriesOverrideRule]:
    return [SeriesOverrideRule.model_validate(row) for row in _BUILTIN_RULE_PAYLOADS]


class SeriesOverrideStore:
    """Atomic JSON store read on demand so multiple web workers see edits."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or settings.series_overrides_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = RLock()

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        """Serialize JSON read-modify-write cycles across web workers."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_custom_unlocked(self) -> list[SeriesOverrideRule]:
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

    def _save_custom_unlocked(self, rules: list[SeriesOverrideRule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
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
            with self._process_lock():
                custom = self._load_custom_unlocked()
        merged = {rule.id: rule for rule in builtin_series_rules()}
        merged.update({rule.id: rule for rule in custom})
        return [merged[key] for key in sorted(merged)]

    def get(self, rule_id: str) -> SeriesOverrideRule | None:
        normalized = rule_id.strip().lower()
        return next((rule for rule in self.list() if rule.id == normalized), None)

    def operator_rule_ids(self) -> set[str]:
        with self._lock:
            with self._process_lock():
                return {rule.id for rule in self._load_custom_unlocked()}

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
            with self._process_lock():
                custom = self._load_custom_unlocked()
                effective = {rule.id: rule for rule in builtin_series_rules()}
                effective.update({rule.id: rule for rule in custom})
                other_ids = {
                    sid
                    for existing in effective.values()
                    if existing.id != normalized.id
                    for sid in existing.subject_ids
                }
                overlaps = sorted(normalized.subject_ids & other_ids)
                if overlaps:
                    raise ValueError(f"subject_id 已被其他规则占用：{', '.join(map(str, overlaps))}")
                kept = [existing for existing in custom if existing.id != normalized.id]
                kept.append(normalized)
                self._save_custom_unlocked(kept)
        return normalized

    def delete(self, rule_id: str) -> bool:
        normalized = rule_id.strip().lower()
        with self._lock:
            with self._process_lock():
                rows = self._load_custom_unlocked()
                kept = [rule for rule in rows if rule.id != normalized]
                if len(kept) == len(rows):
                    return False
                self._save_custom_unlocked(kept)
                return True

    def status(self) -> dict[str, object]:
        rows = self.list()
        with self._lock:
            with self._process_lock():
                operator_count = len(self._load_custom_unlocked())
        return {
            "path": str(self.path),
            "rules": len(rows),
            "subjects": sum(len(row.subject_ids) for row in rows),
            "configured": bool(rows),
            "builtin_rules": len(builtin_series_rules()),
            "operator_rules": operator_count,
        }


def suggested_rule_id(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return value[:80] or "series-override"
