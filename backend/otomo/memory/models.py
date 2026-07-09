"""Typed long-term memory models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemSource = Literal["explicit_user", "bangumi_profile", "derived_from_feedback"]
SpoilerDefault = Literal["none", "mild", "full"]
FeedbackSignal = Literal["like", "dislike", "more", "less"]
VisualFeedbackSignal = Literal["correct", "wrong", "ambiguous"]
WriteActionStatus = Literal["pending", "executed", "cancelled", "failed", "undone"]
WriteOperation = Literal["set_collection", "set_episode_collection", "push_downloader"]
DecisionKind = Literal["accept", "reject", "defer", "write", "undo", "plan", "note"]
PlanStatus = Literal["wishlist", "watching", "backlog", "on_hold", "revive", "completed", "rejected"]
InboxKind = Literal["weekly_digest", "daily_airing", "system"]
WeeklyChannel = Literal["inbox", "webhook", "email"]
WeeklyWebhookFormat = Literal["generic", "serverchan", "telegram", "discord", "feishu"]
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


class VisualFeedbackItem(BaseModel):
    id: str
    image_uri: str = ""
    tool_name: str = "route_image_source"
    predicted_subject_id: int | None = None
    predicted_subject_name: str = ""
    predicted_title: str = ""
    source: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    signal: VisualFeedbackSignal
    corrected_subject_id: int | None = None
    corrected_subject_name: str = ""
    note: str = ""
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
    rss_url: str = ""
    subgroup: str = ""
    last_seen_pub_date: str = ""
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


class WeeklyDigestSubscription(BaseModel):
    enabled: bool = False
    weekday: int = Field(0, ge=0, le=6, description="0=Monday")
    hour: int = Field(9, ge=0, le=23)
    timezone: str = "Asia/Shanghai"
    push_grading: Literal["brief", "normal", "detailed"] = "normal"
    limit: int = Field(8, ge=3, le=20)
    include_on_hold: bool = True
    channels: list[WeeklyChannel] = Field(default_factory=lambda: ["inbox"])
    email: str = ""
    webhook_url: str = ""
    webhook_format: WeeklyWebhookFormat = "generic"
    web_push_endpoint: str = ""
    web_push_p256dh: str = ""
    web_push_auth: str = ""
    last_delivery: list[dict] = Field(default_factory=list)
    last_run_key: str = ""
    updated_at: str = ""


class InboxItem(BaseModel):
    id: str
    kind: InboxKind = "weekly_digest"
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    unread: bool = True
    created_at: str = ""


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
    weekly_digest_subscription: WeeklyDigestSubscription = Field(default_factory=WeeklyDigestSubscription)
    inbox: list[InboxItem] = Field(default_factory=list)
    visual_feedback: list[VisualFeedbackItem] = Field(default_factory=list)
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
    weekly_digest_subscription: WeeklyDigestSubscription = Field(default_factory=WeeklyDigestSubscription)
    inbox: list[InboxItem] = Field(default_factory=list)
    recent_visual_feedback: list[VisualFeedbackItem] = Field(default_factory=list)
    updated_at: str = ""


def memory_summary(mem: UserMemory, feedback_limit: int = 8) -> MemorySummary:
    recent_feedback = mem.feedback[-feedback_limit:] if feedback_limit > 0 else []
    recent_visual_feedback = mem.visual_feedback[-feedback_limit:] if feedback_limit > 0 else []
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
        weekly_digest_subscription=mem.weekly_digest_subscription,
        inbox=mem.inbox[-8:],
        recent_visual_feedback=recent_visual_feedback,
        updated_at=mem.updated_at,
    )
