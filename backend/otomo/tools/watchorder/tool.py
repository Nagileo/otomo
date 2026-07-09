"""补番路线规划 Skill——把"系列图谱编排"封装成一次调用。

Skill ≠ 原子工具：它封装一段多步最佳实践工作流——给作品/IP → 沿 relations 的
前传/续集/不同演绎边 BFS 收集整个系列 → 补年份排观看顺序 → 标入口作 + 列旁支（外传/世界观可选看）。
比让 agent 自己多步编排更稳、更省 token、可复用（"选对场景、不降效率"）。
"""
from __future__ import annotations

from datetime import UTC, datetime
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import InboxItem, MemorySummary, WeeklyChannel, WeeklyDigestSubscription, WeeklyWebhookFormat, memory_summary
from ...notifications import dispatch_weekly_digest_notifications
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..calendar.tool import AiringProgressArgs, AiringProgressItem, AiringProgressTool

_SERIES_REL = {"续集", "前传", "不同演绎"}             # 同一观看线（排进顺序）
_SIDE_REL = {"外传", "相同世界观", "不同世界观", "番外篇"}  # 旁支（可选看，不排进主线）
_MAX_SERIES = 15  # 系列规模上限（控 API / 性能）


class WatchOrderArgs(BaseModel):
    title: str = Field(..., description="作品名或 IP，如『刀剑神域』『Fate/stay night』")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"


class WatchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order: int
    id: int
    name: str
    date: str | None = None
    score: float | None = None
    relation: str = ""
    watch_role: Literal["main", "entry", "side", "alternate"] = "main"
    necessity: Literal["required", "recommended", "optional", "skip"] = "recommended"
    skip_advice: str = ""
    episode_count: int | None = None
    duration_hint: str = ""


