"""Bangumi 短评（吐槽）抓取工具（口碑质性，外部知识增强档）。

Bangumi v0 API 不暴露短评，故抓取网页 bgm.tv/subject/{id}/comments 并解析 <p class="comment">。
口碑 = 评分分布(API,量化) + 短评(本工具,真实民意) + web_search(更广讨论)。礼貌：浏览器 UA、单次、低频。
"""
from __future__ import annotations

import html
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._rag import ahybrid_rank
from ..bangumi.client import BangumiClient

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r'<p class="comment">(.*?)</p>', re.S)
_STARS = re.compile(r'starstop[^"]*sstars(\d+)')  # 部分短评带评分
_EP_REPLY = re.compile(r'<div class="reply_content">(.*?)</div>', re.S)  # 分集吐槽箱正文


class CommentsArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi 条目 ID")
    query: str | None = Field(
        None, description="想了解的方面（如『作画』『剧情』『结局』『值不值得看』）；传则语义检索最相关短评，不传取最新一批"
    )
    limit: int = Field(12, ge=1, le=30)


class CommentsResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject_id: int
    query: str | None = None
    count: int
    comments: list[str] = Field(default_factory=list)


class GetCommentsTool(Tool):
    name = "get_subject_comments"
    description = (
        "抓取作品在 Bangumi 的用户短评（吐槽），看真实民意/口碑质性。"
        "问某方面口碑（作画/剧情/结局/配乐…）时传 query，会从短评里**语义检索**最相关的几条。"
        "拿到后请**提炼大家夸什么、吐槽什么**并引用代表短评，配合 get_subject 的评分分布一起用，注明来自 Bangumi 短评。"
    )
    args_model = CommentsArgs
    result_model = CommentsResult

    async def run(self, args: CommentsArgs) -> ToolResult[CommentsResult]:
        url = f"https://bgm.tv/subject/{args.subject_id}/comments"
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout, headers={"User-Agent": _BROWSER_UA}, follow_redirects=True
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
                page = r.text
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"短评抓取失败：{type(e).__name__}")

        all_comments: list[str] = []
        for raw in _COMMENT.findall(page):
            text = html.unescape(_TAG.sub("", raw)).strip()
            if text:
                all_comments.append(text)
        # 有 query → 短评纳入 hybrid 检索，捞最相关的几条；否则取最新一批
        if args.query and len(all_comments) > args.limit:
            comments = await ahybrid_rank(args.query, all_comments, top_k=args.limit)
        else:
            comments = all_comments[: args.limit]
        return ToolResult(
            ok=True,
            data=CommentsResult(
                subject_id=args.subject_id, query=args.query, count=len(comments), comments=comments
            ),
            sources=[Citation(title=f"Bangumi 短评 · subject {args.subject_id}", url=url, source="bangumi")],
        )


class EpisodeCommentsArgs(BaseModel):
    ep_id: int = Field(..., description="分集 ep_id（先用 get_subject_episodes 按集号拿到）")
    subject_id: int | None = Field(None, description="可选 Bangumi 条目 ID；用于工具层校验分集序号")
    episode_sort: float | None = Field(None, description="当前 ep 的 sort/集号；用于防剧透硬限制")
    max_episode_sort: float | None = Field(None, description="用户最多看到第几集；若 episode_sort 超过该值，工具拒绝抓取")
    query: str | None = Field(
        None, description="想了解的方面（如『名场面』『作画』『结局』『为什么这集评价高/有争议』）；传则语义检索相关讨论"
    )
    limit: int = Field(12, ge=1, le=30)


class EpisodeCommentsResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ep_id: int
    query: str | None = None
    count: int
    comments: list[str] = Field(default_factory=list)
    blocked_by_spoiler: bool = False
    note: str = ""


class GetEpisodeCommentsTool(Tool):
    name = "get_episode_comments"
    description = (
        "抓取**某一集**的吐槽箱讨论（bgm.tv/ep/{ep_id}），看这一集大家的真实反应。"
        "问『第 X 集大家怎么看 / 某集口碑 / 名场面 / 为什么这集评价高或有争议』时用"
        "（先 get_subject_episodes 按集号拿 ep_id）。传 query 语义检索相关讨论。"
        "拿到后提炼夸点/吐槽点 + 留意剧透，注明来自 Bangumi 分集吐槽。"
    )
    args_model = EpisodeCommentsArgs
    result_model = EpisodeCommentsResult

    def __init__(self, client: BangumiClient | None = None) -> None:
        self.client = client

    async def run(self, args: EpisodeCommentsArgs) -> ToolResult[EpisodeCommentsResult]:
        episode_sort = args.episode_sort
        if episode_sort is None and self.client is not None and args.subject_id is not None:
            try:
                raw = await self.client.get_episodes(args.subject_id, ep_type=0, limit=200)
                for ep in raw.get("data") or []:
                    if ep.get("id") == args.ep_id:
                        episode_sort = ep.get("sort") or ep.get("ep")
                        break
            except Exception:  # noqa: BLE001
                episode_sort = None
        if args.max_episode_sort is not None and episode_sort is not None and episode_sort > args.max_episode_sort:
            return ToolResult(
                ok=True,
                data=EpisodeCommentsResult(
                    ep_id=args.ep_id,
                    query=args.query,
                    count=0,
                    comments=[],
                    blocked_by_spoiler=True,
                    note=f"已按用户进度过滤：请求第 {episode_sort:g} 集，超过允许的第 {args.max_episode_sort:g} 集。",
                ),
            )
        url = f"https://bgm.tv/ep/{args.ep_id}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout, headers={"User-Agent": _BROWSER_UA}, follow_redirects=True
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
                page = r.text
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"分集讨论抓取失败：{type(e).__name__}")

        all_comments: list[str] = []
        for raw in _EP_REPLY.findall(page):
            text = html.unescape(_TAG.sub("", raw)).strip()
            if text:
                all_comments.append(text)
        if args.query and len(all_comments) > args.limit:
            comments = await ahybrid_rank(args.query, all_comments, top_k=args.limit)
        else:
            comments = all_comments[: args.limit]
        return ToolResult(
            ok=True,
            data=EpisodeCommentsResult(
                ep_id=args.ep_id, query=args.query, count=len(comments), comments=comments
            ),
            sources=[Citation(title=f"Bangumi 分集吐槽 · ep {args.ep_id}", url=url, source="bangumi")],
        )


def build_comment_tools(client: BangumiClient | None = None) -> list[Tool]:
    return [GetCommentsTool(), GetEpisodeCommentsTool(client)]
