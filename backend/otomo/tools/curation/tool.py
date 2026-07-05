"""Bangumi indices / curated-list tools."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_CURATED_FILE = Path(__file__).resolve().parents[2] / "data" / "curated_indices.json"


class BangumiIndexArgs(BaseModel):
    index_id: int | None = Field(None, description="Bangumi 目录 ID")
    index_url: str = Field("", description="bgm.tv/index/{id} 链接；可不传 index_id")
    username: str | None = Field(None, description="可选：join 用户收藏状态")
    limit: int = Field(30, ge=1, le=80)


class IndexSubjectCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    name_cn: str = ""
    type: int | None = None
    score: float | None = None
    rank: int | None = None
    image: str | None = None
    comment: str = ""
    collection_status: str = ""
    collection_rate: int | None = None
    url: str = ""


class BangumiIndexResult(BaseModel):
    index_id: int
    title: str
    description: str = ""
    creator: str = ""
    source_url: str
    count: int
    items: list[IndexSubjectCard] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _index_id(args: BangumiIndexArgs) -> int | None:
    if args.index_id:
        return args.index_id
    m = re.search(r"/index/(\d+)", args.index_url)
    return int(m.group(1)) if m else None


def load_curated_indices() -> list[dict[str, Any]]:
    try:
        data = json.loads(_CURATED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("id")]


def _subject(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("subject") if isinstance(row.get("subject"), dict) else row


def _card(row: dict[str, Any]) -> IndexSubjectCard | None:
    subj = _subject(row)
    sid = subj.get("id")
    if not sid:
        return None
    rating = subj.get("rating") or {}
    img = subj.get("images") or {}
    return IndexSubjectCard(
        id=int(sid),
        name=str(subj.get("name") or ""),
        name_cn=str(subj.get("name_cn") or ""),
        type=subj.get("type"),
        score=rating.get("score"),
        rank=rating.get("rank"),
        image=img.get("common") or img.get("medium") or img.get("grid"),
        comment=str(row.get("comment") or row.get("description") or "")[:220],
        url=f"https://bgm.tv/subject/{sid}",
    )


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return str(me.get("username") or me.get("id") or "") or None


class GetBangumiIndexTool(Tool):
    name = "get_bangumi_index"
    description = (
        "读取 Bangumi 目录(index)：目录标题/简介/创建者署名和条目卡。"
        "用于用户贴 bgm.tv/index 链接、查榜单/片单/策展目录；可 join 用户收藏状态。"
    )
    args_model = BangumiIndexArgs
    result_model = BangumiIndexResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: BangumiIndexArgs) -> ToolResult[BangumiIndexResult]:
        iid = _index_id(args)
        if not iid:
            return ToolResult(ok=False, error="需要 index_id 或 bgm.tv/index/{id} 链接")
        await emit_tool_progress(tool=self.name, summary=f"读取 Bangumi 目录 {iid}", current=1, total=3)
        meta = await self.client.get_index(iid)
        rows: list[dict[str, Any]] = []
        offset = 0
        while len(rows) < args.limit:
            page = await self.client.get_index_subjects(iid, limit=min(50, args.limit - len(rows)), offset=offset)
            batch = page.get("data") if isinstance(page, dict) else page
            if not isinstance(batch, list):
                batch = []
            rows.extend(batch)
            if len(batch) < min(50, args.limit - len(rows) + len(batch)):
                break
            offset += len(batch)
        await emit_tool_progress(tool=self.name, summary=f"目录条目 {len(rows)} 个，join 收藏状态", current=2, total=3)
        cards = [x for row in rows if (x := _card(row))]
        user = await _username(self.client, args.username) if args.username else None
        if user:
            async def join(card: IndexSubjectCard) -> IndexSubjectCard:
                try:
                    coll = await self.client.get_user_collection(user, card.id)
                    card.collection_status = str(coll.get("type") or "")
                    card.collection_rate = coll.get("rate")
                except Exception:  # noqa: BLE001
                    pass
                return card
            joined = await gather_limited([join(card) for card in cards], host="bangumi")
            cards = [x for x in joined if isinstance(x, IndexSubjectCard)]
        creator = meta.get("creator") or meta.get("user") or {}
        creator_name = creator.get("username") or creator.get("nickname") if isinstance(creator, dict) else str(creator or "")
        title = str(meta.get("title") or meta.get("name") or f"index {iid}")
        result = BangumiIndexResult(
            index_id=iid,
            title=title,
            description=str(meta.get("description") or meta.get("desc") or "")[:800],
            creator=creator_name,
            source_url=f"https://bgm.tv/index/{iid}",
            count=len(cards),
            items=cards[: args.limit],
            notes=[
                "Bangumi 目录是用户/社区策展源，应作为口碑/选片线索，不替代 canonical 事实。",
                "条目收藏状态只 join 当前可见收藏；私有不可见会留空。",
            ],
        )
        await emit_tool_progress(tool=self.name, summary=f"目录完成：{title}", current=3, total=3)
        return ToolResult(
            ok=True,
            data=result,
            sources=[Citation(title=title, url=result.source_url, source="bangumi_index")]
            + [Citation(title=c.name_cn or c.name, url=c.url, source="bangumi", image=c.image) for c in cards[:5]],
        )


async def curated_recall_candidates(
    client: BangumiClient,
    *,
    subject_type: str,
    tags: list[str],
    seen: set[int],
    limit: int = 40,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return low-weight candidate subjects from curated Bangumi indices."""
    wanted_type = SUBJECT_TYPE.get(subject_type)
    curated = [
        item for item in load_curated_indices()
        if item.get("subject_type", subject_type) == subject_type
        and (not tags or set(str(t) for t in item.get("tags", [])) & set(tags) or not item.get("tags"))
    ][:4]
    if not curated:
        return []

    async def fetch(item: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        try:
            page = await client.get_index_subjects(int(item["id"]), limit=min(limit, 50), offset=0)
        except Exception:  # noqa: BLE001
            return []
        rows = page.get("data") if isinstance(page, dict) else page
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows or []:
            subj = _subject(row)
            sid = subj.get("id")
            if not sid or sid in seen:
                continue
            if wanted_type and subj.get("type") != wanted_type:
                continue
            out.append((subj, item))
        return out

    results = await gather_limited([fetch(item) for item in curated], host="bangumi")
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        out.extend(result)
    return out[:limit]


def build_curation_tools(client: BangumiClient) -> list[Tool]:
    return [GetBangumiIndexTool(client)]
