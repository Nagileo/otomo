"""Confirmed Bangumi write-back and Otomo planning tools.

The agent can prepare write actions, but execution tools are marked is_write and
are excluded from model-visible tool schemas. The frontend must call them after a
human confirmation.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from ...agent.contracts import Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import (
    DecisionKind,
    DecisionLogItem,
    MemorySummary,
    PendingWriteAction,
    PlanStatus,
    RecommendationListItem,
    UserMemory,
    WatchPlanItem,
    memory_summary,
)
from ..bangumi.client import BangumiClient
from ..release.qbittorrent import DownloaderPushRequest, push_to_qbittorrent

COLLECTION_TYPE_LABELS = {
    1: "想看/想读/想听/想玩",
    2: "看过/读过/听过/玩过",
    3: "在看/在读/在听/在玩",
    4: "搁置",
    5: "抛弃",
}
EPISODE_TYPE_LABELS = {1: "想看", 2: "看过", 3: "抛弃"}


class MemoryResult(BaseModel):
    username: str
    memory: MemorySummary
    message: str = ""


class PrepareBangumiWriteArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则使用当前 token 账号")
    operation: Literal["set_collection", "set_episode_collection", "mark_episodes_watched"] = Field(
        "set_collection", description="写回类型：条目收藏 / 单集进度 / 看到第N集批量补标"
    )
    up_to_episode: int | None = Field(
        None, ge=1, description="mark_episodes_watched 用：把本篇第 1..N 集中未看的全部标记为看过"
    )
    subject_id: int | None = Field(None, description="Bangumi subject_id")
    subject_name: str = Field("", description="作品名，用于确认弹窗；可由 subject_id 自动补齐")
    collection_type: int | None = Field(None, ge=1, le=5, description="1想看/2看过/3在看/4搁置/5抛弃")
    rate: int | None = Field(None, ge=0, le=10, description="评分；0 表示删除评分")
    comment: str | None = Field(None, description="收藏短评")
    tags: list[str] | None = Field(None, description="收藏标签；传 [] 会清空 Bangumi 标签")
    private: bool | None = Field(None, description="是否仅自己可见")
    ep_status: int | None = Field(None, ge=0, description="书籍条目进度；动画进度优先用单集接口")
    vol_status: int | None = Field(None, ge=0, description="书籍卷数进度")
    episode_id: int | None = Field(None, description="单集 episode_id")
    episode_collection_type: int | None = Field(None, ge=1, le=3, description="单集状态：1想看/2看过/3抛弃")
    reason: str = Field("", description="为什么准备这个动作，会写入决策日志/确认说明")


class BangumiWriteActionResult(BaseModel):
    username: str
    action: PendingWriteAction
    requires_confirmation: bool = True
    warning: str
    memory: MemorySummary


class ConfirmBangumiWriteArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则使用当前 token 账号")
    action_id: str
    confirmed: bool = Field(False, description="用户已明确确认（前端按钮或对话中明确说确认/直接执行）时传 true")


class CancelBangumiWriteArgs(BaseModel):
    username: str | None = None
    action_id: str
    reason: str = ""


class UndoBangumiWriteArgs(BaseModel):
    username: str | None = None
    action_id: str | None = Field(None, description="不传则撤销最近一次可撤销写操作")
    confirmed: bool = Field(False, description="前端确认后必须传 true")


class ExecuteBangumiWriteResult(BaseModel):
    username: str
    action: PendingWriteAction
    decision: DecisionLogItem
    memory: MemorySummary
    message: str


class UpsertWatchPlanArgs(BaseModel):
    username: str | None = None
    subject_id: int
    name: str
    subject_type: str = "anime"
    status: PlanStatus = "backlog"
    priority: int = Field(3, ge=1, le=5)
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    rss_url: str = Field("", description="可选：该作品/字幕组的 RSS 订阅地址，用于每日放送提醒")
    subgroup: str = Field("", description="可选：订阅字幕组名")
    last_seen_pub_date: str = Field("", description="可选：RSS 已处理到的最新 pubDate/ISO 时间")
    source: str = "agent"


class ListWatchPlanArgs(BaseModel):
    username: str | None = None
    status: PlanStatus | None = None
    limit: int = Field(20, ge=1, le=100)


class WatchPlanResult(BaseModel):
    username: str
    watch_plan: list[WatchPlanItem]
    memory: MemorySummary
    message: str = ""


class RecordDecisionArgs(BaseModel):
    username: str | None = None
    kind: DecisionKind
    subject_id: int | None = None
    subject_name: str = ""
    reason: str = ""
    action_id: str | None = None
    source: str = "agent"


class DecisionLogResult(BaseModel):
    username: str
    decision: DecisionLogItem
    memory: MemorySummary


class SaveRecommendationListArgs(BaseModel):
    username: str | None = None
    title: str
    subject_type: str = "anime"
    items: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""


class RecommendationListResult(BaseModel):
    username: str
    list: RecommendationListItem
    memory: MemorySummary


_NO_USER_ERR = "未提供 username 且无法获取当前账号（需要有效 BANGUMI_TOKEN）。"


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return me.get("username") or str(me.get("id")) or None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _summ(mem: UserMemory) -> MemorySummary:
    return memory_summary(mem)


def _append_decision(mem: UserMemory, item: DecisionLogItem) -> DecisionLogItem:
    mem.decision_log.append(item)
    mem.decision_log = mem.decision_log[-500:]
    return item


def _compact_collection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "subject_id", "subject_type", "rate", "type", "comment", "tags",
        "ep_status", "vol_status", "private", "updated_at",
    )
    return {k: value.get(k) for k in keys if k in value}


def _compact_episode(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    episode = value.get("episode") if isinstance(value.get("episode"), dict) else {}
    return {
        "episode_id": episode.get("id"),
        "episode_sort": episode.get("sort"),
        "episode_name": episode.get("name") or episode.get("name_cn"),
        "type": value.get("type"),
        "updated_at": value.get("updated_at"),
    }


async def _current_collection(client: BangumiClient, username: str, subject_id: int) -> dict[str, Any] | None:
    try:
        return _compact_collection(await client.get_user_collection(username, subject_id))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def _current_episode(client: BangumiClient, episode_id: int) -> dict[str, Any] | None:
    try:
        return _compact_episode(await client.get_my_episode_collection(episode_id))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


def _collection_payload(args: PrepareBangumiWriteArgs) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.collection_type is not None:
        payload["type"] = args.collection_type
    if args.rate is not None:
        payload["rate"] = args.rate
    if args.comment is not None:
        payload["comment"] = args.comment
    if args.tags is not None:
        payload["tags"] = [x.strip() for x in args.tags if x.strip()]
    if args.private is not None:
        payload["private"] = args.private
    if args.ep_status is not None:
        payload["ep_status"] = args.ep_status
    if args.vol_status is not None:
        payload["vol_status"] = args.vol_status
    return payload


def _restore_collection_payload(before: dict[str, Any]) -> dict[str, Any]:
    keys = ("type", "rate", "comment", "tags", "private", "ep_status", "vol_status")
    return {k: before[k] for k in keys if k in before and before[k] is not None}


async def _subject_name(client: BangumiClient, subject_id: int | None, fallback: str) -> str:
    if fallback.strip() or subject_id is None:
        return fallback.strip()
    try:
        subject = await client.get_subject(subject_id)
    except Exception:  # noqa: BLE001
        return f"subject {subject_id}"
    return subject.get("name_cn") or subject.get("name") or f"subject {subject_id}"


async def _resolve_unwatched_episodes(
    client: BangumiClient, subject_id: int, up_to: int,
) -> tuple[list[int], list[float], int]:
    """本篇 sort≤N 中还没标"看过"的集。返回 (episode_ids, sorts, 当前已看到第几集)。

    需要条目已收藏（Bangumi 分集接口的前置条件）；未收藏时该接口 404，由调用方给出提示。
    """
    my = await client.get_my_subject_episodes(subject_id, episode_type=0, limit=200)
    rows = list(my.get("data") or [])
    watched_max = 0
    targets: list[tuple[int, float]] = []
    for row in rows:
        ep = row.get("episode") or {}
        sort = float(ep.get("sort") or 0)
        if int(row.get("type") or 0) == 2:
            watched_max = max(watched_max, int(sort))
            continue
        if 0 < sort <= up_to and ep.get("id"):
            targets.append((int(ep["id"]), sort))
    targets.sort(key=lambda x: x[1])
    return [t[0] for t in targets], [t[1] for t in targets], watched_max


def _summary_for_action(args: PrepareBangumiWriteArgs, name: str, payload: dict[str, Any]) -> str:
    if args.operation == "mark_episodes_watched":
        n = len(payload.get("episode_ids") or [])
        return f"将《{name or args.subject_name or '未知作品'}》看到第 {payload.get('up_to')} 集（补标 {n} 集为看过）"
    if args.operation == "set_episode_collection":
        label = EPISODE_TYPE_LABELS.get(args.episode_collection_type or 0, str(args.episode_collection_type))
        return f"将《{name or args.subject_name or '未知作品'}》 episode {args.episode_id} 标记为 {label}"
    parts = []
    if payload.get("type") is not None:
        parts.append(COLLECTION_TYPE_LABELS.get(payload["type"], f"type={payload['type']}"))
    if payload.get("rate") is not None:
        parts.append(f"评分 {payload['rate']}")
    if payload.get("comment"):
        parts.append("更新短评")
    if payload.get("tags") is not None:
        parts.append("更新标签")
    if payload.get("private") is not None:
        parts.append("设为私有" if payload["private"] else "设为公开")
    if payload.get("ep_status") is not None:
        parts.append(f"进度 {payload['ep_status']}")
    if payload.get("vol_status") is not None:
        parts.append(f"卷数 {payload['vol_status']}")
    return f"将《{name or args.subject_name or '未知作品'}》" + ("，".join(parts) if parts else "写回收藏")


class PrepareBangumiWriteActionTool(Tool):
    name = "prepare_bangumi_write_action"
    description = (
        "准备一个需要用户前端二次确认的 Bangumi 写回动作：加入想看/在看/看过/搁置/抛弃、评分、"
        "短评、标签、书籍进度或单集进度。只生成待确认动作，不会实际写 Bangumi。"
    )
    args_model = PrepareBangumiWriteArgs
    result_model = BangumiWriteActionResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: PrepareBangumiWriteArgs) -> ToolResult[BangumiWriteActionResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        if args.operation == "set_collection":
            if args.subject_id is None:
                return ToolResult(ok=False, error="set_collection 需要 subject_id")
            payload = _collection_payload(args)
            if not payload:
                return ToolResult(ok=False, error="没有可写回字段")
            before = await _current_collection(self.client, username, args.subject_id)
            name = await _subject_name(self.client, args.subject_id, args.subject_name)
            subject_id = args.subject_id
            episode_id = None
        elif args.operation == "mark_episodes_watched":
            if args.subject_id is None or args.up_to_episode is None:
                return ToolResult(ok=False, error="mark_episodes_watched 需要 subject_id 和 up_to_episode")
            try:
                ep_ids, sorts, watched_max = await _resolve_unwatched_episodes(
                    self.client, args.subject_id, args.up_to_episode
                )
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    error=f"读取分集进度失败（{type(e).__name__}）；批量打卡需要该条目已在收藏中，可先加入在看。",
                )
            name = await _subject_name(self.client, args.subject_id, args.subject_name)
            if not ep_ids:
                return ToolResult(
                    ok=False,
                    error=f"《{name}》第 1..{args.up_to_episode} 集都已是看过状态（当前看到第 {watched_max} 集），无需补标。",
                )
            payload = {"episode_ids": ep_ids, "type": 2, "up_to": args.up_to_episode, "sorts": sorts}
            before = {"unwatched_episode_ids": ep_ids, "prev_watched_max": watched_max}
            subject_id = args.subject_id
            episode_id = None
        else:
            if args.episode_id is None or args.episode_collection_type is None:
                return ToolResult(ok=False, error="set_episode_collection 需要 episode_id 和 episode_collection_type")
            payload = {"type": args.episode_collection_type}
            before = await _current_episode(self.client, args.episode_id)
            name = await _subject_name(self.client, args.subject_id, args.subject_name)
            subject_id = args.subject_id
            episode_id = args.episode_id

        mem = self.ltm.load_user(username)
        # 去重：同一作品同一操作同一 payload 已有 pending 时直接复用——
        # 用户说"你没加入/再加一下"时 LLM 容易重复 prepare，堆出多个待确认、确认后重复写回。
        dup = next(
            (x for x in mem.pending_write_actions
             if x.status == "pending" and x.operation == args.operation
             and x.subject_id == subject_id and x.episode_id == episode_id and x.payload == payload),
            None,
        )
        if dup is not None:
            return ToolResult(
                ok=True,
                data=BangumiWriteActionResult(
                    username=username,
                    action=dup,
                    warning="已存在相同的待确认动作（未重复创建）；用户确认后用它的 action_id 执行即可。",
                    memory=_summ(mem),
                ),
            )
        action = PendingWriteAction(
            id=_new_id("wr"),
            operation=args.operation,
            summary=_summary_for_action(args, name, payload),
            subject_id=subject_id,
            subject_name=name,
            episode_id=episode_id,
            payload=payload,
            before=before,
            status="pending",
            created_at=now_iso(),
            source="agent",
        )
        mem.pending_write_actions.append(action)
        mem.pending_write_actions = mem.pending_write_actions[-80:]
        self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=BangumiWriteActionResult(
                username=username,
                action=action,
                warning="这是真实 Bangumi 写操作；必须由用户在前端确认后才会执行，可撤销只覆盖已有旧值的字段。",
                memory=_summ(mem),
            ),
        )


class ExecuteBangumiWriteActionTool(Tool):
    name = "execute_bangumi_write_action"
    description = ("执行已准备的 Bangumi 写回动作。仅当用户在当前对话中明确确认（说\"确认/直接加/写回吧\"等）或前端按钮触发时调用；未经用户明确确认绝不调用。多个待确认动作应逐个全部执行。")
    args_model = ConfirmBangumiWriteArgs
    result_model = ExecuteBangumiWriteResult
    is_write = True

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: ConfirmBangumiWriteArgs) -> ToolResult[ExecuteBangumiWriteResult]:
        if not args.confirmed:
            return ToolResult(ok=False, error="写回需要 confirmed=true")
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        action = next((x for x in mem.pending_write_actions if x.id == args.action_id), None)
        if action is None:
            return ToolResult(ok=False, error=f"找不到待确认动作：{args.action_id}")
        if action.status != "pending":
            return ToolResult(ok=False, error=f"动作状态不是 pending：{action.status}")

        try:
            if action.operation == "push_downloader":
                result = await push_to_qbittorrent(
                    DownloaderPushRequest(
                        url=str(action.payload.get("url") or ""),
                        category=str(action.payload.get("category") or ""),
                        save_path=str(action.payload.get("save_path") or ""),
                        paused=bool(action.payload.get("paused") or False),
                    )
                )
                action.after = result
            elif action.operation == "set_collection":
                if action.subject_id is None:
                    return ToolResult(ok=False, error="动作缺少 subject_id")
                await self.client.set_my_collection(action.subject_id, action.payload)
                action.after = await _current_collection(self.client, username, action.subject_id)
            elif action.operation == "mark_episodes_watched":
                if action.subject_id is None or not action.payload.get("episode_ids"):
                    return ToolResult(ok=False, error="动作缺少 subject_id 或分集列表")
                await self.client.patch_my_subject_episodes(
                    action.subject_id,
                    [int(x) for x in action.payload["episode_ids"]],
                    int(action.payload.get("type") or 2),
                )
                action.after = {"watched_up_to": action.payload.get("up_to"), "marked": len(action.payload["episode_ids"])}
            else:
                if action.episode_id is None:
                    return ToolResult(ok=False, error="动作缺少 episode_id")
                await self.client.set_my_episode_collection(action.episode_id, int(action.payload["type"]))
                action.after = await _current_episode(self.client, action.episode_id)
            action.status = "executed"
            action.executed_at = now_iso()
            decision = _append_decision(
                mem,
                DecisionLogItem(
                    id=_new_id("dec"),
                    kind="write",
                    subject_id=action.subject_id,
                    subject_name=action.subject_name,
                    operation=action.operation,
                    reason=action.summary,
                    action_id=action.id,
                    before=action.before,
                    after=action.after,
                    confirmed=True,
                    source="bangumi_write",
                    # qB 推送仍用 write 决策类型；source 字段区分边界。
                    ts=now_iso(),
                ),
            )
            if action.operation == "push_downloader":
                decision.source = "downloader"
            self.ltm.save_user(mem)
            return ToolResult(
                ok=True,
                data=ExecuteBangumiWriteResult(
                    username=username,
                    action=action,
                    decision=decision,
                    memory=_summ(mem),
                    message=(
                        "已推送到 qBittorrent，并记录到 decision_log。"
                        if action.operation == "push_downloader"
                        else "已写回 Bangumi，并记录到 decision_log。"
                    ),
                ),
            )
        except Exception as e:  # noqa: BLE001
            action.status = "failed"
            action.error = f"{type(e).__name__}: {e}"
            self.ltm.save_user(mem)
            return ToolResult(ok=False, error=action.error)


class CancelBangumiWriteActionTool(Tool):
    name = "cancel_bangumi_write_action"
    description = "取消一个尚未执行的 Bangumi 写回动作。"
    args_model = CancelBangumiWriteArgs
    result_model = MemoryResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: CancelBangumiWriteArgs) -> ToolResult[MemoryResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        action = next((x for x in mem.pending_write_actions if x.id == args.action_id), None)
        if action is None:
            return ToolResult(ok=False, error=f"找不到待确认动作：{args.action_id}")
        if action.status != "pending":
            return ToolResult(ok=False, error=f"只能取消 pending 动作，当前为 {action.status}")
        action.status = "cancelled"
        action.error = args.reason.strip()
        _append_decision(
            mem,
            DecisionLogItem(
                id=_new_id("dec"),
                kind="reject",
                subject_id=action.subject_id,
                subject_name=action.subject_name,
                operation=action.operation,
                reason=args.reason or "用户取消写回",
                action_id=action.id,
                confirmed=True,
                source="bangumi_write",
                ts=now_iso(),
            ),
        )
        self.ltm.save_user(mem)
        return ToolResult(ok=True, data=MemoryResult(username=username, memory=_summ(mem), message="已取消写回动作。"))


class UndoBangumiWriteActionTool(Tool):
    name = "undo_bangumi_write_action"
    description = "撤销最近一次已执行且有旧值的 Bangumi 写回动作。只应由后端确认接口调用。"
    args_model = UndoBangumiWriteArgs
    result_model = ExecuteBangumiWriteResult
    is_write = True

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: UndoBangumiWriteArgs) -> ToolResult[ExecuteBangumiWriteResult]:
        if not args.confirmed:
            return ToolResult(ok=False, error="撤销写回需要 confirmed=true")
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        candidates = [x for x in mem.pending_write_actions if x.status == "executed"]
        action = next((x for x in candidates if x.id == args.action_id), None) if args.action_id else (candidates[-1] if candidates else None)
        if action is None:
            return ToolResult(ok=False, error="没有可撤销的已执行动作")
        if action.operation == "push_downloader":
            return ToolResult(ok=False, error="下载器推送不支持自动撤销；请在 qBittorrent WebUI 中手动移除。")
        if not action.before:
            return ToolResult(ok=False, error="该动作没有旧值快照，无法安全撤销；请在 Bangumi 手动检查。")
        try:
            if action.operation == "set_collection":
                if action.subject_id is None:
                    return ToolResult(ok=False, error="动作缺少 subject_id")
                await self.client.set_my_collection(action.subject_id, _restore_collection_payload(action.before))
                after = await _current_collection(self.client, username, action.subject_id)
            elif action.operation == "mark_episodes_watched":
                ids = [int(x) for x in (action.before.get("unwatched_episode_ids") or [])]
                if action.subject_id is None or not ids:
                    return ToolResult(ok=False, error="动作缺少批量补标的旧值快照")
                # 这些集在执行前都是"未看"，恢复即批量清回未收藏状态（type=0）
                await self.client.patch_my_subject_episodes(action.subject_id, ids, 0)
                after = {"restored_unwatched": len(ids)}
            else:
                if action.episode_id is None or action.before.get("type") is None:
                    return ToolResult(ok=False, error="动作缺少 episode_id 或旧单集状态")
                await self.client.set_my_episode_collection(action.episode_id, int(action.before["type"]))
                after = await _current_episode(self.client, action.episode_id)
            action.status = "undone"
            decision = _append_decision(
                mem,
                DecisionLogItem(
                    id=_new_id("dec"),
                    kind="undo",
                    subject_id=action.subject_id,
                    subject_name=action.subject_name,
                    operation=action.operation,
                    reason=f"撤销：{action.summary}",
                    action_id=action.id,
                    before=action.after,
                    after=after,
                    confirmed=True,
                    source="bangumi_write",
                    ts=now_iso(),
                ),
            )
            self.ltm.save_user(mem)
            return ToolResult(
                ok=True,
                data=ExecuteBangumiWriteResult(
                    username=username,
                    action=action,
                    decision=decision,
                    memory=_summ(mem),
                    message="已按旧值快照撤销写回。",
                ),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")


class UpsertWatchPlanTool(Tool):
    name = "upsert_watch_plan_item"
    description = "把作品加入/更新 Otomo 本地计划板：待看、在看、补番、搁置复活、完成或拒绝。不会写 Bangumi。"
    args_model = UpsertWatchPlanArgs
    result_model = WatchPlanResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: UpsertWatchPlanArgs) -> ToolResult[WatchPlanResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        now = now_iso()
        current = next((x for x in mem.watch_plan if x.subject_id == args.subject_id), None)
        if current:
            current.name = args.name.strip() or current.name
            current.subject_type = args.subject_type
            current.status = args.status
            current.priority = args.priority
            current.reason = args.reason.strip()
            current.tags = [x.strip() for x in args.tags if x.strip()]
            if args.rss_url.strip():
                current.rss_url = args.rss_url.strip()
            if args.subgroup.strip():
                current.subgroup = args.subgroup.strip()
            if args.last_seen_pub_date.strip():
                current.last_seen_pub_date = args.last_seen_pub_date.strip()
            current.source = args.source
            current.updated_at = now
            message = "已更新计划板条目。"
        else:
            current = WatchPlanItem(
                id=_new_id("plan"),
                subject_id=args.subject_id,
                name=args.name.strip() or f"subject {args.subject_id}",
                subject_type=args.subject_type,
                status=args.status,
                priority=args.priority,
                reason=args.reason.strip(),
                tags=[x.strip() for x in args.tags if x.strip()],
                rss_url=args.rss_url.strip(),
                subgroup=args.subgroup.strip(),
                last_seen_pub_date=args.last_seen_pub_date.strip(),
                source=args.source,
                created_at=now,
                updated_at=now,
            )
            mem.watch_plan.append(current)
            message = "已加入计划板。"
        mem.watch_plan = sorted(mem.watch_plan, key=lambda x: (x.status, x.priority, x.updated_at), reverse=True)[:300]
        _append_decision(
            mem,
            DecisionLogItem(
                id=_new_id("dec"),
                kind="plan",
                subject_id=current.subject_id,
                subject_name=current.name,
                operation=f"watch_plan:{current.status}",
                reason=current.reason,
                confirmed=True,
                source=current.source,
                ts=now,
            ),
        )
        self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=WatchPlanResult(username=username, watch_plan=mem.watch_plan, memory=_summ(mem), message=message),
        )


class ListWatchPlanTool(Tool):
    name = "list_watch_plan"
    description = "读取 Otomo 本地计划板，用于继续追番、补番、搁置复活、backlog 整理和决策复盘。"
    args_model = ListWatchPlanArgs
    result_model = WatchPlanResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: ListWatchPlanArgs) -> ToolResult[WatchPlanResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        items = [x for x in mem.watch_plan if args.status is None or x.status == args.status]
        items = sorted(items, key=lambda x: (x.priority, x.updated_at), reverse=True)[: args.limit]
        return ToolResult(
            ok=True,
            data=WatchPlanResult(username=username, watch_plan=items, memory=_summ(mem), message=f"{len(items)} 个计划条目"),
        )


class RecordDecisionTool(Tool):
    name = "record_decision_log"
    description = "记录用户对作品/推荐的接受、拒绝、延期、备注等决策，作为长期偏好和后训练信号。"
    args_model = RecordDecisionArgs
    result_model = DecisionLogResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: RecordDecisionArgs) -> ToolResult[DecisionLogResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        decision = _append_decision(
            mem,
            DecisionLogItem(
                id=_new_id("dec"),
                kind=args.kind,
                subject_id=args.subject_id,
                subject_name=args.subject_name.strip(),
                operation=args.kind,
                reason=args.reason.strip(),
                action_id=args.action_id,
                confirmed=True,
                source=args.source,
                ts=now_iso(),
            ),
        )
        self.ltm.save_user(mem)
        return ToolResult(ok=True, data=DecisionLogResult(username=username, decision=decision, memory=_summ(mem)))


class SaveRecommendationListTool(Tool):
    name = "save_recommendation_list"
    description = "保存一次推荐候选列表到 Otomo 本地 recommendation_lists，便于后续接受/拒绝/计划板追踪。"
    args_model = SaveRecommendationListArgs
    result_model = RecommendationListResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: SaveRecommendationListArgs) -> ToolResult[RecommendationListResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        now = now_iso()
        rec_list = RecommendationListItem(
            id=_new_id("reclist"),
            title=args.title.strip() or "Otomo 推荐列表",
            subject_type=args.subject_type,
            items=args.items[:50],
            reason=args.reason.strip(),
            created_at=now,
            updated_at=now,
        )
        mem.recommendation_lists.append(rec_list)
        mem.recommendation_lists = mem.recommendation_lists[-80:]
        self.ltm.save_user(mem)
        return ToolResult(ok=True, data=RecommendationListResult(username=username, list=rec_list, memory=_summ(mem)))


def build_writeback_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [
        PrepareBangumiWriteActionTool(client, ltm),
        ExecuteBangumiWriteActionTool(client, ltm),
        CancelBangumiWriteActionTool(client, ltm),
        UndoBangumiWriteActionTool(client, ltm),
        UpsertWatchPlanTool(client, ltm),
        ListWatchPlanTool(client, ltm),
        RecordDecisionTool(client, ltm),
        SaveRecommendationListTool(client, ltm),
        GetEpisodeProgressTool(client),
    ]


class EpisodeProgressArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi subject_id（需要该条目已在收藏中）")


class EpisodeProgressRow(BaseModel):
    episode_id: int
    sort: float
    name: str = ""
    air_date: str = ""
    status: str = "未看"      # 未看/想看/看过/抛弃


class EpisodeProgressResult(BaseModel):
    subject_id: int
    subject_name: str = ""
    total_main: int = 0
    watched: int = 0
    watched_up_to: int = 0      # 连续看到第几集（第一个未看集之前）
    next_episode: float | None = None
    episodes: list[EpisodeProgressRow] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


_EP_STATUS = {0: "未看", 1: "想看", 2: "看过", 3: "抛弃"}


class GetEpisodeProgressTool(Tool):
    name = "get_my_episode_progress"
    description = (
        "查询我在某部作品的分集观看进度：每集状态、看到第几集、下一集是第几集。"
        "用户问'我看到第几集了/进度/下一集该看哪集'时用；打卡进度前也可先查。需要该条目已收藏。"
    )
    args_model = EpisodeProgressArgs
    result_model = EpisodeProgressResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: EpisodeProgressArgs) -> ToolResult[EpisodeProgressResult]:
        name = await _subject_name(self.client, args.subject_id, "")
        try:
            my = await self.client.get_my_subject_episodes(args.subject_id, episode_type=0, limit=200)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"读取分集进度失败（{type(e).__name__}）；该接口要求条目已在收藏中，可先加入在看。",
            )
        rows: list[EpisodeProgressRow] = []
        for row in list(my.get("data") or []):
            ep = row.get("episode") or {}
            if not ep.get("id"):
                continue
            rows.append(
                EpisodeProgressRow(
                    episode_id=int(ep["id"]),
                    sort=float(ep.get("sort") or 0),
                    name=str(ep.get("name_cn") or ep.get("name") or ""),
                    air_date=str(ep.get("airdate") or ""),
                    status=_EP_STATUS.get(int(row.get("type") or 0), "未看"),
                )
            )
        rows.sort(key=lambda x: x.sort)
        watched = sum(1 for r in rows if r.status == "看过")
        watched_up_to = 0
        next_ep: float | None = None
        for r in rows:
            if r.status == "看过":
                watched_up_to = int(r.sort)
            else:
                next_ep = r.sort
                break
        return ToolResult(
            ok=True,
            data=EpisodeProgressResult(
                subject_id=args.subject_id,
                subject_name=name,
                total_main=len(rows),
                watched=watched,
                watched_up_to=watched_up_to,
                next_episode=next_ep,
                episodes=rows,
                caveats=["进度取自 Bangumi 分集收藏（本篇）；说'看到第 N 集了'可一句话批量补标。"],
            ),
        )
