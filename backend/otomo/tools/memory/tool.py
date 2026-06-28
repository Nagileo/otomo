"""Long-term memory tools.

Memory stores only ACGN recommendation/evaluation preferences and explicit
feedback. It never writes to Bangumi; it only persists local JSON under cache/ltm.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import Action, consolidate_preference, now_iso, remove_item
from ...memory.models import (
    FeedbackItem,
    FeedbackSignal,
    MemSource,
    MemorySummary,
    ProgressItem,
    UserMemory,
    memory_summary,
)
from ..bangumi.client import BangumiClient


class MemoryBaseArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则使用当前 token 账号")


class GetUserMemoryArgs(MemoryBaseArgs):
    feedback_limit: int = Field(8, ge=0, le=30, description="返回最近多少条推荐反馈")


class GetUserMemoryResult(BaseModel):
    memory: MemorySummary
    caveats: list[str] = Field(default_factory=list)


class RememberPreferenceArgs(MemoryBaseArgs):
    kind: Literal["like", "dislike", "spoiler", "progress"] = Field(
        ..., description="写入类型：like/dislike 偏好，spoiler 默认剧透等级，progress 作品进度"
    )
    value: str | None = Field(None, description="偏好/避雷词，或 spoiler 的 none/mild/full")
    subject: str | None = Field(None, description="progress 用作品名，如 摇曳露营△")
    episode: int | None = Field(None, ge=0, description="progress 用看到第几集")
    source: MemSource = "explicit_user"
    confidence: float = Field(0.9, ge=0.0, le=1.0)


class MemoryWriteResult(BaseModel):
    username: str
    action: Action
    changed: bool
    message: str
    memory: MemorySummary


class ForgetMemoryArgs(MemoryBaseArgs):
    kind: Literal["like", "dislike", "spoiler", "progress", "feedback", "all"] = Field(
        ..., description="删除哪类记忆；all 会清空该用户全部 Otomo 本地记忆"
    )
    value: str | None = Field(None, description="like/dislike 要删除的具体词")
    subject: str | None = Field(None, description="progress 要删除的作品名；不传则清空全部进度")


class FeedbackArgs(MemoryBaseArgs):
    subject_id: int | None = Field(None, description="可选 Bangumi subject_id")
    name: str = Field("", description="作品名或推荐项名")
    signal: FeedbackSignal = Field(..., description="like/dislike/more/less")
    note: str = Field("", description="用户反馈原话或简短说明")
    source: MemSource = "explicit_user"
    confidence: float = Field(0.8, ge=0.0, le=1.0)


class FeedbackResult(BaseModel):
    username: str
    action: Literal["ADD"]
    feedback: FeedbackItem
    memory: MemorySummary


_NO_USER_ERR = "未提供 username 且无法获取当前账号（需要有效 BANGUMI_TOKEN）；请改用 username 指定要操作记忆的用户。"


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return me.get("username") or str(me.get("id")) or None


def _summary(mem: UserMemory, feedback_limit: int = 8) -> MemorySummary:
    return memory_summary(mem, feedback_limit=feedback_limit)


class GetUserMemoryTool(Tool):
    name = "get_user_memory"
    description = (
        "读取 Otomo 本地长期记忆：用户显式偏好/避雷、默认剧透等级、作品进度和最近推荐反馈。"
        "推荐、评价、按我口味、别推某类、查看你记住了什么时使用。"
    )
    args_model = GetUserMemoryArgs
    result_model = GetUserMemoryResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: GetUserMemoryArgs) -> ToolResult[GetUserMemoryResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        return ToolResult(
            ok=True,
            data=GetUserMemoryResult(
                memory=_summary(mem, args.feedback_limit),
                caveats=[
                    "Memory 只代表用户在 Otomo 对话中显式表达或由反馈弱推断的 ACGN 偏好。",
                    "derived_from_feedback 是低置信推断，用户显式说法优先。",
                ],
            ),
        )


class RememberUserPreferenceTool(Tool):
    name = "remember_user_preference"
    description = (
        "写入长期记忆：喜欢/避雷词、默认剧透等级、作品观看进度。"
        "写入会走 consolidation，不会追加矛盾记录；只记 ACGN 推荐/评价相关内容。"
    )
    args_model = RememberPreferenceArgs
    result_model = MemoryWriteResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: RememberPreferenceArgs) -> ToolResult[MemoryWriteResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        action: Action = "NOOP"
        changed = False
        message = "没有可写入的记忆。"
        if args.kind in {"like", "dislike"}:
            if not args.value or not args.value.strip():
                return ToolResult(ok=False, error="like/dislike 需要 value")
            action, changed = consolidate_preference(
                mem,
                args.kind,
                args.value,
                source=args.source,
                confidence=args.confidence,
            )
            message = f"{args.kind}={args.value.strip()} consolidation: {action}"
        elif args.kind == "spoiler":
            if args.value not in {"none", "mild", "full"}:
                return ToolResult(ok=False, error="spoiler value 必须是 none/mild/full")
            old = mem.spoiler_default
            mem.spoiler_default = args.value  # type: ignore[assignment]
            changed = old != mem.spoiler_default
            action = "UPDATE" if changed else "NOOP"
            message = f"spoiler_default={mem.spoiler_default}"
        elif args.kind == "progress":
            if not args.subject or args.episode is None:
                return ToolResult(ok=False, error="progress 需要 subject 和 episode")
            current = mem.progress.get(args.subject)
            changed = (
                current is None
                or current.episode != args.episode
                or current.source != args.source
                or args.confidence > current.confidence
            )
            if changed:
                mem.progress[args.subject] = ProgressItem(
                    episode=args.episode,
                    source=args.source,
                    confidence=max(args.confidence, current.confidence if current else 0.0),
                    ts=now_iso(),
                )
            action = "UPDATE" if current and changed else ("ADD" if changed else "NOOP")
            message = f"{args.subject} progress={args.episode}"
        if changed:
            self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=MemoryWriteResult(
                username=username,
                action=action,
                changed=changed,
                message=message,
                memory=_summary(mem),
            ),
        )


class ForgetUserMemoryTool(Tool):
    name = "forget_user_memory"
    description = (
        "删除 Otomo 本地长期记忆。用于用户要求忘记某个偏好/避雷/进度/反馈/全部记忆时。"
        "这是隐私红线工具，必须尊重用户显式删除请求。"
    )
    args_model = ForgetMemoryArgs
    result_model = MemoryWriteResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: ForgetMemoryArgs) -> ToolResult[MemoryWriteResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        changed = False
        message = ""
        if args.kind == "all":
            mem = UserMemory(username=username)
            changed = True
            message = "已清空该用户 Otomo 本地长期记忆。"
        elif args.kind == "like":
            if not args.value:
                return ToolResult(ok=False, error="删除 like 需要 value")
            changed = remove_item(mem.likes, args.value)
            message = f"删除喜欢项：{args.value}"
        elif args.kind == "dislike":
            if not args.value:
                return ToolResult(ok=False, error="删除 dislike 需要 value")
            changed = remove_item(mem.dislikes, args.value)
            message = f"删除避雷项：{args.value}"
        elif args.kind == "spoiler":
            changed = mem.spoiler_default != "none"
            mem.spoiler_default = "none"
            message = "默认剧透等级已恢复 none。"
        elif args.kind == "progress":
            if args.subject:
                changed = mem.progress.pop(args.subject, None) is not None
                message = f"删除进度：{args.subject}"
            else:
                changed = bool(mem.progress)
                mem.progress.clear()
                message = "清空全部作品进度。"
        elif args.kind == "feedback":
            changed = bool(mem.feedback)
            mem.feedback.clear()
            message = "清空推荐反馈。"
        if changed:
            self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=MemoryWriteResult(
                username=username,
                action="DELETE" if changed else "NOOP",
                changed=changed,
                message=message,
                memory=_summary(mem),
            ),
        )


class RecordRecommendationFeedbackTool(Tool):
    name = "record_recommendation_feedback"
    description = (
        "记录用户对推荐结果的反馈：喜欢/不喜欢/想多看/想少看。"
        "v1 只存原始反馈，不自动派生 aspect 偏好；派生留给 Phase 6。"
    )
    args_model = FeedbackArgs
    result_model = FeedbackResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: FeedbackArgs) -> ToolResult[FeedbackResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        feedback = FeedbackItem(
            subject_id=args.subject_id,
            name=args.name.strip(),
            signal=args.signal,
            note=args.note.strip(),
            source=args.source,
            confidence=args.confidence,
            ts=now_iso(),
        )
        mem.feedback.append(feedback)
        mem.feedback = mem.feedback[-100:]
        self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=FeedbackResult(
                username=username,
                action="ADD",
                feedback=feedback,
                memory=_summary(mem),
            ),
        )


def build_memory_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [
        GetUserMemoryTool(client, ltm),
        RememberUserPreferenceTool(client, ltm),
        ForgetUserMemoryTool(client, ltm),
        RecordRecommendationFeedbackTool(client, ltm),
    ]
