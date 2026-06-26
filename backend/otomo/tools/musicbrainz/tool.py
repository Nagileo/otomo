"""MusicBrainz metadata search.

MusicBrainz is used as a structured music metadata supplement for Bangumi music
subjects. It is not a rating/review source, so review fusion should cite it as
metadata evidence instead of score evidence.
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_BASE = "https://musicbrainz.org/ws/2"


MusicBrainzEntity = Literal["release-group", "release", "recording"]


class MusicBrainzArgs(BaseModel):
    keyword: str = Field(..., description="music title / album / artist query")
    entity: MusicBrainzEntity = Field("release-group", description="MusicBrainz entity kind")
    limit: int = Field(5, ge=1, le=10)


class MusicBrainzItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    artist: str = ""
    first_release_date: str | None = None
    primary_type: str | None = None
    disambiguation: str | None = None
    score: int | None = None
    url: str


class MusicBrainzResult(BaseModel):
    query: str
    entity: MusicBrainzEntity
    count: int
    source_url: str
    results: list[MusicBrainzItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _artist_credit(raw: dict) -> str:
    credits = raw.get("artist-credit") or []
    parts: list[str] = []
    for credit in credits:
        if isinstance(credit, dict):
            if isinstance(credit.get("artist"), dict) and credit["artist"].get("name"):
                parts.append(str(credit["artist"]["name"]))
            elif credit.get("name"):
                parts.append(str(credit["name"]))
    return ", ".join(dict.fromkeys(parts))


def _entity_key(entity: MusicBrainzEntity) -> str:
    return {
        "release-group": "release-groups",
        "release": "releases",
        "recording": "recordings",
    }[entity]


def _parse_musicbrainz_items(data: dict, entity: MusicBrainzEntity, limit: int) -> list[MusicBrainzItem]:
    out: list[MusicBrainzItem] = []
    for raw in data.get(_entity_key(entity), []) or []:
        mbid = raw.get("id")
        title = raw.get("title")
        if not mbid or not title:
            continue
        out.append(
            MusicBrainzItem(
                id=mbid,
                title=title,
                artist=_artist_credit(raw),
                first_release_date=raw.get("first-release-date") or raw.get("date"),
                primary_type=raw.get("primary-type") or raw.get("type"),
                disambiguation=raw.get("disambiguation") or None,
                score=raw.get("score"),
                url=f"https://musicbrainz.org/{entity}/{mbid}",
            )
        )
        if len(out) >= limit:
            break
    return out


class SearchMusicBrainzTool(Tool):
    name = "search_musicbrainz"
    description = (
        "Search MusicBrainz structured music metadata. Use it for Bangumi music subjects "
        "when album/track/artist/release-date metadata is needed. This is not a review source."
    )
    args_model = MusicBrainzArgs
    result_model = MusicBrainzResult

    async def run(self, args: MusicBrainzArgs) -> ToolResult[MusicBrainzResult]:
        path = f"{_BASE}/{args.entity}/"
        params = {"query": args.keyword.strip(), "fmt": "json", "limit": args.limit}
        url = f"{path}?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                headers={
                    "User-Agent": settings.bangumi_user_agent,
                    "Accept": "application/json",
                },
            ) as c:
                r = await c.get(path, params=params)
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"MusicBrainz query failed: {type(e).__name__}")
        items = _parse_musicbrainz_items(data, args.entity, args.limit)
        return ToolResult(
            ok=True,
            data=MusicBrainzResult(
                query=args.keyword,
                entity=args.entity,
                count=len(items),
                source_url=url,
                results=items,
                caveats=[
                    "MusicBrainz provides metadata, not community rating evidence.",
                    "For ACG music, Bangumi remains the primary community anchor; MusicBrainz is supplementary.",
                ],
            ),
            sources=[Citation(title=f"MusicBrainz - {i.title}", url=i.url, source="musicbrainz") for i in items[:5]],
        )


def build_musicbrainz_tools() -> list[Tool]:
    return [SearchMusicBrainzTool()]