class WatchOrderResult(BaseModel):
    ip: str
    watch_order: list[WatchItem] = Field(default_factory=list)
    side_stories: list[WatchItem] = Field(default_factory=list)  # 外传/世界观/剧场版，可选看
    alternate_routes: list[WatchItem] = Field(default_factory=list)
    skip_candidates: list[WatchItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WatchCopilotArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    limit: int = Field(6, ge=1, le=12, description="本周队列最多返回多少部")
    include_on_hold: bool = Field(True, description="是否把搁置作品纳入盘活候选")


class WatchCopilotItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    status: Literal["在看", "想看", "搁置"]
    action: str
    why: list[str] = Field(default_factory=list)
    score: float = 0.0
    ep_status: int | None = None
    eps: int | None = None
    bangumi_score: float | None = None
    image: str | None = None
    tags: list[str] = Field(default_factory=list)


class WatchCopilotResult(BaseModel):
    username: str
    profile_tags: list[str] = Field(default_factory=list)
    queue: list[WatchCopilotItem] = Field(default_factory=list)
    continue_watching: list[WatchCopilotItem] = Field(default_factory=list)
    start_from_wishlist: list[WatchCopilotItem] = Field(default_factory=list)
    revive_on_hold: list[WatchCopilotItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WeeklyDigestArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    limit: int = Field(8, ge=3, le=20)
    include_on_hold: bool = True


class WeeklyDigestSection(BaseModel):
    title: str
    items: list[WatchCopilotItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WeeklyDigestResult(BaseModel):
    username: str
    week: str
    profile_tags: list[str] = Field(default_factory=list)
    sections: list[WeeklyDigestSection] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class ConfigureWeeklyDigestArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    enabled: bool = True
    weekday: int = Field(0, ge=0, le=6, description="0=Monday")
    hour: int = Field(9, ge=0, le=23)
    timezone: str = "Asia/Shanghai"
    push_grading: Literal["brief", "normal", "detailed"] = Field("normal", description="推送内容粒度")
    limit: int = Field(8, ge=3, le=20)
    include_on_hold: bool = True
    channels: list[WeeklyChannel] = Field(
        default_factory=lambda: ["inbox"],
        description="通知渠道：inbox/webhook/email；webhook/email 需要对应地址和服务端配置",
    )
    email: str = Field("", description="email 渠道收件地址")
    webhook_url: str = Field("", description="webhook 渠道 URL")
    webhook_format: WeeklyWebhookFormat = Field(
        "generic",
        description="webhook 格式：generic/serverchan/telegram/discord/feishu",
    )
    web_push_endpoint: str = Field("", description="Web Push endpoint；HTTPS 部署后由浏览器订阅写入")
    web_push_p256dh: str = Field("", description="Web Push p256dh key")
    web_push_auth: str = Field("", description="Web Push auth secret")


class WeeklyDigestMemoryResult(BaseModel):
    username: str
    subscription: WeeklyDigestSubscription
    memory: MemorySummary
    message: str = ""


class GenerateWeeklyDigestNowArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    limit: int = Field(8, ge=3, le=20)
    include_on_hold: bool = True
    mark_unread: bool = True
    dispatch: bool = Field(False, description="是否按当前订阅渠道真实发送，用于测试 webhook/email")


class WeeklyDigestInboxArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    unread_only: bool = False
    limit: int = Field(8, ge=1, le=30)


class WeeklyDigestInboxResult(BaseModel):
    username: str
    items: list[InboxItem] = Field(default_factory=list)
    memory: MemorySummary


def _subject_name(item: dict) -> str:
    subj = item.get("subject") or item
    return subj.get("name_cn") or subj.get("name") or ""


def _subject_id(item: dict) -> int | None:
    sid = (item.get("subject") or item).get("id")
    return int(sid) if sid else None


def _subject_image(item: dict) -> str | None:
    img = ((item.get("subject") or item).get("images") or {})
    return img.get("common") or img.get("medium") or img.get("grid")


def _subject_tags(item: dict) -> list[str]:
    tags = ((item.get("subject") or item).get("tags") or [])
    return [
        t.get("name")
        for t in tags
        if isinstance(t, dict) and t.get("name")
    ][:10]


def _rating(item: dict) -> dict:
    return ((item.get("subject") or item).get("rating") or {})


def _eps(item: dict) -> int | None:
    value = (item.get("subject") or item).get("eps") or (item.get("subject") or item).get("total_episodes")
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _watch_metadata(name: str, relation: str, role: str, eps: int | None) -> tuple[str, str, str]:
    text = f"{name} {relation}".lower()
    if any(k.lower() in text for k in ("总集篇", "recap", "総集編", "summary")):
        return "skip", "总集篇/回顾性质，通常可跳过，除非你想复习剧情。", "回顾/总集篇"
    if any(k.lower() in text for k in ("ova", "oad", "sp", "番外", "外传", "特典", "special")) or role == "side":
        hint = "可选补充"
        if eps:
            hint = f"{eps} 集左右的可选补充"
        return "optional", "番外/OVA/OAD/外传通常不影响主线理解，看完主线后按兴趣补。", hint
    if any(k in text for k in ("剧场版", "映画", "movie")):
        return "recommended", "剧场版可能是主线续作、总集篇或外传；按 relation 和上映时间判断，建议看前确认是否承接主线。", "剧场版/长篇"
    if role in {"entry", "main"}:
        hint = f"{eps} 集" if eps else "主线条目"
        return "required", "主线/前传/续作，建议按顺序观看。", hint
    return "recommended", "关系边不足以判定必要性，建议按日期和 relation 参考。", f"{eps} 集" if eps else ""


async def _resolve_username(client: BangumiClient, username: str | None) -> str:
    if username:
        return username
    me = await client.get_me()
    return me.get("username") or str(me.get("id"))


def _digest_inbox_item(result: WeeklyDigestResult, *, unread: bool = True) -> InboxItem:
    return InboxItem(
        id=secrets.token_urlsafe(14),
        kind="weekly_digest",
        title=f"{result.week} 周报",
        payload=result.model_dump(mode="json", exclude_none=True),
        unread=unread,
        created_at=now_iso(),
    )


class WatchOrderTool(Tool):
    name = "plan_watch_order"
    description = (
        "规划某作品/系列的**推荐观看顺序（补番路线）**：沿图谱前传/续集/不同演绎边收集整个系列，"
        "按年份排序、第 1 部即入口作，并单列可选看的外传/剧场版。"
        "用于『XX 怎么入坑 / 按什么顺序看 / 补番路线 / 先看哪部 / 系列观看顺序』。"
    )
    args_model = WatchOrderArgs
    result_model = WatchOrderResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: WatchOrderArgs) -> ToolResult[WatchOrderResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        res = await self.client.search_subjects(args.title, stype, limit=1)
        data = res.get("data") or []
        if not data:
            return ToolResult(ok=False, error=f"没找到作品《{args.title}》")
        seed = data[0]
        sid = seed["id"]
        ip = seed.get("name_cn") or seed.get("name")

        # BFS 沿系列边收集整条观看线；同时记旁支/不同演绎，供补番路线面板分栏展示。
        members: dict[int, dict] = {sid: seed}
        side_map: dict[int, dict] = {}
        alternate_map: dict[int, dict] = {}
        queue, visited = [sid], {sid}
        while queue and len(visited) < _MAX_SERIES:
            rels = await self.client.get_subject_relations(queue.pop(0))
            for r in rels or []:
                if r.get("type") != stype or not r.get("id"):
                    continue
                rid, rel = r["id"], r.get("relation")
                if rel in _SERIES_REL and rid not in visited:
                    visited.add(rid)
                    queue.append(rid)
                    members[rid] = r
                elif rel in _SIDE_REL:
                    side_map.setdefault(rid, r)
                elif rel in {"不同演绎", "重制", "再编集"}:
                    alternate_map.setdefault(rid, r)

        # relations 不带 date → 补 get_subject 拿年份/评分，再按年份排观看顺序
        async def row_of(mid: int, m: dict, *, role: str, relation: str = "") -> dict:
            date, score, eps = m.get("date"), None, _eps(m)
            raw = None
            if not date:
                try:
                    raw = await self.client.get_subject(mid)
                    date, score = raw.get("date"), (raw.get("rating") or {}).get("score")
                    eps = eps or _eps(raw)
                except Exception:  # noqa: BLE001
                    pass
            name = m.get("name_cn") or m.get("name") or (raw and (raw.get("name_cn") or raw.get("name"))) or f"subject {mid}"
            rel = relation or m.get("relation") or ""
            necessity, skip_advice, duration_hint = _watch_metadata(name, rel, role, eps)
            return {
                "id": mid,
                "name": name,
                "date": date,
                "score": score,
                "relation": rel,
                "role": role,
                "necessity": necessity,
                "skip_advice": skip_advice,
                "episode_count": eps,
                "duration_hint": duration_hint,
            }

        rows = [await row_of(mid, m, role="main") for mid, m in members.items()]
        rows.sort(key=lambda x: x["date"] or "9999")  # 无日期的沉底

        order = [
            WatchItem(
                order=i + 1,
                id=r["id"],
                name=r["name"],
                date=r["date"],
                score=r["score"],
                relation=r.get("relation") or "",
                watch_role="entry" if i == 0 else "main",
                necessity=r["necessity"],
                skip_advice=r["skip_advice"],
                episode_count=r["episode_count"],
                duration_hint=r["duration_hint"],
            )
            for i, r in enumerate(rows)
        ]
        side_rows = [await row_of(mid, m, role="side", relation=m.get("relation") or "") for mid, m in side_map.items()]
        side_rows.sort(key=lambda x: x["date"] or "9999")
        sides = [
            WatchItem(
                order=i + 1,
                id=r["id"],
                name=r["name"],
                date=r["date"],
                score=r["score"],
                relation=r.get("relation") or "外传",
                watch_role="side",
                necessity=r["necessity"],
                skip_advice=r["skip_advice"],
                episode_count=r["episode_count"],
                duration_hint=r["duration_hint"],
            )
            for i, r in enumerate(side_rows[:8])
        ]
        alt_rows = [await row_of(mid, m, role="alternate", relation=m.get("relation") or "") for mid, m in alternate_map.items()]
        alt_rows.sort(key=lambda x: x["date"] or "9999")
        alternates = [
            WatchItem(
                order=i + 1,
                id=r["id"],
                name=r["name"],
                date=r["date"],
                score=r["score"],
                relation=r.get("relation") or "不同演绎",
                watch_role="alternate",
                necessity=r["necessity"],
                skip_advice=r["skip_advice"],
                episode_count=r["episode_count"],
                duration_hint=r["duration_hint"],
            )
            for i, r in enumerate(alt_rows[:8])
        ]
        skip_candidates = [x for x in order + sides + alternates if x.necessity == "skip"]
        notes = [
            "主线按 Bangumi 关系边和播出日期排序；没有日期的条目沉底。",
            "外传/番外/世界观分支不强制排进主线，适合看完入口后按兴趣补。",
            "必要性是基于 Bangumi relation、标题关键词和集数的启发式判断；总集篇/OVA/OAD/番外会标为可跳过或可选。",
        ]
        if alternates:
            notes.append("不同演绎/重制作为替代路线展示，不默认替换主线。")
        return ToolResult(
            ok=True,
            data=WatchOrderResult(
                ip=ip,
                watch_order=order,
                side_stories=sides,
                alternate_routes=alternates,
                skip_candidates=skip_candidates[:8],
                notes=notes,
            ),
            sources=[
                Citation(title=w.name, url=f"https://bgm.tv/subject/{w.id}", source="bangumi")
                for w in (order + sides + alternates)[:5]
            ],
        )


class WatchCopilotTool(Tool):
    name = "plan_watch_copilot"
    description = (
        "根据用户 Bangumi 在看/想看/搁置/已看画像生成本周追番副驾队列：接着看哪部、从哪集继续、"
        "想看列表先开哪几部、搁置作品是否值得盘活。用于『这周看什么 / 帮我安排追番 / 搁置盘活 / 想看太多先看哪部』。"
    )
    args_model = WatchCopilotArgs
    result_model = WatchCopilotResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _username(self, username: str | None) -> str:
        return await _resolve_username(self.client, username)

    def _item(
        self,
        raw: dict,
        status: Literal["在看", "想看", "搁置"],
        profile_tags: set[str],
    ) -> WatchCopilotItem | None:
        sid = _subject_id(raw)
        name = _subject_name(raw)
        if not sid or not name:
            return None
        tags = _subject_tags(raw)
        tag_hits = [t for t in tags if t in profile_tags][:4]
        rating = _rating(raw)
        score = float(rating.get("score") or 0.0)
        rank = rating.get("rank") or 99999
        ep_status = raw.get("ep_status") if isinstance(raw.get("ep_status"), int) else None
        eps = _eps(raw)
        why: list[str] = []
        base = 0.0
        if tag_hits:
            base += 0.35 * len(tag_hits)
            why.append("命中你的画像标签：" + "、".join(tag_hits))
        if score:
            base += score / 10.0
            why.append(f"Bangumi 评分 {score:g}")
        if rank and rank < 1000:
            base += 0.25
            why.append(f"综合排名 {rank}")
        if status == "在看":
            base += 1.4
            action = f"接着看第 {(ep_status or 0) + 1} 集" if ep_status else "接着追当前进度"
            if ep_status and eps:
                remain = max(eps - ep_status, 0)
                why.append(f"当前进度 {ep_status}/{eps}，剩余 {remain} 集")
        elif status == "搁置":
            base += 0.45
            action = f"试着补到第 {(ep_status or 0) + 1} 集再判断" if ep_status else "先补 1 集判断是否盘活"
            why.append("来自搁置列表，属于低压力盘活候选")
        else:
            base += 0.75
            action = "本周开坑 1-2 集试口味"
            why.append("来自想看列表，可作为新开坑候选")
        if eps and eps <= 13:
            base += 0.25
            why.append("短篇，启动成本低")
        return WatchCopilotItem(
            id=sid,
            name=name,
            status=status,
            action=action,
            why=why[:5],
            score=round(base, 3),
            ep_status=ep_status,
            eps=eps,
            bangumi_score=score or None,
            image=_subject_image(raw),
            tags=tags,
        )

    async def run(self, args: WatchCopilotArgs) -> ToolResult[WatchCopilotResult]:
        username = await self._username(args.username)
        watched = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE["anime"], collection_type=2, max_items=1000
        )
        watching = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE["anime"], collection_type=3, max_items=200
        )
        wishlist = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE["anime"], collection_type=1, max_items=400
        )
        on_hold = (
            await self.client.get_all_user_collections(username, SUBJECT_TYPE["anime"], collection_type=4, max_items=200)
            if args.include_on_hold else []
        )
        profile = compute_taste_profile(username, watched)
        profile_tags = {str(x.get("tag")) for x in profile.top_tags[:10] if x.get("tag")}

        def build(rows: list[dict], status: Literal["在看", "想看", "搁置"]) -> list[WatchCopilotItem]:
            items = [x for raw in rows if (x := self._item(raw, status, profile_tags))]
            return sorted(items, key=lambda x: -x.score)

        cont = build(watching, "在看")[: max(args.limit, 4)]
        start = build(wishlist, "想看")[: max(args.limit, 4)]
        revive = build(on_hold, "搁置")[: max(args.limit, 4)]
        queue = sorted(cont[:3] + start[:3] + revive[:2], key=lambda x: -x.score)[: args.limit]
        notes = [
            "追番副驾只基于 Bangumi 收藏状态、评分、标签和 ep_status；不会断言你为何搁置。",
            "在看优先于想看，搁置盘活默认低权重；短篇会轻微加权，适合本周执行。",
        ]
        if not queue:
            notes.append("没有拿到在看/想看/搁置候选，可能收藏不可见或列表为空。")
        return ToolResult(
            ok=True,
            data=WatchCopilotResult(
                username=username,
                profile_tags=[str(x.get("tag")) for x in profile.top_tags[:10] if x.get("tag")],
                queue=queue,
                continue_watching=cont[:5],
                start_from_wishlist=start[:5],
                revive_on_hold=revive[:5],
                notes=notes,
            ),
            sources=[Citation(title=i.name, url=f"https://bgm.tv/subject/{i.id}", source="bangumi", image=i.image) for i in queue[:5]],
        )


