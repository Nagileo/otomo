"""AniList（英文圈动画/漫画库）查询——Source Router 的 Canonical 兜底添头。

GraphQL API（graphql.anilist.co），无 token，评分满分 100。
**关键约束：中文名搜不到，须用日文原名 / 英文 / 罗马音**（可先用 search_subjects 拿 Bangumi 的 name 日文原名再来搜）。
定位：主源是 Bangumi，这里作"查不到再补英文圈数据/别名/评分"的兜底，主体不动摇。
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_ANILIST_API = "https://graphql.anilist.co"
_MEDIA_FIELDS = "id title{romaji native english} averageScore seasonYear format episodes"
_QUERY = (
    "query($s:String,$t:MediaType){Page(perPage:%d){media(search:$s,type:$t)"
    "{" + _MEDIA_FIELDS + "}}}"
)
_BATCH_SIZE = 8


class AniListArgs(BaseModel):
    keyword: str = Field(
        ..., description="作品名——**用日文原名 / 英文 / 罗马音**搜（中文名搜不到；可先 search_subjects 拿 Bangumi 的 name）"
    )
    type: Literal["anime", "manga"] = "anime"
    limit: int = Field(5, ge=1, le=10)
    expected_year: int | None = Field(None, ge=1900, le=date.today().year + 5, description="用于消除同名作品歧义的年份")
    expected_episodes: int | None = Field(None, ge=1, le=5000, description="用于消除同名动画歧义的集数")


class AniListMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    title_romaji: str = ""
    title_native: str = ""
    title_english: str | None = None
    score: int | None = None   # 满分 100
    year: int | None = None
    format: str | None = None
    episodes: int | None = None
    mapping_confidence: float = 0.0
    matched_by: str = "title_mismatch"
    mapping_note: str = ""
    verified: bool = False


class AniListResult(BaseModel):
    query: str
    count: int
    results: list[AniListMedia] = Field(default_factory=list)
    mapping_status: Literal["verified", "ambiguous", "unmatched"] = "unmatched"
    mapping_warnings: list[str] = Field(default_factory=list)


def _norm_title(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalnum())


_SAFE_TITLE_SUFFIXES = (
    "tv", "movie", "themovie", "ova", "oad", "season", "part", "manga", "comic",
    "完全版", "新装版", "総集編", "剧场版", "劇場版",
)


def _safe_title_delta(left: str, right: str) -> bool:
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    if not shorter or not longer.startswith(shorter):
        return False
    delta = longer[len(shorter):]
    return bool(delta) and any(token in delta for token in _SAFE_TITLE_SUFFIXES)


def _mapping_confidence(args: AniListArgs, media: dict) -> tuple[float, str, str]:
    """Conservatively align one AniList result to the requested canonical title.

    Search rank is deliberately not evidence. Exact canonical/alias titles are
    accepted; a small allow-list covers harmless edition suffixes. Year and
    episode metadata only disambiguate an already credible title match.
    """
    query_key = _norm_title(args.keyword)
    title = media.get("title") or {}
    title_keys = {
        _norm_title(title.get("native")),
        _norm_title(title.get("romaji")),
        _norm_title(title.get("english")),
    }
    title_keys.discard("")
    if not query_key or not title_keys:
        return 0.0, "missing_title", "标题信息不足，无法建立外站映射"
    if query_key in title_keys:
        confidence = 0.94
        matched_by = "exact_title"
    elif any(_safe_title_delta(query_key, key) for key in title_keys):
        confidence = 0.84
        matched_by = "safe_edition_delta"
    else:
        return 0.0, "title_mismatch", "检索结果标题与 Bangumi canonical 标题不一致"

    notes = ["标题精确一致" if matched_by == "exact_title" else "标题仅有安全的版本后缀差异"]
    result_year = media.get("seasonYear")
    if args.expected_year and result_year:
        year_delta = abs(int(result_year) - args.expected_year)
        if year_delta == 0:
            confidence += 0.04
            notes.append("年份一致")
        elif year_delta >= 2:
            confidence -= 0.22
            notes.append(f"年份冲突（Bangumi {args.expected_year} / AniList {result_year}）")
        else:
            notes.append("年份相差 1 年（可能是连载/播出跨年）")
    result_episodes = media.get("episodes")
    if args.expected_episodes and result_episodes:
        if int(result_episodes) == args.expected_episodes:
            confidence += 0.02
            notes.append("集数一致")
        elif abs(int(result_episodes) - args.expected_episodes) >= 2:
            confidence -= 0.14
            notes.append(f"集数冲突（Bangumi {args.expected_episodes} / AniList {result_episodes}）")
    return round(max(0.0, min(confidence, 1.0)), 3), matched_by, "；".join(notes)


class SearchAniListTool(Tool):
    name = "search_anilist"
    description = (
        "在 AniList（英文圈动画/漫画库）搜作品，拿 canonical 评分（满分 100）/ 年份 / 集数 / 别名。"
        "Bangumi 查不到、或想要英文圈评分/别名时的**兜底**。"
        "**用日文原名或英文名搜，中文名搜不到**（可先 search_subjects 拿日文 name 再来搜）。引用注明 AniList（满分100）。"
    )
    args_model = AniListArgs
    result_model = AniListResult

    @staticmethod
    def _result(args: AniListArgs, media: list[dict]) -> ToolResult[AniListResult]:
        items: list[AniListMedia] = []
        for raw in media:
            if not raw.get("id"):
                continue
            confidence, matched_by, note = _mapping_confidence(args, raw)
            items.append(AniListMedia(
                id=raw["id"],
                title_romaji=(raw.get("title") or {}).get("romaji") or "",
                title_native=(raw.get("title") or {}).get("native") or "",
                title_english=(raw.get("title") or {}).get("english"),
                score=raw.get("averageScore"),
                year=raw.get("seasonYear"),
                format=raw.get("format"),
                episodes=raw.get("episodes"),
                mapping_confidence=confidence,
                matched_by=matched_by,
                mapping_note=note,
            ))
        items.sort(key=lambda item: item.mapping_confidence, reverse=True)
        credible = [item for item in items if item.mapping_confidence >= 0.84]
        mapping_status: Literal["verified", "ambiguous", "unmatched"] = "unmatched"
        warnings: list[str] = []
        if credible:
            top = credible[0]
            tied = [item for item in credible[1:] if top.mapping_confidence - item.mapping_confidence < 0.05]
            if tied:
                mapping_status = "ambiguous"
                warnings.append(
                    "AniList 返回多个同等可信的同名条目，缺少足够年份/集数证据，已拒绝自动对齐。"
                )
            else:
                mapping_status = "verified"
                top.verified = True
        if mapping_status == "unmatched" and items:
            warnings.append("AniList 检索结果未通过标题/年份/集数对齐门禁，评分未被采用。")
        return ToolResult(
            ok=True,
            data=AniListResult(
                query=args.keyword,
                count=len(items),
                results=items,
                mapping_status=mapping_status,
                mapping_warnings=warnings,
            ),
            sources=[
                Citation(title=f"AniList — {item.title_romaji}", url=f"https://anilist.co/{args.type}/{item.id}", source="anilist")
                for item in items if item.verified
            ],
        )

    async def run_many(self, requests: list[AniListArgs]) -> list[ToolResult[AniListResult]]:
        """Resolve independent title searches with GraphQL aliases.

        Recommendation verification commonly checks 16 finalists. Sending one
        HTTP request per title adds no evidence quality; aliases preserve every
        individual search while reducing connection and rate-limit overhead.
        """
        if not requests:
            return []
        output: list[ToolResult[AniListResult] | None] = [None] * len(requests)

        async def fetch_chunk(client: httpx.AsyncClient, indexed: list[tuple[int, AniListArgs]]) -> None:
            definitions = ",".join(f"$s{idx}:String" for idx, _args in indexed)
            selections = []
            variables: dict[str, str] = {}
            for idx, args in indexed:
                mtype = "ANIME" if args.type == "anime" else "MANGA"
                selections.append(
                    f"q{idx}:Page(perPage:{args.limit})"
                    f"{{media(search:$s{idx},type:{mtype}){{{_MEDIA_FIELDS}}}}}"
                )
                variables[f"s{idx}"] = args.keyword
            query = f"query({definitions}){{{''.join(selections)}}}"
            try:
                response = await client.post(_ANILIST_API, json={"query": query, "variables": variables})
                response.raise_for_status()
                data = (response.json().get("data") or {})
            except (httpx.HTTPError, httpx.TransportError) as exc:
                for idx, _args in indexed:
                    output[idx] = ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}")
                return
            for idx, args in indexed:
                media = ((data.get(f"q{idx}") or {}).get("media") or [])
                output[idx] = self._result(args, media)

        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                chunks = [
                    list(enumerate(requests))[start:start + _BATCH_SIZE]
                    for start in range(0, len(requests), _BATCH_SIZE)
                ]
                await asyncio.gather(
                    *(fetch_chunk(c, chunk) for chunk in chunks),
                )
        except (httpx.HTTPError, httpx.TransportError) as exc:
            return [ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}") for _args in requests]

        return [
            result if result is not None else ToolResult(ok=False, error="AniList 批量查询未返回结果")
            for result in output
        ]

    async def run(self, args: AniListArgs) -> ToolResult[AniListResult]:
        # 单标题入口保留原 GraphQL 变量查询；推荐批量核验走 run_many。
        mtype = "ANIME" if args.type == "anime" else "MANGA"
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                response = await c.post(
                    _ANILIST_API,
                    json={"query": _QUERY % args.limit, "variables": {"s": args.keyword, "t": mtype}},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, httpx.TransportError) as exc:
            return ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}")
        media = ((data.get("data") or {}).get("Page") or {}).get("media") or []
        return self._result(args, media)


def build_anilist_tools() -> list[Tool]:
    return [SearchAniListTool()]
