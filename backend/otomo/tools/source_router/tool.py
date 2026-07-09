"""Cross-media source routing tool.

The point is not to answer facts directly.  It makes the source policy visible:
which layer can support canonical facts, which layer is only discourse or
navigation, and what tools are recommended next for a given medium + intent.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

SubjectTypeName = Literal["anime", "book", "music", "game", "real"]
SourceIntent = Literal["fact", "review", "recommendation", "guide", "resource", "image", "music", "discourse"]
LayerName = Literal["canonical", "metadata", "reputation", "discourse", "navigation"]
RiskLevel = Literal["low", "medium", "high"]


class SourceRouterArgs(BaseModel):
    subject_id: int | None = Field(None, description="可选 Bangumi subject_id；有则用于回锚 title/type/tags")
    title: str = Field("", description="没有 subject_id 时可给标题，工具会轻量搜索 Bangumi")
    subject_type: SubjectTypeName = "anime"
    intent: SourceIntent = "review"


class SourceEntry(BaseModel):
    name: str
    role: str
    can_answer_fact: bool = False
    risk: RiskLevel = "medium"
    recommended_next_tool: str = ""
    why: str = ""
    url: str = ""
    caveat: str = ""


class SourceLayer(BaseModel):
    layer: LayerName
    label: str
    sources: list[SourceEntry] = Field(default_factory=list)


class RouteSubjectSourcesResult(BaseModel):
    subject: dict[str, Any] = Field(default_factory=dict)
    subject_type: SubjectTypeName
    intent: SourceIntent
    source_layers: dict[str, list[SourceEntry]] = Field(default_factory=dict)
    recommended_tools: list[str] = Field(default_factory=list)
    blocked_uses: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    decision: str = ""


def _image(raw: dict[str, Any]) -> str:
    images = raw.get("images") or {}
    return images.get("common") or images.get("medium") or images.get("grid") or ""


def _subject_type_name(value: int | None) -> SubjectTypeName | None:
    return {1: "book", 2: "anime", 3: "music", 4: "game", 6: "real"}.get(value or 0)


def _title(raw: dict[str, Any]) -> str:
    return str(raw.get("name_cn") or raw.get("name") or f"subject {raw.get('id')}")


def _tags(raw: dict[str, Any]) -> list[str]:
    return [str(t.get("name")) for t in raw.get("tags") or [] if isinstance(t, dict) and t.get("name")][:20]


async def _resolve_subject(client: BangumiClient, args: SourceRouterArgs) -> dict[str, Any]:
    if args.subject_id:
        try:
            raw = await client.get_subject(args.subject_id)
            return {
                "id": raw.get("id"),
                "name": _title(raw),
                "name_jp": raw.get("name") or "",
                "type_name": _subject_type_name(raw.get("type")) or args.subject_type,
                "score": (raw.get("rating") or {}).get("score"),
                "rank": (raw.get("rating") or {}).get("rank"),
                "date": raw.get("date") or "",
                "tags": _tags(raw),
                "image": _image(raw),
                "url": f"https://bgm.tv/subject/{raw.get('id')}",
            }
        except Exception:  # noqa: BLE001
            return {"id": args.subject_id, "name": args.title, "type_name": args.subject_type, "tags": []}
    if args.title.strip():
        try:
            raw = await client.search_subjects(args.title, SUBJECT_TYPE.get(args.subject_type), limit=5)
            rows = raw.get("data") or []
            if rows:
                first = rows[0]
                return {
                    "id": first.get("id"),
                    "name": _title(first),
                    "name_jp": first.get("name") or "",
                    "type_name": _subject_type_name(first.get("type")) or args.subject_type,
                    "score": first.get("score") or ((first.get("rating") or {}).get("score")),
                    "rank": (first.get("rating") or {}).get("rank"),
                    "date": first.get("date") or "",
                    "tags": _tags(first),
                    "image": _image(first),
                    "url": f"https://bgm.tv/subject/{first.get('id')}",
                }
        except Exception:  # noqa: BLE001
            pass
    return {"name": args.title.strip(), "type_name": args.subject_type, "tags": []}


def _entry(
    name: str,
    role: str,
    *,
    fact: bool = False,
    risk: RiskLevel = "medium",
    tool: str = "",
    why: str = "",
    url: str = "",
    caveat: str = "",
) -> SourceEntry:
    return SourceEntry(
        name=name,
        role=role,
        can_answer_fact=fact,
        risk=risk,
        recommended_next_tool=tool,
        why=why,
        url=url,
        caveat=caveat,
    )


def _base_layers(subject_type: str, intent: str, tags: list[str]) -> dict[str, list[SourceEntry]]:
    if subject_type == "anime":
        return {
            "canonical": [
                _entry("Bangumi", "作品、人物、staff、评分、关系、分集 canonical 锚点", fact=True, risk="low", tool="get_subject / get_subject_persons / get_subject_episodes"),
            ],
            "metadata": [
                _entry("yuc.wiki", "季度新番表、放送、官网/PV、制作阵容辅助", fact=False, risk="medium", tool="list_yuc_season"),
                _entry("AnimeThemes", "OP/ED 曲名、艺人和视频入口", fact=False, risk="low", tool="anime_music_themes"),
                _entry("AniList", "英文圈热度/评分辅助", fact=False, risk="medium", tool="search_anilist"),
            ],
            "reputation": [
                _entry("Bangumi 评分/短评/分集讨论", "中文圈口碑、分集热度和短评摘要", fact=False, risk="medium", tool="review_subject / get_episode_comments"),
            ],
            "discourse": [
                _entry("B站导视/漫评白名单", "UP 导视、评论区期待/担心点", fact=False, risk="high", tool="find_guide_videos / summarize_bilibili_video_content"),
                _entry("论坛/贴吧/网页 URL 摘要", "显式 URL 的话语源摘要", fact=False, risk="high", tool="fetch_url_summary / browser_fetch_summary"),
            ],
            "navigation": [
                _entry("正版观看入口", "官方/正版平台入口", fact=False, risk="medium", tool="where_to_watch"),
                _entry("Mikan/DMHY/ACGNX/VCB", "RSS/资源搜索导航，不下载不托管", fact=False, risk="high", tool="get_anime_release_feeds"),
            ],
        }
    if subject_type == "game":
        return {
            "canonical": [
                _entry("Bangumi game", "中文圈主锚点：条目、标签、收藏、短评", fact=True, risk="low", tool="search_subjects / get_subject"),
                _entry("VNDB", "VN 别名、发售、开发商、国际 VN 圈元数据", fact=True, risk="low", tool="search_visual_novels"),
            ],
            "metadata": [
                _entry("VNDB releases/tags", "发售日、别名、tag/trait 辅助", fact=False, risk="medium", tool="search_visual_novels"),
            ],
            "reputation": [
                _entry("Bangumi game 评分/短评", "中文圈口碑", fact=False, risk="medium", tool="review_subject"),
                _entry("批判空间 / ErogameScape", "日本 gal 圈中央值、平均值、排名、Data 数", fact=False, risk="medium", tool="search_erogamescape / rank_erogamescape"),
                _entry("VNDB vote", "国际 VN 圈评分辅助", fact=False, risk="medium", tool="search_visual_novels"),
            ],
            "discourse": [
                _entry("绯月/月幕/galgame 吧 URL 摘要", "需要用户给 URL 或 web 兜底，不能当事实源", risk="high", tool="fetch_url_summary / web_search"),
            ],
            "navigation": [
                _entry("Steam / DLsite / FANZA / 批判空间入口", "购买/资料导航；R18 与区域限制需提示", risk="high", tool="get_vertical_links / web_search"),
            ],
        }
    if subject_type == "book":
        return {
            "canonical": [
                _entry("Bangumi book", "漫画/轻小说/小说主锚点，需结合 tags 区分 subtype", fact=True, risk="low", tool="search_subjects / get_subject"),
            ],
            "metadata": [
                _entry("Google Books / Open Library / ISBN", "封面、ISBN、作者、出版元数据辅助", fact=False, risk="medium", tool="route_image_source / extract_visual_text"),
                _entry("MangaDex", "漫画英文圈条目和章节入口，仅辅助", fact=False, risk="medium", tool="route_image_source"),
            ],
            "reputation": [
                _entry("Bangumi book 评分/短评", "中文圈口碑", risk="medium", tool="review_subject"),
            ],
            "discourse": [
                _entry("轻之国度/真白萌/S1/NGA/贴吧 URL 摘要", "显式 URL 的讨论摘要，可能剧透", risk="high", tool="fetch_url_summary / browser_fetch_summary"),
            ],
            "navigation": [
                _entry("BOOK☆WALKER / Amazon / B漫 / MangaDex", "购买或阅读入口导航，不代表可读内容托管", risk="medium", tool="get_vertical_links / web_search"),
            ],
        }
    if subject_type == "music":
        return {
            "canonical": [
                _entry("Bangumi music", "音乐条目、关联动画/角色歌/OST 的社区锚点", fact=True, risk="low", tool="search_subjects / get_subject_relations"),
            ],
            "metadata": [
                _entry("AnimeThemes", "动画 OP/ED 曲名、艺人、视频入口", risk="low", tool="anime_music_themes / search_anime_themes"),
                _entry("MusicBrainz", "专辑、艺人、发行、曲目元数据", risk="low", tool="search_musicbrainz"),
                _entry("AniSongDB", "动画歌曲元数据候选，后续可接", risk="medium", caveat="当前未实现专门工具"),
            ],
            "reputation": [
                _entry("Bangumi music 评分/短评", "音乐条目的中文圈口碑", risk="medium", tool="review_subject"),
            ],
            "discourse": [
                _entry("网易云/QQ音乐评论", "只在用户显式 URL 时摘要；默认不抓评论区", risk="high", tool="fetch_url_summary"),
            ],
            "navigation": [
                _entry("网易云 / QQ音乐 / YouTube / B站搜索", "收听/二创入口导航，不作为事实源", risk="medium", tool="get_vertical_links / find_related_videos"),
            ],
        }
    return {
        "canonical": [_entry("Bangumi real", "三次元条目的 Bangumi 社区锚点", fact=True, risk="low", tool="search_subjects / get_subject")],
        "metadata": [_entry("官方站 / TMDB / IMDb", "三次元元数据可后续接入", risk="medium", tool="web_search")],
        "reputation": [_entry("Bangumi real 评分/短评", "社区口碑", risk="medium", tool="review_subject")],
        "discourse": [_entry("web/论坛 URL 摘要", "话语源，需标注来源", risk="high", tool="fetch_url_summary / web_search")],
        "navigation": [_entry("官方入口/流媒体搜索", "外链导航", risk="medium", tool="where_to_watch / web_search")],
    }


def _intent_tools(subject_type: str, intent: str, tags: list[str]) -> list[str]:
    if intent == "fact":
        return ["search_subjects", "get_subject", "get_subject_persons", "get_subject_relations"]
    if intent == "review":
        tools = ["review_subject"]
        if subject_type == "game":
            tools += ["search_erogamescape", "search_visual_novels"]
        if subject_type == "anime":
            tools += ["get_subject_comments", "get_subject_episodes"]
        return tools
    if intent == "recommendation":
        return ["recommend_subjects"]
    if intent == "guide":
        return ["season_guide_brief"] if subject_type == "anime" else ["recommend_subjects", "get_vertical_links"]
    if intent == "resource":
        return ["where_to_watch", "get_anime_release_feeds"] if subject_type == "anime" else ["get_vertical_links", "web_search"]
    if intent == "image":
        return ["route_image_source", "extract_visual_text", "search_image_source"]
    if intent == "music":
        return ["anime_music_themes", "search_musicbrainz", "search_anime_themes"]
    return ["fetch_url_summary", "browser_fetch_summary", "web_search"]


def _blocked_uses(subject_type: str, intent: str) -> list[str]:
    blocked = [
        "不要把导航链接当事实证据。",
        "不要把 B站评论、论坛楼、音乐平台评论当 canonical 事实。",
        "没有工具命中时，不要声称某 UP / 某站已经评价过具体作品。",
    ]
    if subject_type == "game":
        blocked.append("不要把 VNDB/批判空间评分说成 Bangumi 评分；三圈层必须分开。")
    if subject_type == "music":
        blocked.append("MusicBrainz/AnimeThemes 是元数据源，不是口碑源。")
    if intent in {"review", "discourse"}:
        blocked.append("外部话语源默认高剧透风险，未授权时只能做无剧透摘要。")
    return blocked


def _decision(subject_type: str, intent: str) -> str:
    if subject_type == "anime" and intent == "guide":
        return "先用 Bangumi/yuc 定季番列表，再按作品标签路由到百合/芳文/数据向/泛用导视源。"
    if subject_type == "game":
        return "Bangumi game 做中文圈主锚点，批判空间补日本 gal 口碑，VNDB 补国际元数据与评分。"
    if subject_type == "book":
        return "先用 Bangumi book 定锚并区分漫画/轻小说/小说，再按显式 URL 或 OCR 进入外部源。"
    if subject_type == "music":
        return "Bangumi music 是社区锚点；OP/ED 用 AnimeThemes，专辑/艺人元数据用 MusicBrainz。"
    return "先取 canonical 锚点，再按 intent 补 metadata/reputation/discourse/navigation。"


class RouteSubjectSourcesTool(Tool):
    name = "route_subject_sources"
    description = (
        "显性化跨媒介信息源路由：根据 subject_type 和 intent 返回 canonical/metadata/reputation/"
        "discourse/navigation 分层、推荐工具、禁用用法和风险。用于解释为何选择 Bangumi/EGS/VNDB/"
        "yuc/AnimeThemes/论坛/资源站等来源；不要作为普通查询的前置分发器。"
    )
    args_model = SourceRouterArgs
    result_model = RouteSubjectSourcesResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SourceRouterArgs) -> ToolResult[RouteSubjectSourcesResult]:
        subject = await _resolve_subject(self.client, args)
        stype = subject.get("type_name") or args.subject_type
        if stype not in {"anime", "book", "music", "game", "real"}:
            stype = args.subject_type
        tags = subject.get("tags") or []
        layers = _base_layers(stype, args.intent, tags)
        if args.intent == "resource" and stype != "anime":
            layers["navigation"].append(
                _entry("搜索引擎定向查询", "非动画资源/购买入口先做导航，不直接聚合下载内容", risk="high", tool="web_search")
            )
        result = RouteSubjectSourcesResult(
            subject=subject,
            subject_type=stype,
            intent=args.intent,
            source_layers=layers,
            recommended_tools=_intent_tools(stype, args.intent, tags),
            blocked_uses=_blocked_uses(stype, args.intent),
            caveats=[
                "source router 只决定来源策略，不直接证明事实。",
                "后续回答仍需调用 recommended_tools 取真实证据。",
                "discourse/navigation 层默认不参与 canonical 事实判断。",
            ],
            decision=_decision(stype, args.intent),
        )
        sources = []
        if subject.get("id"):
            sources.append(Citation(title=subject.get("name") or "Bangumi subject", url=f"https://bgm.tv/subject/{subject['id']}", source="bangumi", image=subject.get("image")))
        return ToolResult(ok=True, data=result, sources=sources)


def build_source_router_tools(client: BangumiClient) -> list[Tool]:
    return [RouteSubjectSourcesTool(client)]