class WeeklyDigestTool(Tool):
    name = "build_weekly_digest"
    description = (
        "生成按需周报内容：本周继续追什么、想看列表先开哪部、搁置作品是否盘活、可加入计划板/同步 Bangumi 的下一步。"
        "这是内容生成工具，不做定时推送。"
    )
    args_model = WeeklyDigestArgs
    result_model = WeeklyDigestResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.copilot = WatchCopilotTool(client)
        self.airing = AiringProgressTool(client)

    @staticmethod
    def _airing_item(raw: AiringProgressItem) -> WatchCopilotItem:
        why = []
        if raw.behind > 0:
            why.append(f"已播到第 {raw.aired_ep} 集，你看到第 {raw.my_ep} 集，落后 {raw.behind} 集")
        elif raw.next_air_date:
            why.append(f"当前同步，下集预计 {raw.next_air_date}")
        else:
            why.append("当前没有明显落后")
        if raw.score:
            why.append(f"Bangumi 评分 {raw.score:g}")
        return WatchCopilotItem(
            id=raw.id,
            name=raw.name,
            status="在看" if raw.status == "watching" else "想看",
            action=raw.action,
            why=why[:5],
            score=2.0 + raw.behind,
            ep_status=raw.my_ep or None,
            eps=raw.total_eps,
            bangumi_score=raw.score,
            image=raw.image,
            tags=["本周放送"] if raw.next_air_date or raw.behind else [],
        )

    async def run(self, args: WeeklyDigestArgs) -> ToolResult[WeeklyDigestResult]:
        copilot_res = await self.copilot.run(
            WatchCopilotArgs(
                username=args.username,
                limit=min(args.limit, 12),
                include_on_hold=args.include_on_hold,
            )
        )
        if not copilot_res.ok or copilot_res.data is None:
            return ToolResult(ok=False, error=copilot_res.error or "周报生成失败")
        data = copilot_res.data
        airing_res = await self.airing.run(
            AiringProgressArgs(username=data.username, include_wishlist=True, limit=min(args.limit, 12))
        )
        airing_items: list[WatchCopilotItem] = []
        airing_sources = []
        if airing_res.ok and airing_res.data is not None:
            airing_items = [self._airing_item(x) for x in airing_res.data.items[: args.limit]]
            airing_sources = airing_res.sources
        week = datetime.now(UTC).strftime("%Y-W%U")
        sections = [
            WeeklyDigestSection(
                title="本周放送/进度",
                items=airing_items[:4],
                notes=["结合 Bangumi 正片 airdate 与你的 ep_status；日期以日本放送日为主。"],
            ),
            WeeklyDigestSection(
                title="继续追",
                items=data.continue_watching[:4],
                notes=["优先处理已经在看的条目，减少追番断点。"],
            ),
            WeeklyDigestSection(
                title="想看开坑",
                items=data.start_from_wishlist[:4],
                notes=["从想看列表挑启动成本低、命中画像标签的候选。"],
            ),
        ]
        if args.include_on_hold:
            sections.append(
                WeeklyDigestSection(
                    title="搁置盘活",
                    items=data.revive_on_hold[:4],
                    notes=["只建议低压力试一集，不断言搁置原因。"],
                )
            )
        next_actions = []
        if data.queue:
            next_actions.append("把本周队列加入 Otomo 计划板，按优先级执行。")
            next_actions.append("对确定要看的作品，可准备 Bangumi 写回动作：标记在看或更新进度。")
        if data.start_from_wishlist:
            next_actions.append("想看列表过长时，先开坑 1-2 集再根据反馈调整画像。")
        return ToolResult(
            ok=True,
            data=WeeklyDigestResult(
                username=data.username,
                week=week,
                profile_tags=data.profile_tags,
                sections=sections,
                next_actions=next_actions,
                caveats=[
                    "这是按需周报内容；若配置订阅，会由后台 scheduler 写入 inbox/推送。",
                    "放送进度基于 Bangumi 正片 airdate 和 ep_status；没有断言国内播放平台上架时间。",
                ],
            ),
            sources=(airing_sources + copilot_res.sources)[:8],
        )


