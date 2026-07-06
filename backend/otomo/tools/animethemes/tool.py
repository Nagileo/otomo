"""AnimeThemes API integration for anime music metadata.

AnimeThemes is used as a structured OP/ED/theme-song source. It is not a
ratings/reputation source and should not replace Bangumi as the canonical ACGN
subject anchor.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_BASE = "https://api.animethemes.moe"


class AnimeThemesArgs(BaseModel):
    title: str = Field(..., description="动画标题，中文/日文/英文均可尝试")
    limit: int = Field(8, ge=1, le=20)


class AnimeThemeEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    anime_title: str
    theme_type: str = ""
    sequence: int | None = None
    song_title: str = ""
    artists: list[str] = Field(default_factory=list)
    slug: str = ""
    video_url: str = ""
    page_url: str = ""


class AnimeThemesResult(BaseModel):
    query: str
    count: int = 0
    entries: list[AnimeThemeEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _flatten_artist_names(theme: dict[str, Any]) -> list[str]:
    names: list[str] = []
    song = theme.get("song") or {}
    for artist in song.get("artists") or []:
        name = artist.get("name")
        if name:
            names.append(str(name))
    return list(dict.fromkeys(names))


class SearchAnimeThemesTool(Tool):
    name = "search_anime_themes"
    description = (
        "查询 AnimeThemes 的动画 OP/ED/插曲元数据：歌曲名、艺术家、主题类型和视频入口。"
        "用于 music/OPED/主题曲相关问题；这是音乐元数据源，不是口碑评分源。"
    )
    args_model = AnimeThemesArgs
    result_model = AnimeThemesResult

    async def run(self, args: AnimeThemesArgs) -> ToolResult[AnimeThemesResult]:
        query = args.title.strip()
        if not query:
            return ToolResult(ok=False, error="title 不能为空")
        params = {
            "q": query,
            "include": "animethemes.song.artists,animethemes.animethemeentries.videos",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout, headers={"User-Agent": settings.bangumi_user_agent}) as client:
                res = await client.get(f"{_BASE}/anime", params=params)
                res.raise_for_status()
                payload = res.json()
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"AnimeThemes 暂不可用：{type(e).__name__}")
        entries: list[AnimeThemeEntry] = []
        for anime in (payload.get("anime") or [])[: max(args.limit, 3)]:
            anime_title = anime.get("name") or anime.get("slug") or query
            for theme in anime.get("animethemes") or []:
                song = theme.get("song") or {}
                entry_payloads = theme.get("animethemeentries") or []
                video_url = ""
                for entry in entry_payloads:
                    for video in entry.get("videos") or []:
                        video_url = video.get("link") or video.get("path") or video_url
                        if video_url:
                            break
                    if video_url:
                        break
                slug = anime.get("slug") or ""
                entries.append(
                    AnimeThemeEntry(
                        anime_title=str(anime_title),
                        theme_type=str(theme.get("type") or ""),
                        sequence=theme.get("sequence"),
                        song_title=str(song.get("title") or ""),
                        artists=_flatten_artist_names(theme),
                        slug=str(slug),
                        video_url=video_url,
                        page_url=f"https://animethemes.moe/anime/{slug}" if slug else "",
                    )
                )
                if len(entries) >= args.limit:
                    break
            if len(entries) >= args.limit:
                break
        return ToolResult(
            ok=True,
            data=AnimeThemesResult(
                query=query,
                count=len(entries),
                entries=entries,
                notes=[
                    "AnimeThemes 适合 OP/ED/主题曲元数据，不提供 Bangumi 式口碑评价。",
                    "视频入口可用性以 AnimeThemes 站内为准；版权地区可能变化。",
                ],
            ),
            sources=[
                Citation(title=f"{e.anime_title} {e.theme_type}{e.sequence or ''} {e.song_title}", url=e.page_url or e.video_url, source="animethemes")
                for e in entries[:5]
            ],
        )


def build_animethemes_tools() -> list[Tool]:
    return [SearchAnimeThemesTool()]
