"""Typed long-term memory models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemSource = Literal["explicit_user", "bangumi_profile", "derived_from_feedback"]
SpoilerDefault = Literal["none", "mild", "full"]
FeedbackSignal = Literal["like", "dislike", "more", "less"]
WriteActionStatus = Literal["pending", "executed", "cancelled", "failed", "undone"]
WriteOperation = Literal["set_collection", "set_episode_collection"]
DecisionKind = Literal["accept", "reject", "defer", "write", "undo", "plan", "note"]
PlanStatus = Literal["wishlist", "watching", "backlog", "on_hold", "revive", "completed", "rejected"]
AspectKey = Literal[
    "story", "character", "pacing", "visual", "music",
    "direction", "text", "system", "voice", "general",
]
AspectPolarity = Literal["like", "dislike"]


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


class PendingWriteAction(BaseModel):
    id: str
    operation: WriteOperation
    summary: str
    subject_id: int | None = None
    subject_name: str = ""
    episode_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    status: WriteActionStatus = "pending"
    created_at: str = ""
    executed_at: str = ""
    error: str = ""
    source: str = "agent"


class DecisionLogItem(BaseModel):
    id: str
    kind: DecisionKind
    subject_id: int | None = None
    subject_name: str = ""
    operation: str = ""
    reason: str = ""
    action_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    confirmed: bool = False
    source: str = "agent"
    ts: str = ""


class WatchPlanItem(BaseModel):
    id: str
    subject_id: int
    name: str
    subject_type: str = "anime"
    status: PlanStatus = "backlog"
    priority: int = Field(3, ge=1, le=5)
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "agent"
    created_at: str = ""
    updated_at: str = ""


class RecommendationListItem(BaseModel):
    id: str
    title: str
    subject_type: str = "anime"
    items: list[dict[str, Any]] = Field(default_factory=list)
    accepted_ids: list[int] = Field(default_factory=list)
    rejected_ids: list[int] = Field(default_factory=list)
    reason: str = ""
    created_at: str = ""
    updated_at: str = ""


class AspectPreference(BaseModel):
    aspect: AspectKey
    label: str
    polarity: AspectPolarity
    weight: float = Field(0.5, ge=0.0, le=1.0)
    evidence_count: int = Field(0, ge=0)
    sample: str = ""
    source: MemSource = "derived_from_feedback"
    confidence: float = Field(0.5, ge=0.0, le=1.0)


class UserAspectProfile(BaseModel):
    username: str
    subject_type: str = "anime"
    likes: list[AspectPreference] = Field(default_factory=list)
    dislikes: list[AspectPreference] = Field(default_factory=list)
    sample_count: int = 0
    extraction_source: Literal["llm", "fallback", "none"] = "none"
    updated_at: str = ""


class UserMemory(BaseModel):
    username: str
    likes: list[MemoryItem] = Field(default_factory=list)
    dislikes: list[MemoryItem] = Field(default_factory=list)
    spoiler_default: SpoilerDefault = "none"
    progress: dict[str, ProgressItem] = Field(default_factory=dict)
    feedback: list[FeedbackItem] = Field(default_factory=list)
    affinity_cache: dict[str, dict] = Field(default_factory=dict)
    profile_snapshot: dict = Field(default_factory=dict)
    aspect_profiles: dict[str, UserAspectProfile] = Field(default_factory=dict)
    pending_write_actions: list[PendingWriteAction] = Field(default_factory=list)
    decision_log: list[DecisionLogItem] = Field(default_factory=list)
    watch_plan: list[WatchPlanItem] = Field(default_factory=list)
    recommendation_lists: list[RecommendationListItem] = Field(default_factory=list)
    updated_at: str = ""


class MemorySummary(BaseModel):
    username: str
    likes: list[MemoryItem] = Field(default_factory=list)
    dislikes: list[MemoryItem] = Field(default_factory=list)
    spoiler_default: SpoilerDefault = "none"
    progress: dict[str, ProgressItem] = Field(default_factory=dict)
    recent_feedback: list[FeedbackItem] = Field(default_factory=list)
    profile_snapshot: dict = Field(default_factory=dict)
    aspect_profiles: dict[str, UserAspectProfile] = Field(default_factory=dict)
    pending_write_actions: list[PendingWriteAction] = Field(default_factory=list)
    recent_decisions: list[DecisionLogItem] = Field(default_factory=list)
    watch_plan: list[WatchPlanItem] = Field(default_factory=list)
    recommendation_lists: list[RecommendationListItem] = Field(default_factory=list)
    updated_at: str = ""


def memory_summary(mem: UserMemory, feedback_limit: int = 8) -> MemorySummary:
    recent_feedback = mem.feedback[-feedback_limit:] if feedback_limit > 0 else []
    pending = [x for x in mem.pending_write_actions if x.status == "pending"][-6:]
    return MemorySummary(
        username=mem.username,
        likes=mem.likes[:12],
        dislikes=mem.dislikes[:12],
        spoiler_default=mem.spoiler_default,
        progress=dict(list(mem.progress.items())[:20]),
        recent_feedback=recent_feedback,
        profile_snapshot=mem.profile_snapshot,
        aspect_profiles=dict(list(mem.aspect_profiles.items())[:8]),
        pending_write_actions=pending,
        recent_decisions=mem.decision_log[-10:],
        watch_plan=mem.watch_plan[-20:],
        recommendation_lists=mem.recommendation_lists[-6:],
        updated_at=mem.updated_at,
    )