class ConfigureWeeklyDigestTool(Tool):
    name = "configure_weekly_digest"
    description = (
        "配置 Otomo 主动周报订阅：星期几/几点生成、是否包含搁置盘活、候选数量。"
        "这是本地 Otomo 状态，不会写回 Bangumi。"
    )
    args_model = ConfigureWeeklyDigestArgs
    result_model = WeeklyDigestMemoryResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: ConfigureWeeklyDigestArgs) -> ToolResult[WeeklyDigestMemoryResult]:
        username = await _resolve_username(self.client, args.username)
        mem = self.ltm.load_user(username)
        old = mem.weekly_digest_subscription
        mem.weekly_digest_subscription = WeeklyDigestSubscription(
            enabled=args.enabled,
            weekday=args.weekday,
            hour=args.hour,
            timezone=args.timezone,
            push_grading=args.push_grading or old.push_grading,
            limit=args.limit,
            include_on_hold=args.include_on_hold,
            channels=list(dict.fromkeys(args.channels or ["inbox"])),
            email=args.email.strip() or old.email,
            webhook_url=args.webhook_url.strip() or old.webhook_url,
            webhook_format=args.webhook_format or old.webhook_format,
            web_push_endpoint=args.web_push_endpoint.strip() or old.web_push_endpoint,
            web_push_p256dh=args.web_push_p256dh.strip() or old.web_push_p256dh,
            web_push_auth=args.web_push_auth.strip() or old.web_push_auth,
            last_delivery=old.last_delivery,
            last_run_key=old.last_run_key,
            updated_at=now_iso(),
        )
        self.ltm.save_user(mem)
        message = ("已开启周报订阅。" if args.enabled else "已关闭周报订阅。") + " 每日追番/RSS/生日提醒请在主动订阅中心配置。"
        return ToolResult(
            ok=True,
            data=WeeklyDigestMemoryResult(
                username=username,
                subscription=mem.weekly_digest_subscription,
                memory=memory_summary(mem),
                message=message,
            ),
        )


