"""候选批量验证工具（支撑 "LLM 提名 + 图谱验证" 这条召回，见 docs/06 §8）。

让 agent 凭长尾知识提名一批冷门候选 → 一次性核实：是否存在于 Bangumi、评分/排名、用户是否已看过。
比逐个 search 高效得多，也避免钻牛角尖。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Tool, ToolResult
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_MAX_COLLECT = 1000
_MAX_TITLES = 15


class CheckArgs(BaseModel):
    titles: list[str] = Field(..., description="你提名的候选作品名列表（凭你的知识提名冷门/小众的）")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    username: str | None = Field(None, description="不传则用当前账号")


class CheckItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    found: bool
    id: int | None = None
    name: str | None = None
    bangumi_score: float | None = None
    rank: int | None = None
    seen: bool = False


class CheckResult(BaseModel):
    items: list[CheckItem] = Field(default_factory=list)


class CheckSubjectsTool(Tool):
    name = "check_subjects"
    description = (
        "批量核实你提名的候选作品：是否存在于 Bangumi、评分/排名、用户是否已看过。"
        "用于『LLM 提名冷门 → 图谱验证』：你一次提名一批（最多 15 个），别逐个 search。"
        "据结果只把 found=true、seen=false、评分不错的推给用户。"
    )
    args_model = CheckArgs
    result_model = CheckResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: CheckArgs) -> ToolResult[CheckResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        username = args.username or (await self.client.get_me()).get("username")
        coll = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in coll if it.get("subject", {}).get("id")}

        out: list[CheckItem] = []
        for title in args.titles[:_MAX_TITLES]:
            res = await self.client.search_subjects(title, stype, sort="match", limit=1)
            data = res.get("data") or []
            if not data:
                out.append(CheckItem(title=title, found=False))
                continue
            x = data[0]
            sid = x.get("id")
            r = x.get("rating") or {}
            out.append(CheckItem(
                title=title, found=True, id=sid,
                name=x.get("name_cn") or x.get("name"),
                bangumi_score=r.get("score"), rank=r.get("rank"),
                seen=bool(sid and sid in seen),
            ))
        return ToolResult(ok=True, data=CheckResult(items=out))
