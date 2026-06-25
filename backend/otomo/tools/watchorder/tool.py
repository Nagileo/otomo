"""补番路线规划 Skill——把"系列图谱编排"封装成一次调用。

Skill ≠ 原子工具：它封装一段多步最佳实践工作流——给作品/IP → 沿 relations 的
前传/续集/不同演绎边 BFS 收集整个系列 → 补年份排观看顺序 → 标入口作 + 列旁支（外传/世界观可选看）。
比让 agent 自己多步编排更稳、更省 token、可复用（"选对场景、不降效率"）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
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


def build_watchorder_tools(client: BangumiClient) -> list[Tool]:
    return [WatchOrderTool(client)]