class GenerateWeeklyDigestNowTool(Tool):
    name = "generate_weekly_digest_now"
    description = "立即生成一份周报并写入 Otomo inbox，用于测试订阅效果或手动补生成。"
    args_model = GenerateWeeklyDigestNowArgs
    result_model = WeeklyDigestInboxResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm
        self.digest = WeeklyDigestTool(client)

    async def run(self, args: GenerateWeeklyDigestNowArgs) -> ToolResult[WeeklyDigestInboxResult]:
        username = await _resolve_username(self.client, args.username)
        res = await self.digest.run(
            WeeklyDigestArgs(username=username, limit=args.limit, include_on_hold=args.include_on_hold)
        )
        if not res.ok or res.data is None:
            return ToolResult(ok=False, error=res.error or "周报生成失败")
        mem = self.ltm.load_user(username)
        item = _digest_inbox_item(res.data, unread=args.mark_unread)
        if args.dispatch:
            deliveries = await dispatch_weekly_digest_notifications(username, mem.weekly_digest_subscription, item)
            item.payload["deliveries"] = deliveries
            mem.weekly_digest_subscription.last_delivery = deliveries[-8:]
            mem.weekly_digest_subscription.updated_at = now_iso()
        mem.inbox.append(item)
        mem.inbox = mem.inbox[-30:]
        self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=WeeklyDigestInboxResult(username=username, items=mem.inbox[-8:], memory=memory_summary(mem)),
            sources=res.sources,
        )


