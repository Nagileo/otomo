"""Bangumi 只读图谱工具（A1）。每个工具：typed 入参 + typed 出参 + run()。

支持单/两跳：角色→声优→作品、作品→角色→声优 等。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from .client import SUBJECT_TYPE, BangumiClient
from .models import (
    CharacterBrief,
    CharacterListResult,
    PersonBrief,
    PersonListResult,
    SubjectBrief,
    SubjectDetail,
    SubjectListResult,
)


def _subject_citation(subject_id: int, name: str, image: str | None = None) -> Citation:
    return Citation(title=name or f"subject {subject_id}",
                    url=f"https://bgm.tv/subject/{subject_id}", source="bangumi", image=image)


# --------------------------------------------------------------------------- #
# 入参 schema
# --------------------------------------------------------------------------- #


class SearchSubjectsArgs(BaseModel):
    keyword: str = Field(..., description="作品名/关键词，如『白色相簿2』")
    type: Literal["anime", "book", "music", "game", "real"] | None = Field(
        None, description="限定条目类型；不传则全部"
    )
    limit: int = Field(10, ge=1, le=50)


class SubjectIdArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi 条目 ID")


class KeywordArgs(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    limit: int = Field(10, ge=1, le=50)


class CharacterIdArgs(BaseModel):
    character_id: int = Field(..., description="Bangumi 角色 ID")


class PersonIdArgs(BaseModel):
    person_id: int = Field(..., description="Bangumi 人物（声优/staff）ID")


class PersonSubjectsArgs(BaseModel):
    person_id: int = Field(..., description="Bangumi 人物（声优/staff）ID")
    type: Literal["anime", "book", "music", "game", "real"] | None = Field(
        None, description="限定作品类型；问『配过哪些动画』时传 anime（会过滤掉音乐专辑等）"
    )
    limit: int = Field(30, ge=1, le=100, description="最多返回多少部")


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #


class SearchSubjectsTool(Tool):
    name = "search_subjects"
    description = "按关键词搜索作品（番剧/书/游戏等），返回候选及评分。用于把作品名解析成 ID。"
    args_model = SearchSubjectsArgs
    result_model = SubjectListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SearchSubjectsArgs) -> ToolResult[SubjectListResult]:
        stype = SUBJECT_TYPE.get(args.type) if args.type else None
        raw = await self.client.search_subjects(args.keyword, stype, limit=args.limit)
        items = [SubjectBrief.from_raw(s) for s in (raw.get("data") or [])]
        return ToolResult(
            ok=True,
            data=SubjectListResult(query=args.keyword, count=len(items), subjects=items),
            sources=[_subject_citation(s.id, s.name_cn or s.name, s.image) for s in items[:5]],
        )


class GetSubjectTool(Tool):
    name = "get_subject"
    description = "按 ID 取作品详情（简介、评分、标签、年份）。"
    args_model = SubjectIdArgs
    result_model = SubjectDetail

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SubjectIdArgs) -> ToolResult[SubjectDetail]:
        raw = await self.client.get_subject(args.subject_id)
        detail = SubjectDetail.from_raw(raw)
        return ToolResult(ok=True, data=detail,
                          sources=[_subject_citation(detail.id, detail.name_cn or detail.name, detail.image)])


class GetSubjectCharactersTool(Tool):
    name = "get_subject_characters"
    description = "取作品的角色列表（含主角/配角关系）；每个角色可进一步查其声优。"
    args_model = SubjectIdArgs
    result_model = CharacterListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SubjectIdArgs) -> ToolResult[CharacterListResult]:
        raw = await self.client.get_subject_characters(args.subject_id)
        chars = [
            CharacterBrief(id=c.get("id"), name=c.get("name", "") or "", relation=c.get("relation"))
            for c in (raw or [])
            if c.get("id")
        ]
        return ToolResult(ok=True, data=CharacterListResult(count=len(chars), characters=chars))


class GetSubjectPersonsTool(Tool):
    name = "get_subject_persons"
    description = "取作品的 staff（导演/脚本/原作/动画制作公司等），每条带职责。用于『导演是谁/哪家公司制作』。"
    args_model = SubjectIdArgs
    result_model = PersonListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SubjectIdArgs) -> ToolResult[PersonListResult]:
        raw = await self.client.get_subject_persons(args.subject_id)
        persons = [
            PersonBrief(
                id=p.get("id"), name=p.get("name", "") or "", type=p.get("type"),
                relation=p.get("relation"),
            )
            for p in (raw or [])
            if p.get("id")
        ]
        return ToolResult(ok=True, data=PersonListResult(count=len(persons), persons=persons))


class SearchCharactersTool(Tool):
    name = "search_characters"
    description = "按名字搜索角色，返回候选角色及 ID。用于把角色名解析成 ID。"
    args_model = KeywordArgs
    result_model = CharacterListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: KeywordArgs) -> ToolResult[CharacterListResult]:
        raw = await self.client.search_characters(args.keyword, limit=args.limit)
        chars = [
            CharacterBrief(id=c.get("id"), name=c.get("name", "") or "")
            for c in (raw.get("data") or [])
            if c.get("id")
        ]
        return ToolResult(ok=True, data=CharacterListResult(count=len(chars), characters=chars))


class GetCharacterPersonsTool(Tool):
    name = "get_character_persons"
    description = "取某角色的声优（CV）/出演者，每条带其所属作品上下文。角色→声优 的一跳。"
    args_model = CharacterIdArgs
    result_model = PersonListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: CharacterIdArgs) -> ToolResult[PersonListResult]:
        raw = await self.client.get_character_persons(args.character_id)
        persons = [
            PersonBrief(
                id=p.get("id"), name=p.get("name", "") or "", type=p.get("type"),
                relation=p.get("staff"), subject_name=p.get("subject_name_cn") or p.get("subject_name"),
            )
            for p in (raw or [])
            if p.get("id")
        ]
        return ToolResult(ok=True, data=PersonListResult(count=len(persons), persons=persons))


class SearchPersonsTool(Tool):
    name = "search_persons"
    description = "按名字搜索人物（声优/导演/编剧等），返回候选及 ID。"
    args_model = KeywordArgs
    result_model = PersonListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: KeywordArgs) -> ToolResult[PersonListResult]:
        raw = await self.client.search_persons(args.keyword, limit=args.limit)
        persons = [
            PersonBrief(id=p.get("id"), name=p.get("name", "") or "", type=p.get("type"))
            for p in (raw.get("data") or [])
            if p.get("id")
        ]
        return ToolResult(ok=True, data=PersonListResult(count=len(persons), persons=persons))


class GetPersonSubjectsTool(Tool):
    name = "get_person_subjects"
    description = (
        "取某人物（声优/staff）参与的作品列表，可按类型过滤（anime/book/...），"
        "每条带其在该作品的职责/角色。声优→作品 的一跳（用于『TA 还配过哪些番』）。"
    )
    args_model = PersonSubjectsArgs
    result_model = SubjectListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: PersonSubjectsArgs) -> ToolResult[SubjectListResult]:
        raw = await self.client.get_person_subjects(args.person_id)
        want = SUBJECT_TYPE.get(args.type) if args.type else None
        items = [
            SubjectBrief.from_raw(s)
            for s in (raw or [])
            if s.get("id") and (want is None or s.get("type") == want)
        ]
        items = items[: args.limit]
        return ToolResult(
            ok=True,
            data=SubjectListResult(query=f"person:{args.person_id}", count=len(items), subjects=items),
            sources=[_subject_citation(s.id, s.name_cn or s.name, s.image) for s in items[:5]],
        )


def build_bangumi_tools(client: BangumiClient) -> list[Tool]:
    return [
        SearchSubjectsTool(client),
        GetSubjectTool(client),
        GetSubjectCharactersTool(client),
        GetSubjectPersonsTool(client),
        SearchCharactersTool(client),
        GetCharacterPersonsTool(client),
        SearchPersonsTool(client),
        GetPersonSubjectsTool(client),
    ]
