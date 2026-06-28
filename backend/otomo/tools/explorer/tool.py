"""角色/声优探索网络工具（Phase 1 ⊕ 补做）。

把 Bangumi 图谱的"声优→出演作品""作品→角色→声优"聚合成可前端漫游的网络。
复用现有 client 图谱方法，不新增数据源——体现图谱护城河的可视化。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ..bangumi.client import SUBJECT_TYPE, BangumiClient


class ExploreVoiceNetworkArgs(BaseModel):
    person: str | None = Field(None, description="声优/人物名，如 花守ゆみり；看 TA 的出演网络")
    subject_id: int | None = Field(None, description="作品 ID；看该作的角色-声优网络")
    subject_type: Literal["anime", "book", "music", "game", "real"] = Field(
        "anime", description="person 模式按此类型过滤出演作品"
    )
    limit: int = Field(15, ge=1, le=30)
    enrich_scores: bool = Field(True, description="给出演作品补评分并按高分排（稍慢，命中缓存后很快）")


class NetworkNode(BaseModel):
    kind: Literal["work", "character", "voice"]
    id: int
    name: str
    detail: str = ""              # 角色名 / 职责 / CV
    score: float | None = None
    image: str | None = None
    url: str = ""


class VoiceNetworkResult(BaseModel):
    anchor: str
    anchor_kind: Literal["person", "subject"]
    anchor_id: int | None = None
    nodes: list[NetworkNode] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _img(images: dict | None) -> str | None:
    images = images or {}
    return images.get("common") or images.get("medium") or images.get("grid")


class ExploreVoiceNetworkTool(Tool):
    name = "explore_voice_network"
    description = (
        "探索角色/声优图谱网络：给声优名看 TA 的出演作品网络（带角色、评分、按高分排）；"
        "给作品 ID 看该作主要角色及其声优阵容。"
        "用于『这个 CV 还配过哪些高分作 / 这部番声优阵容 / 角色声优漫游』。"
    )
    args_model = ExploreVoiceNetworkArgs
    result_model = VoiceNetworkResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _person_network(self, person: str, stype: str, limit: int, enrich: bool) -> VoiceNetworkResult:
        res = await self.client.search_persons(person, limit=1)
        cand = res.get("data") or []
        if not cand or not cand[0].get("id"):
            return VoiceNetworkResult(anchor=person, anchor_kind="person", notes=[f"未找到人物「{person}」"])
        pid, pname = cand[0]["id"], cand[0].get("name") or person
        raw = await self.client.get_person_subjects(pid)
        want = SUBJECT_TYPE.get(stype)
        works = [s for s in (raw or []) if s.get("id") and (want is None or s.get("type") == want)][:limit]
        nodes: list[NetworkNode] = []
        for s in works:
            sid = s["id"]
            score = None
            img = _img(s.get("images"))
            if enrich:
                try:
                    detail = await self.client.get_subject(sid)
                    score = (detail.get("rating") or {}).get("score")
                    img = img or _img(detail.get("images"))
                except Exception:  # noqa: BLE001
                    pass
            nodes.append(NetworkNode(
                kind="work", id=sid, name=s.get("name_cn") or s.get("name") or "",
                detail=str(s.get("staff") or s.get("relation") or "出演"),
                score=score, image=img, url=f"https://bgm.tv/subject/{sid}",
            ))
        if enrich:
            nodes.sort(key=lambda n: -(n.score or 0))
        return VoiceNetworkResult(
            anchor=pname, anchor_kind="person", anchor_id=pid, nodes=nodes,
            notes=[f"{pname} 的 {stype} 出演网络，共 {len(nodes)} 部" + ("（按评分排）" if enrich else "")],
        )

    async def _subject_network(self, subject_id: int, limit: int) -> VoiceNetworkResult:
        chars = await self.client.get_subject_characters(subject_id)
        rows = [c for c in (chars or []) if c.get("id")]
        rows.sort(key=lambda c: 0 if c.get("relation") == "主角" else 1)
        nodes: list[NetworkNode] = []
        for c in rows[: min(limit, 10)]:
            actors = c.get("actors") or []
            cv = (actors[0].get("name") if actors else "") or "?"
            nodes.append(NetworkNode(
                kind="character", id=c["id"], name=c.get("name") or "",
                detail=f"{c.get('relation') or '角色'} · CV {cv}",
                image=_img(c.get("images")), url=f"https://bgm.tv/character/{c['id']}",
            ))
        return VoiceNetworkResult(
            anchor=f"subject {subject_id}", anchor_kind="subject", anchor_id=subject_id, nodes=nodes,
            notes=[f"该作主要角色及声优阵容，共 {len(nodes)} 个"],
        )

    async def run(self, args: ExploreVoiceNetworkArgs) -> ToolResult[VoiceNetworkResult]:
        if args.person:
            data = await self._person_network(args.person, args.subject_type, args.limit, args.enrich_scores)
        elif args.subject_id:
            data = await self._subject_network(args.subject_id, args.limit)
        else:
            return ToolResult(ok=False, error="需要 person（声优名）或 subject_id（作品）之一")
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=n.name, url=n.url, source="bangumi", image=n.image)
                for n in data.nodes[:5] if n.url
            ],
        )


def build_explorer_tools(client: BangumiClient) -> list[Tool]:
    return [ExploreVoiceNetworkTool(client)]