class ListWeeklyDigestInboxTool(Tool):
    name = "list_weekly_digest_inbox"
    description = "查看 Otomo 本地 inbox 里的周报历史；用于『看看本周周报/历史周报/未读周报』。"
    args_model = WeeklyDigestInboxArgs
    result_model = WeeklyDigestInboxResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: WeeklyDigestInboxArgs) -> ToolResult[WeeklyDigestInboxResult]:
        username = await _resolve_username(self.client, args.username)
        mem = self.ltm.load_user(username)
        items = [x for x in mem.inbox if x.kind == "weekly_digest"]
        if args.unread_only:
            items = [x for x in items if x.unread]
        return ToolResult(
            ok=True,
            data=WeeklyDigestInboxResult(
                username=username,
                items=items[-args.limit:],
                memory=memory_summary(mem),
            ),
        )


def build_watchorder_tools(client: BangumiClient, ltm: LongTermMemory | None = None) -> list[Tool]:
    tools: list[Tool] = [WatchOrderTool(client), WatchCopilotTool(client), WeeklyDigestTool(client)]
    if ltm is not None:
        tools.extend([
            ConfigureWeeklyDigestTool(client, ltm),
            GenerateWeeklyDigestNowTool(client, ltm),
            ListWeeklyDigestInboxTool(client, ltm),
        ])
    return tools
