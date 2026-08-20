"""User-aware anime franchise progress shared by recommendation and product surfaces.

Bangumi stores every season/movie/OVA as an independent subject.  This module
keeps that boundary intact, overlays collection state on the relation graph,
and only unlocks a sequel after every required predecessor is complete.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent.contracts import Citation, Tool, ToolResult
from ._concurrency import gather_limited
from .bangumi.client import SUBJECT_TYPE, BangumiClient

CollectionState = Literal[
    "watched", "watching", "wishlist", "on_hold", "dropped", "uncollected", "unknown"
]
SeriesRole = Literal["entry", "main", "side", "alternate"]
SeriesNecessity = Literal["required", "recommended", "optional", "skip"]

_STATE_BY_TYPE: dict[int, CollectionState] = {
    1: "wishlist",
    2: "watched",
    3: "watching",
    4: "on_hold",
    5: "dropped",
}
_STATE_LABEL: dict[CollectionState, str] = {
    "watched": "已看",
    "watching": "在看",
    "wishlist": "想看",
    "on_hold": "搁置",
    "dropped": "抛弃",
    "uncollected": "未收藏",
    "unknown": "状态未知",
}
_MAIN_RELATIONS = {"前传", "续集"}
_SIDE_RELATIONS = {"外传", "相同世界观", "不同世界观", "番外篇", "主线故事"}
_ALTERNATE_RELATIONS = {"不同演绎", "重制", "再编集"}
_OPTIONAL_WORDS = ("ova", "oad", "特典", "special", "sp", "番外", "外传")
_SKIP_WORDS = ("总集篇", "総集編", "recap", "summary")
_MAX_SERIES = 24
_MAX_PREDECESSOR_HOPS = 10


class SeriesRelationMemo:
    """Request-scoped single-flight cache for Bangumi relation graph reads.

    The client TTL cache prevents later duplicate requests, but concurrent
    season/recommendation audits can still start the same predecessor request
    before the first response has populated that cache.  Sharing the in-flight
    task removes that fan-out without making user collection state persistent.
    """

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self._tasks: dict[int, asyncio.Task[Any]] = {}

    async def get(self, subject_id: int) -> Any:
        sid = int(subject_id)
        task = self._tasks.get(sid)
        if task is None:
            task = asyncio.create_task(self.client.get_subject_relations(sid))
            self._tasks[sid] = task
        try:
            return await task
        except BaseException:
            if self._tasks.get(sid) is task:
                self._tasks.pop(sid, None)
            raise


class SeriesProgressArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi 动画 subject_id；优先使用")
    title: str = Field("", description="subject_id 为空时按标题搜索")
    username: str | None = Field(None, description="Bangumi 用户名；不传则尝试当前账号")
    max_members: int = Field(18, ge=4, le=_MAX_SERIES)


class SeriesProgressItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    date: str = ""
    image: str | None = None
    eps: int | None = None
    role: SeriesRole = "main"
    necessity: SeriesNecessity = "required"
    relation: str = ""
    collection_state: CollectionState = "unknown"
    collection_label: str = "状态未知"
    ep_status: int | None = None
    completed: bool = False
    completion_source: str = ""
    prerequisite_ids: list[int] = Field(default_factory=list)
    blocked_by: list[int] = Field(default_factory=list)
    prerequisites_satisfied: bool = True
    is_current: bool = False
    is_next: bool = False
    action: str = ""


class SeriesProgressResult(BaseModel):
    subject_id: int
    username: str | None = None
    personalized: bool = False
    collection_available: bool = False
    mainline: list[SeriesProgressItem] = Field(default_factory=list)
    optional: list[SeriesProgressItem] = Field(default_factory=list)
    alternates: list[SeriesProgressItem] = Field(default_factory=list)
    current: SeriesProgressItem | None = None
    next_unwatched: SeriesProgressItem | None = None
    completed_required: int = 0
    total_required: int = 0
    progress_percent: int = 0
    summary: str = ""
    notes: list[str] = Field(default_factory=list)


class SeriesCandidateStatus(BaseModel):
    subject_id: int
    collection_state: CollectionState = "unknown"
    collection_label: str = "状态未知"
    is_sequel: bool = False
    prerequisites_satisfied: bool = True
    predecessor_ids: list[int] = Field(default_factory=list)
    completed_predecessor_ids: list[int] = Field(default_factory=list)
    missing_predecessors: list[dict[str, Any]] = Field(default_factory=list)
    next_subject_id: int | None = None
    next_subject_name: str = ""
    action: str = ""
    note: str = ""


def subject_id_from_collection(row: dict[str, Any]) -> int | None:
    subject = row.get("subject") or {}
    value = subject.get("id") or row.get("subject_id") or row.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collection_map(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {sid: row for row in rows if (sid := subject_id_from_collection(row)) is not None}


def collection_state(row: dict[str, Any] | None, *, available: bool) -> CollectionState:
    if row is None:
        return "uncollected" if available else "unknown"
    try:
        return _STATE_BY_TYPE.get(int(row.get("type") or 0), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def state_label(state: CollectionState, ep_status: int | None = None, eps: int | None = None) -> str:
    label = _STATE_LABEL[state]
    if state == "watching" and ep_status:
        return f"{label} {ep_status}/{eps}" if eps else f"{label} · EP{ep_status}"
    return label


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def collection_progress(row: dict[str, Any] | None, fallback_eps: int | None = None) -> tuple[int | None, int | None]:
    if row is None:
        return None, fallback_eps
    subject = row.get("subject") or {}
    ep_status = _int_or_none(row.get("ep_status"))
    eps = _int_or_none(subject.get("eps") or subject.get("total_episodes")) or fallback_eps
    return ep_status, eps


def collection_completed(row: dict[str, Any] | None, fallback_eps: int | None = None) -> tuple[bool, str]:
    if row is None:
        return False, ""
    try:
        ctype = int(row.get("type") or 0)
    except (TypeError, ValueError):
        ctype = 0
    if ctype == 2:
        return True, "Bangumi 条目标记为看过"
    ep_status, eps = collection_progress(row, fallback_eps)
    if ep_status and eps and ep_status >= eps:
        return True, "Bangumi 分集进度已到末集（条目状态尚未改成看过）"
    return False, ""


def necessity_for(name: str, role: SeriesRole) -> SeriesNecessity:
    value = name.lower()
    if any(word.lower() in value for word in _SKIP_WORDS):
        return "skip"
    if role in {"side", "alternate"} or any(word.lower() in value for word in _OPTIONAL_WORDS):
        return "optional"
    # A movie connected by an explicit 前传/续集 edge remains mainline.  Its
    # format alone is not enough to silently let later seasons skip it.
    return "required" if role in {"entry", "main"} else "recommended"


def _name(raw: dict[str, Any], fallback: str = "") -> str:
    return str(raw.get("name_cn") or raw.get("name") or fallback).strip()


def _image(raw: dict[str, Any]) -> str | None:
    images = raw.get("images") or {}
    return images.get("common") or images.get("medium") or images.get("grid") or images.get("large")


def _eps(raw: dict[str, Any]) -> int | None:
    return _int_or_none(raw.get("eps") or raw.get("total_episodes"))


def _action_for(state: CollectionState, name: str, ep_status: int | None, *, personalized: bool) -> str:
    if not personalized:
        return f"从《{name}》开始；登录 Bangumi 后可合并你的系列进度"
    if state == "watching":
        return f"继续《{name}》第 {(ep_status or 0) + 1} 集" if ep_status else f"继续《{name}》"
    if state == "wishlist":
        return f"开始想看列表中的《{name}》"
    if state == "on_hold":
        return f"恢复《{name}》，或确认跳过后再看后续作"
    if state == "dropped":
        return f"确认是否重启《{name}》；不会自动放行后续作"
    if state == "watched":
        return f"《{name}》已完成"
    return f"开始《{name}》"


async def _optional_username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001 - anonymous product pages are valid
        return None
    return str(me.get("username") or me.get("id") or "") or None


async def load_collection_context(
    client: BangumiClient,
    username: str | None,
    rows: list[dict[str, Any]] | None = None,
) -> tuple[str | None, bool, dict[int, dict[str, Any]]]:
    resolved = await _optional_username(client, username)
    if rows is not None:
        return resolved, bool(resolved), collection_map(rows)
    if not resolved:
        return None, False, {}
    try:
        loaded = await client.get_all_user_collections(
            resolved, SUBJECT_TYPE["anime"], collection_type=None, max_items=1200
        )
    except Exception:  # noqa: BLE001 - public graph still works without collection access
        return resolved, False, {}
    return resolved, True, collection_map(loaded)


async def inspect_series_candidate(
    client: BangumiClient,
    subject_id: int,
    collections: dict[int, dict[str, Any]],
    *,
    collection_available: bool,
    subject_name: str = "",
    max_hops: int = _MAX_PREDECESSOR_HOPS,
    relation_memo: SeriesRelationMemo | None = None,
) -> SeriesCandidateStatus:
    """Lightweight strict predecessor audit used by guides and recommender."""
    raw_by_id: dict[int, dict[str, Any]] = {subject_id: {"id": subject_id, "name": subject_name}}
    direct_edges: set[tuple[int, int]] = set()
    queue: list[tuple[int, int]] = [(subject_id, 0)]
    visited = {subject_id}
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_hops:
            continue
        try:
            rels = await (
                relation_memo.get(current)
                if relation_memo is not None
                else client.get_subject_relations(current)
            )
        except Exception:  # noqa: BLE001
            continue
        for rel in rels or []:
            if rel.get("type") != SUBJECT_TYPE["anime"] or rel.get("relation") != "前传" or not rel.get("id"):
                continue
            rid = int(rel["id"])
            direct_edges.add((rid, current))
            raw_by_id.setdefault(rid, rel)
            if rid not in visited:
                visited.add(rid)
                queue.append((rid, depth + 1))

    predecessor_ids = {source for source, _target in direct_edges}
    # A title-marked OVA/recap does not block the mainline even if community
    # relations happen to label it as a predecessor.
    required_ids = {
        sid for sid in predecessor_ids
        if necessity_for(_name(raw_by_id.get(sid, {}), str(sid)), "main") == "required"
    }
    completed_ids = {
        sid for sid in required_ids
        if collection_completed(collections.get(sid), _eps(raw_by_id.get(sid, {})))[0]
    }
    missing_ids = required_ids - completed_ids

    # Topological order over the predecessor subgraph; date/id is only a tie-break.
    all_ids = predecessor_ids | {subject_id}
    indegree = {sid: 0 for sid in all_ids}
    outgoing: dict[int, set[int]] = defaultdict(set)
    for source, target in direct_edges:
        if source in all_ids and target in all_ids and target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1
    ready = sorted((sid for sid, degree in indegree.items() if degree == 0), key=lambda sid: sid)
    ordered: list[int] = []
    while ready:
        sid = ready.pop(0)
        ordered.append(sid)
        for target in sorted(outgoing[sid]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
        ready.sort()
    ordered.extend(sorted(all_ids - set(ordered)))

    candidate_row = collections.get(subject_id)
    candidate_state = collection_state(candidate_row, available=collection_available)
    next_id = next((sid for sid in ordered if sid in missing_ids), None)
    if next_id is None and not collection_completed(candidate_row, _eps(raw_by_id.get(subject_id, {})))[0]:
        next_id = subject_id
    next_raw = raw_by_id.get(next_id or 0, {})
    next_name = _name(next_raw, subject_name if next_id == subject_id else str(next_id or ""))
    next_row = collections.get(next_id or 0)
    next_state = collection_state(next_row, available=collection_available)
    next_ep, _next_eps = collection_progress(next_row, _eps(next_raw))
    missing = [
        {
            "id": sid,
            "name": _name(raw_by_id.get(sid, {}), str(sid)),
            "collection_state": collection_state(collections.get(sid), available=collection_available),
            "collection_label": state_label(
                collection_state(collections.get(sid), available=collection_available),
                *collection_progress(collections.get(sid), _eps(raw_by_id.get(sid, {}))),
            ),
        }
        for sid in ordered if sid in missing_ids
    ]
    prerequisites_satisfied = not missing_ids
    if not predecessor_ids:
        note = "系列入口或未发现必要前作"
    elif prerequisites_satisfied:
        note = "所有必要前作已完成，可以继续这一部"
    elif collection_available:
        note = "尚有必要前作未完成，默认不提前推荐后续作"
    else:
        note = "未登录或收藏不可见，无法确认必要前作是否完成"
    return SeriesCandidateStatus(
        subject_id=subject_id,
        collection_state=candidate_state,
        collection_label=state_label(candidate_state, *collection_progress(candidate_row)),
        is_sequel=bool(predecessor_ids),
        prerequisites_satisfied=prerequisites_satisfied if collection_available else not predecessor_ids,
        predecessor_ids=[sid for sid in ordered if sid in predecessor_ids],
        completed_predecessor_ids=[sid for sid in ordered if sid in completed_ids],
        missing_predecessors=missing,
        next_subject_id=next_id,
        next_subject_name=next_name,
        action=_action_for(next_state, next_name, next_ep, personalized=collection_available) if next_id else "系列主线已完成",
        note=note,
    )


class SeriesProgressService:
    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _resolve_subject(self, args: SeriesProgressArgs) -> dict[str, Any] | None:
        if args.subject_id:
            try:
                return await self.client.get_subject(args.subject_id)
            except Exception:  # noqa: BLE001
                return None
        if not args.title.strip():
            return None
        try:
            result = await self.client.search_subjects(args.title.strip(), SUBJECT_TYPE["anime"], limit=1)
        except Exception:  # noqa: BLE001
            return None
        rows = result.get("data") or []
        return rows[0] if rows else None

    async def build(
        self,
        args: SeriesProgressArgs,
        *,
        collection_rows: list[dict[str, Any]] | None = None,
    ) -> SeriesProgressResult | None:
        seed = await self._resolve_subject(args)
        if not seed or not seed.get("id"):
            return None
        seed_id = int(seed["id"])
        username, available, collections = await load_collection_context(
            self.client, args.username, collection_rows
        )

        raw_by_id: dict[int, dict[str, Any]] = {seed_id: seed}
        roles: dict[int, Literal["main", "side", "alternate"]] = {seed_id: "main"}
        relations: dict[int, str] = {seed_id: "当前条目"}
        main_edges: set[tuple[int, int]] = set()
        queue = [seed_id]
        visited = {seed_id}
        while queue and len(raw_by_id) < args.max_members:
            current = queue.pop(0)
            try:
                rels = await self.client.get_subject_relations(current)
            except Exception:  # noqa: BLE001
                continue
            for rel in rels or []:
                if rel.get("type") != SUBJECT_TYPE["anime"] or not rel.get("id"):
                    continue
                rid = int(rel["id"])
                relation = str(rel.get("relation") or "")
                if relation in _MAIN_RELATIONS:
                    source, target = (rid, current) if relation == "前传" else (current, rid)
                    main_edges.add((source, target))
                    roles[rid] = "main"
                    relations.setdefault(rid, relation)
                    raw_by_id.setdefault(rid, rel)
                    if rid not in visited and len(raw_by_id) < args.max_members:
                        visited.add(rid)
                        queue.append(rid)
                elif relation in _ALTERNATE_RELATIONS and len(raw_by_id) < args.max_members:
                    raw_by_id.setdefault(rid, rel)
                    roles.setdefault(rid, "alternate")
                    relations.setdefault(rid, relation)
                elif relation in _SIDE_RELATIONS and len(raw_by_id) < args.max_members:
                    raw_by_id.setdefault(rid, rel)
                    roles.setdefault(rid, "side")
                    relations.setdefault(rid, relation)

        # Detailed episode totals are essential for progress completion.  Fetch
        # mainline subjects concurrently; optional nodes can use relation payloads.
        main_ids = [sid for sid, role in roles.items() if role == "main"]
        details = await gather_limited(
            [self.client.get_subject(sid) for sid in main_ids], host="bangumi", return_exceptions=True
        )
        for sid, detail in zip(main_ids, details, strict=False):
            if not isinstance(detail, BaseException) and isinstance(detail, dict):
                raw_by_id[sid] = detail

        indegree = {sid: 0 for sid in main_ids}
        outgoing: dict[int, set[int]] = defaultdict(set)
        incoming: dict[int, set[int]] = defaultdict(set)
        for source, target in main_edges:
            if source in indegree and target in indegree and target not in outgoing[source]:
                outgoing[source].add(target)
                incoming[target].add(source)
                indegree[target] += 1

        def order_key(sid: int) -> tuple[str, int]:
            return (str(raw_by_id.get(sid, {}).get("date") or "9999"), sid)

        ready = sorted((sid for sid, degree in indegree.items() if degree == 0), key=order_key)
        ordered_ids: list[int] = []
        while ready:
            sid = ready.pop(0)
            ordered_ids.append(sid)
            for target in sorted(outgoing[sid], key=order_key):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
            ready.sort(key=order_key)
        ordered_ids.extend(sorted(set(main_ids) - set(ordered_ids), key=order_key))

        required_by_id: dict[int, bool] = {}
        completed_by_id: dict[int, bool] = {}
        completion_source: dict[int, str] = {}
        for sid in ordered_ids:
            raw = raw_by_id.get(sid, {})
            role: SeriesRole = "entry" if sid == ordered_ids[0] else "main"
            required_by_id[sid] = necessity_for(_name(raw, str(sid)), role) == "required"
            completed_by_id[sid], completion_source[sid] = collection_completed(collections.get(sid), _eps(raw))

        ancestor_cache: dict[int, set[int]] = {}

        def ancestors(sid: int, trail: set[int] | None = None) -> set[int]:
            if sid in ancestor_cache:
                return ancestor_cache[sid]
            active = set(trail or ())
            if sid in active:
                return set()
            active.add(sid)
            value: set[int] = set()
            for parent in incoming.get(sid, set()):
                value.add(parent)
                value.update(ancestors(parent, active))
            ancestor_cache[sid] = value
            return value

        mainline: list[SeriesProgressItem] = []
        for index, sid in enumerate(ordered_ids):
            raw = raw_by_id.get(sid, {})
            role: SeriesRole = "entry" if index == 0 else "main"
            name = _name(raw, str(sid))
            row = collections.get(sid)
            ep_status, eps = collection_progress(row, _eps(raw))
            state = collection_state(row, available=available)
            prerequisite_ids = [pid for pid in ordered_ids if pid in ancestors(sid) and required_by_id.get(pid)]
            blocked_by = [pid for pid in prerequisite_ids if not completed_by_id.get(pid, False)]
            mainline.append(SeriesProgressItem(
                id=sid,
                name=name,
                date=str(raw.get("date") or ""),
                image=_image(raw),
                eps=eps,
                role=role,
                necessity=necessity_for(name, role),
                relation=relations.get(sid, ""),
                collection_state=state,
                collection_label=state_label(state, ep_status, eps),
                ep_status=ep_status,
                completed=completed_by_id[sid],
                completion_source=completion_source[sid],
                prerequisite_ids=prerequisite_ids,
                blocked_by=blocked_by,
                prerequisites_satisfied=not blocked_by if available else not prerequisite_ids,
                is_current=sid == seed_id,
                action=_action_for(state, name, ep_status, personalized=available),
            ))

        next_item = next(
            (item for item in mainline if not item.completed and item.prerequisites_satisfied),
            None,
        )
        if next_item is not None:
            next_item.is_next = True
        current = next((item for item in mainline if item.id == seed_id), None)

        def peripheral_item(sid: int, role: Literal["side", "alternate"]) -> SeriesProgressItem:
            raw = raw_by_id.get(sid, {})
            name = _name(raw, str(sid))
            row = collections.get(sid)
            ep_status, eps = collection_progress(row, _eps(raw))
            state = collection_state(row, available=available)
            done, source = collection_completed(row, eps)
            return SeriesProgressItem(
                id=sid,
                name=name,
                date=str(raw.get("date") or ""),
                image=_image(raw),
                eps=eps,
                role=role,
                necessity=necessity_for(name, role),
                relation=relations.get(sid, ""),
                collection_state=state,
                collection_label=state_label(state, ep_status, eps),
                ep_status=ep_status,
                completed=done,
                completion_source=source,
                action=("可按兴趣补充" if role == "side" else "替代演绎，不阻塞原主线"),
            )

        optional = sorted(
            (peripheral_item(sid, "side") for sid, role in roles.items() if role == "side"),
            key=lambda item: (item.date or "9999", item.id),
        )
        alternates = sorted(
            (peripheral_item(sid, "alternate") for sid, role in roles.items() if role == "alternate"),
            key=lambda item: (item.date or "9999", item.id),
        )
        required = [item for item in mainline if item.necessity == "required"]
        completed_required = sum(item.completed for item in required)
        total_required = len(required)
        percent = round(completed_required / total_required * 100) if total_required else 100
        if available and next_item:
            summary = f"主线完成 {completed_required}/{total_required}；下一步：{next_item.action}"
        elif available:
            summary = f"主线完成 {completed_required}/{total_required}；当前关系图中的必要主线已完成"
        else:
            summary = "已生成客观系列顺序；登录 Bangumi 后可识别你看到了哪一季"
        notes = [
            "每一季仍按独立 Bangumi subject 判断；不会由第一季看过推断整个系列都看过。",
            "只有所有必要前作完成才放行后续作；OVA/OAD/总集篇、旁支和不同演绎默认不阻塞主线。",
            "关系由 Bangumi 社区维护；缺边、错误关系或收藏不可见时会保守处理，不自动写成已看。",
        ]
        if any(item.collection_state == "watching" and item.completed for item in mainline):
            notes.append("有条目的分集进度已到末集但收藏仍为在看；本轮视为已完成前置，不会自动替你改成看过。")
        return SeriesProgressResult(
            subject_id=seed_id,
            username=username,
            personalized=available,
            collection_available=available,
            mainline=mainline,
            optional=optional[:8],
            alternates=alternates[:8],
            current=current,
            next_unwatched=next_item,
            completed_required=completed_required,
            total_required=total_required,
            progress_percent=percent,
            summary=summary,
            notes=notes,
        )


class SeriesProgressTool(Tool):
    name = "get_series_progress"
    description = (
        "读取动画系列主线、旁支和用户逐季进度，严格找出下一部未完成主线。用于『第一季看过第二季没看』、"
        "『这个系列我看到哪』『下一季该看什么』『能不能直接看第三季』。不会把看过一季推断成看完整个系列。"
    )
    args_model = SeriesProgressArgs
    result_model = SeriesProgressResult

    def __init__(self, client: BangumiClient) -> None:
        self.service = SeriesProgressService(client)

    async def run(self, args: SeriesProgressArgs) -> ToolResult[SeriesProgressResult]:
        result = await self.service.build(args)
        if result is None:
            return ToolResult(ok=False, error="没有找到可解析的动画条目")
        rows = result.mainline + result.optional + result.alternates
        return ToolResult(
            ok=True,
            data=result,
            sources=[
                Citation(title=item.name, url=f"https://bgm.tv/subject/{item.id}", source="bangumi", image=item.image)
                for item in rows[:8]
            ],
        )
