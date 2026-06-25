"""萌娘百科设定/梗/考据检索工具（A4 RAG 第一刀）。

约束下的务实 RAG：萌娘只能按标题取、不能建持久语料库，所以**按需取单页 → 切块 → 按查询排序 → 返回 top 片段 + 来源**。
（向量库 / 跨页混合检索属后续 C3；现在的"语料"就是临时取来的这一页，词法排序足够。）
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from .._rag import chunk_text, hybrid_rank
from .client import MoegirlClient


class LoreArgs(BaseModel):
    query: str = Field(..., description="想了解的设定/梗/考据问题，如『后藤一里的吉他梗』")
    title_hint: str | None = Field(
        None, description="若已知词条名（角色/作品名）就传，用于更准地定位萌娘页面"
    )


class LoreResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    found: bool
    snippets: list[str] = Field(default_factory=list)


class LoreSearchTool(Tool):
    name = "lore_search"
    description = (
        "从萌娘百科检索角色/作品的设定、剧情、梗、术语、考据等非结构化知识。"
        "用于回答『XX 有什么梗 / 设定 / 由来』这类 Bangumi 结构化数据答不了的问题。返回片段并附来源链接。"
    )
    args_model = LoreArgs
    result_model = LoreResult

    def __init__(self, client: MoegirlClient) -> None:
        self.client = client

    async def run(self, args: LoreArgs) -> ToolResult[LoreResult]:
        seed = args.title_hint or args.query
        titles = await self.client.opensearch(seed, limit=3)
        if not titles:
            return ToolResult(
                ok=True, data=LoreResult(title="", found=False, snippets=[]),
                error=None,
            )
        page = await self.client.extract(titles[0], intro_only=False)
        if not page or not page.get("extract"):
            return ToolResult(ok=True, data=LoreResult(title=titles[0], found=False, snippets=[]))

        snippets = hybrid_rank(args.query, chunk_text(page["extract"]))
        cite = Citation(
            title=f"萌娘百科 — {page['title']}",
            url=page.get("fullurl") or "",
            source="moegirl",
        )
        return ToolResult(
            ok=True,
            data=LoreResult(title=page["title"], found=True, snippets=snippets),
            sources=[cite],
        )


def build_moegirl_tools(client: MoegirlClient) -> list[Tool]:
    return [LoreSearchTool(client)]
