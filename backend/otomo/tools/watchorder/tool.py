"""补番路线规划 Skill——把"系列图谱编排"封装成一次调用。

Skill ≠ 原子工具：它封装一段多步最佳实践工作流——给作品/IP → 沿 relations 的
前传/续集/不同演绎边 BFS 收集整个系列 → 补年份排观看顺序 → 标入口作 + 列旁支（外传/世界观可选看）。
比让 agent 自己多步编排更稳、更省 token、可复用（"选对场景、不降效率"）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

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


class WatchOrderResult(BaseModel):
    ip: str
    watch_order: list[WatchItem] = Field(default_factory=list)
    side_stories: list[str] = Field(default_factory=list)  # 外传/世界观/剧场版，可选看


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

        # BFS 沿系列边收集整条观看线；同时记旁支
        members: dict[int, dict] = {sid: seed}
        side: list[str] = []
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
                    nm = r.get("name_cn") or r.get("name")
                    if nm and nm not in side:
                        side.append(nm)

        # relations 不带 date → 补 get_subject 拿年份/评分，再按年份排观看顺序
        rows: list[dict] = []
        for mid, m in members.items():
            date, score = m.get("date"), None
            if not date:
                try:
                    raw = await self.client.get_subject(mid)
                    date, score = raw.get("date"), (raw.get("rating") or {}).get("score")
                except Exception:  # noqa: BLE001
                    pass
            rows.append({"id": mid, "name": m.get("name_cn") or m.get("name"), "date": date, "score": score})
        rows.sort(key=lambda x: x["date"] or "9999")  # 无日期的沉底

        order = [
            WatchItem(order=i + 1, id=r["id"], name=r["name"], date=r["date"], score=r["score"])
            for i, r in enumerate(rows)
        ]
        return ToolResult(
            ok=True,
            data=WatchOrderResult(ip=ip, watch_order=order, side_stories=side[:5]),
            sources=[
                Citation(title=w.name, url=f"https://bgm.tv/subject/{w.id}", source="bangumi")
                for w in order[:5]
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
        if username:
            return username
        me = await self.client.get_me()
        return me.get("username") or str(me.get("id"))

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
            username, SUBJECT_TYPE["anime"], collection_type=3, max_items=100
        )
        wishlist = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE["anime"], collection_type=1, max_items=160
        )
        on_hold = (
            await self.client.get_all_user_collections(username, SUBJECT_TYPE["anime"], collection_type=4, max_items=100)
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


def build_watchorder_tools(client: BangumiClient) -> list[Tool]:
    return [WatchOrderTool(client), WatchCopilotTool(client)]
