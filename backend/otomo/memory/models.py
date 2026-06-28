"""Typed long-term memory models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemSource = Literal["explicit_user", "bangumi_profile", "derived_from_feedback"]
SpoilerDefault = Literal["none", "mild", "full"]
FeedbackSignal = Literal["like", "dislike", "more", "less"]


class MemoryItem(BaseModel):
    value: str
    source: MemSource = "explicit_user"
    confidence: float = Field(0.6, ge=0.0, le=1.0)
    ts: str = ""


class ProgressItem(BaseModel):
    episode: int
    source: MemSource = "explicit_user"
    confidence: float = Field(0.9, ge=0.0, le=1.0)
    ts: str = ""


class FeedbackItem(BaseModel):
    subject_id: int | None = None
    name: str = ""
    signal: FeedbackSignal
    note: str = ""
    source: MemSource = "explicit_user"
    confidence: float = Field(0.8, ge=0.0, le=1.0)
    ts: str = ""


class UserMemory(BaseModel):
    username: str
    likes: list[MemoryItem] = Field(default_factory=list)
    dislikes: list[MemoryItem] = Field(default_factory=list)
    spoiler_default: SpoilerDefault = "none"
    progress: dict[str, ProgressItem] = Field(default_factory=dict)
    feedback: list[FeedbackItem] = Field(default_factory=list)
    affinity_cache: dict[str, dict] = Field(default_factory=dict)
    profile_snapshot: dict = Field(default_factory=dict)
    updated_at: str = ""


class MemorySummary(BaseModel):
    username: str
    likes: list[MemoryItem] = Field(default_factory=list)
    dislikes: list[MemoryItem] = Field(default_factory=list)
    spoiler_default: SpoilerDefault = "none"
    progress: dict[str, ProgressItem] = Field(default_factory=dict)
    recent_feedback: list[FeedbackItem] = Field(default_factory=list)
    profile_snapshot: dict = Field(default_factory=dict)
    updated_at: str = ""


def memory_summary(mem: UserMemory, feedback_limit: int = 8) -> MemorySummary:
    recent_feedback = mem.feedback[-feedback_limit:] if feedback_limit > 0 else []
    return MemorySummary(
        username=mem.username,
        likes=mem.likes[:12],
        dislikes=mem.dislikes[:12],
        spoiler_default=mem.spoiler_default,
        progress=dict(list(mem.progress.items())[:20]),
        recent_feedback=recent_feedback,
        profile_snapshot=mem.profile_snapshot,
        updated_at=mem.updated_at,
    )
