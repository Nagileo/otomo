"""统一评价融合工具。

把不同来源的评分/口碑证据规整到同一 schema。它不替代 agent 的最终表达，
而是给最终总结提供“共识/分歧/置信度/剧透边界”的结构化底稿。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ..anilist.tool import AniListArgs, SearchAniListTool
from ..bangumi.client import BangumiClient
from ..bangumi.models import SUBJECT_TYPE_NAME, SubjectDetail
from ..comments.tool import CommentsArgs, GetCommentsTool
from ..erogamescape.tool import EGSArgs, SearchErogameScapeTool
from ..musicbrainz.tool import MusicBrainzArgs, SearchMusicBrainzTool
from ..vndb.tool import SearchVNTool, VNSearchArgs
from ..spoiler.tool import SpoilerLevel


class ReviewSubjectArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi 条目 ID")
    title_hint: str | None = Field(None, description="可选标题，game/galgame 外部源检索时优先使用")
    focus: str | None = Field(None, description="评价关注点，如 作画/剧情/音乐/节奏/结局")
    spoiler_level: SpoilerLevel = Field("none", description="剧透等级；默认 none")
    include_comments: bool = Field(True, description="是否尝试 Bangumi 短评；无剧透模式默认隐藏原文样本")


class RatingEvidence(BaseModel):
    source: str
    score: float | None = None
    scale: int | None = None
    rank: int | None = None
    count: int | None = None
    signal: Literal["strong", "positive", "mixed", "weak", "low_data", "unknown"] = "unknown"
    note: str = ""
    url: str | None = None


class CommentEvidence(BaseModel):
    source: str
    samples: list[str] = Field(default_factory=list)
    hidden_for_spoiler: bool = False
    note: str = ""
    url: str | None = None


class ReviewAspect(BaseModel):
    aspect: Literal["praise", "criticism"]
    source: str
    points: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"


class AspectOpinion(BaseModel):
    aspect: Literal["story", "character", "pacing", "visual", "music", "direction", "text", "system", "voice", "general"]
    sentiment: Literal["positive", "negative", "mixed"]
    source: str
    evidence_snippet: str
    spoiler_risk: Literal["low", "medium", "high"] = "low"
    confidence: Literal["low", "medium", "high"] = "low"


class AspectSummary(BaseModel):
    aspect: Literal["story", "character", "pacing", "visual", "music", "direction", "text", "system", "voice", "general"]
    label: str
    positive: int = 0
    negative: int = 0
    mixed: int = 0
    total: int = 0
    dominant_sentiment: Literal["positive", "negative", "mixed"] = "mixed"
    spoiler_risk: Literal["low", "medium", "high"] = "low"
    confidence: Literal["low", "medium", "high"] = "low"
    sources: list[str] = Field(default_factory=list)
    sample_snippets: list[str] = Field(default_factory=list)


class SourceAvailability(BaseModel):
    source: str
    role: str
    status: Literal["used", "hidden", "unavailable", "link_only"] = "used"
    note: str = ""


class SourceGroup(BaseModel):
    group: str
    role: str
    sources: list[str] = Field(default_factory=list)
    consensus: str = ""
    confidence: Literal["low", "medium", "high"] = "low"


class ReviewFusionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject_id: int
    title: str
    subject_type: str
    spoiler_level: SpoilerLevel
    ratings: list[RatingEvidence] = Field(default_factory=list)
    comments: list[CommentEvidence] = Field(default_factory=list)
    praise: list[ReviewAspect] = Field(default_factory=list)
    criticism: list[ReviewAspect] = Field(default_factory=list)
    aspect_opinions: list[AspectOpinion] = Field(default_factory=list)
    aspect_summary: list[AspectSummary] = Field(default_factory=list)
    consensus: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    caveats: list[str] = Field(default_factory=list)
    source_matrix: list[SourceAvailability] = Field(default_factory=list)
    source_groups: list[SourceGroup] = Field(default_factory=list)
    source_routing_notes: list[str] = Field(default_factory=list)
    suggested_summary_points: list[str] = Field(default_factory=list)


def _bangumi_signal(score: float | None, total: int | None, count: dict | None) -> str:
    if not score:
        return "unknown"
    if total is not None and total < 30:
        return "low_data"
    if count:
        high = sum(int(count.get(str(i), count.get(i, 0)) or 0) for i in (8, 9, 10))
        low = sum(int(count.get(str(i), count.get(i, 0)) or 0) for i in range(1, 6))
        base = max(sum(int(v or 0) for v in count.values()), 1)
        if high / base >= 0.35 and low / base >= 0.25:
            return "mixed"
    if score >= 8:
        return "strong"
    if score >= 7:
        return "positive"
    if score < 6:
        return "weak"
    return "mixed"


def _score_signal(score: float | None, total: int | None, scale: int) -> str:
    if score is None:
        return "unknown"
    if total is not None and total < 30:
        return "low_data"
    normalized = score / scale * 10
    if normalized >= 8:
        return "strong"
    if normalized >= 7:
        return "positive"
    if normalized < 6:
        return "weak"
    return "mixed"


def _consensus(ratings: list[RatingEvidence]) -> str:
    reliable = [r for r in ratings if r.signal not in {"low_data", "unknown"}]
    if not reliable:
        return "有效评分证据不足，适合只做低置信度参考。"
    positives = [r for r in reliable if r.signal in {"strong", "positive"}]
    weak = [r for r in reliable if r.signal in {"weak", "mixed"}]
    if len(positives) >= 2 and not weak:
        return "多源口碑偏正向，可以作为较稳的推荐依据。"
    if positives and not weak:
        return "现有评分口碑偏正向；若只有单一来源，建议结合短评或用户偏好再下结论。"
    if positives and weak:
        return "不同圈层或来源存在分歧，需要把优点和争议分开讲。"
    if weak and not positives:
        return "现有口碑偏谨慎，建议降低推荐强度或等待更多反馈。"
    return "口碑信号中性，需要结合用户偏好判断。"


def _rating_by_source(ratings: list[RatingEvidence], source: str) -> RatingEvidence | None:
    return next((r for r in ratings if r.source == source), None)


def _rating_phrase(r: RatingEvidence | None) -> str:
    if r is None:
        return "未命中"
    score = f"{r.score:g}/{r.scale}" if r.score is not None and r.scale else "暂无分"
    count = f"，{r.count} 样本" if r.count else ""
    return f"{score}{count}，{r.signal}"


def _galgame_source_groups(ratings: list[RatingEvidence]) -> tuple[list[SourceGroup], list[str], str]:
    bgm = _rating_by_source(ratings, "Bangumi")
    egs = _rating_by_source(ratings, "ErogameScape/批判空间")
    vndb = _rating_by_source(ratings, "VNDB")
    groups = [
        SourceGroup(
            group="中文圈 / Bangumi",
            role="中文用户收藏、评分与条目锚点",
            sources=["Bangumi"] if bgm else [],
            consensus=_rating_phrase(bgm),
            confidence="high" if bgm and (bgm.count or 0) >= 100 else ("medium" if bgm else "low"),
        ),
        SourceGroup(
            group="日本 gal 圈 / 批判空间",
            role="中央値、平均值、Data 数与 gal 圈口碑",
            sources=["ErogameScape/批判空间"] if egs else [],
            consensus=_rating_phrase(egs),
            confidence="high" if egs and (egs.count or 0) >= 80 else ("medium" if egs else "low"),
        ),
        SourceGroup(
            group="国际 VN 圈 / VNDB",
            role="国际视觉小说评分、别名与发售信息",
            sources=["VNDB"] if vndb else [],
            consensus=_rating_phrase(vndb),
            confidence="high" if vndb and (vndb.count or 0) >= 200 else ("medium" if vndb else "low"),
        ),
    ]
    notes = [
        "galgame 评价优先区分三个圈层：Bangumi=中文圈，批判空间=日本 gal 圈，VNDB=国际 VN 圈。",
        "事实锚点仍以 Bangumi subject 为准；外部源只作为口碑/别名/发售补充。",
    ]
    available = [x for x in (bgm, egs, vndb) if x is not None and x.signal not in {"unknown", "low_data"}]
    if len(available) >= 2:
        positives = [x for x in available if x.signal in {"strong", "positive"}]
        weak = [x for x in available if x.signal in {"mixed", "weak"}]
        if positives and not weak:
            consensus = "galgame 三源可用且整体偏正向；推荐时可作为强口碑证据。"
        elif positives and weak:
            consensus = "galgame 三圈层存在分歧；需要分开说明中文圈、日本 gal 圈与国际 VN 圈的差异。"
        else:
            consensus = "galgame 外部圈层口碑偏谨慎；推荐强度应降低。"
    else:
        consensus = "galgame 三源证据不足；以 Bangumi game 条目为主，外部源仅作弱补充。"
    return groups, notes, consensus


_PRAISE_HINTS = (
    "喜欢", "好看", "优秀", "神", "佳作", "名作", "有趣", "治愈", "舒服", "感动", "稳定", "精彩", "推荐", "良作",
    "期待", "好耶", "稳", "牛", "来了", "泪目", "可以", "香", "想看",
)
_CRITIC_HINTS = (
    "不喜欢", "差", "烂", "崩", "无聊", "拖", "尬", "雷", "失望", "问题", "一般", "劝退", "节奏", "作画崩", "难受",
    "寄", "难绷", "不行", "翻车", "爆雷", "担心", "怕", "崩坏",
)

_ASPECT_HINTS: dict[str, tuple[str, ...]] = {
    "story": ("剧情", "故事", "脚本", "展开", "结局", "反转", "叙事", "主线"),
    "character": ("角色", "人设", "人物", "塑造", "关系", "感情线", "CP"),
    "pacing": ("节奏", "慢热", "拖", "赶", "日常", "单元回"),
    "visual": ("作画", "画面", "美术", "摄影", "分镜", "演出", "崩", "打戏"),
    "music": ("音乐", "配乐", "OP", "ED", "插曲", "音响", "歌"),
    "direction": ("监督", "导演", "演出", "构成", "制作", "改编"),
    "text": ("文本", "文笔", "台词", "剧本", "翻译", "描写"),
    "system": ("系统", "玩法", "战斗", "UI", "养成", "手感", "数值"),
    "voice": ("声优", "配音", "CV", "演技"),
}
_SPOILER_HINTS = ("结局", "反转", "真相", "黑幕", "凶手", "死", "后面", "后期", "终章")
_ASPECT_LABELS = {
    "story": "剧情",
    "character": "角色",
    "pacing": "节奏",
    "visual": "画面/作画",
    "music": "音乐",
    "direction": "制作/演出",
    "text": "文本",
    "system": "系统/玩法",
    "voice": "声优",
    "general": "整体观感",
}
_SENTIMENT_LABELS = {"positive": "正向", "negative": "负向", "mixed": "分歧"}
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _aspect_confidence(n: int) -> str:
    if n >= 4:
        return "high"
    if n >= 2:
        return "medium"
    return "low"


def _comment_sentiment(text: str) -> str | None:
    pos = any(k in text for k in _PRAISE_HINTS)
    neg = any(k in text for k in _CRITIC_HINTS)
    if pos and neg:
        return "mixed"
    if pos:
        return "positive"
    if neg:
        return "negative"
    return None


def _spoiler_risk(text: str) -> str:
    if any(k in text for k in _SPOILER_HINTS):
        return "high"
    if any(k in text for k in ("剧情", "故事", "后半", "后续")):
        return "medium"
    return "low"


def _extract_aspect_opinions(comments: list[CommentEvidence]) -> list[AspectOpinion]:
    opinions: list[AspectOpinion] = []
    seen: set[tuple[str, str, str]] = set()
    for c in comments:
        if c.hidden_for_spoiler:
            continue
        for sample in c.samples:
            text = sample.strip()
            if not text:
                continue
            sentiment = _comment_sentiment(text)
            if not sentiment:
                continue
            hit_aspects = [
                aspect for aspect, keys in _ASPECT_HINTS.items()
                if any(k in text for k in keys)
            ] or ["general"]
            for aspect in hit_aspects[:3]:
                key = (aspect, sentiment, text[:80])
                if key in seen:
                    continue
                seen.add(key)
                opinions.append(
                    AspectOpinion(
                        aspect=aspect,  # type: ignore[arg-type]
                        sentiment=sentiment,  # type: ignore[arg-type]
                        source=c.source,
                        evidence_snippet=text[:160],
                        spoiler_risk=_spoiler_risk(text),  # type: ignore[arg-type]
                        confidence="medium" if aspect != "general" else "low",
                    )
                )
    return opinions[:12]


def _build_aspect_summary(opinions: list[AspectOpinion]) -> list[AspectSummary]:
    grouped: dict[str, list[AspectOpinion]] = {}
    for op in opinions:
        grouped.setdefault(op.aspect, []).append(op)
    out: list[AspectSummary] = []
    for aspect, items in grouped.items():
        positive = sum(1 for x in items if x.sentiment == "positive")
        negative = sum(1 for x in items if x.sentiment == "negative")
        mixed = sum(1 for x in items if x.sentiment == "mixed")
        counts = {"positive": positive, "negative": negative, "mixed": mixed}
        dominant = max(counts.items(), key=lambda kv: (kv[1], kv[0] == "mixed"))[0]
        if positive and negative and abs(positive - negative) <= 1:
            dominant = "mixed"
        risk = max((x.spoiler_risk for x in items), key=lambda x: _RISK_ORDER[x], default="low")
        sources = list(dict.fromkeys(x.source for x in items if x.source))
        snippets = list(dict.fromkeys(x.evidence_snippet for x in items if x.evidence_snippet))[:3]
        confidence = "high" if len(items) >= 5 and len(sources) >= 2 else ("medium" if len(items) >= 2 else "low")
        out.append(
            AspectSummary(
                aspect=aspect,  # type: ignore[arg-type]
                label=_ASPECT_LABELS.get(aspect, aspect),
                positive=positive,
                negative=negative,
                mixed=mixed,
                total=len(items),
                dominant_sentiment=dominant,  # type: ignore[arg-type]
                spoiler_risk=risk,  # type: ignore[arg-type]
                confidence=confidence,
                sources=sources,
                sample_snippets=snippets,
            )
        )
    out.sort(key=lambda x: (-x.total, x.label))
    return out[:8]


def _format_aspect_summary(summary: list[AspectSummary]) -> list[str]:
    return [
        f"{s.label}：{_SENTIMENT_LABELS.get(s.dominant_sentiment, s.dominant_sentiment)}"
        f"（+{s.positive}/-{s.negative}/±{s.mixed}，{s.confidence}）"
        for s in summary[:6]
    ]


def _pick_aspects(comments: list[CommentEvidence]) -> tuple[list[ReviewAspect], list[ReviewAspect]]:
    praise: list[str] = []
    criticism: list[str] = []
    for c in comments:
        for sample in c.samples:
            text = sample.strip()
            if not text:
                continue
            if any(k in text for k in _PRAISE_HINTS):
                praise.append(text[:140])
            if any(k in text for k in _CRITIC_HINTS):
                criticism.append(text[:140])
    out_praise = [
        ReviewAspect(
            aspect="praise", source="Bangumi 短评", points=list(dict.fromkeys(praise))[:5],
            confidence=_aspect_confidence(len(praise)),
        )
    ] if praise else []
    out_criticism = [
        ReviewAspect(
            aspect="criticism", source="Bangumi 短评", points=list(dict.fromkeys(criticism))[:5],
            confidence=_aspect_confidence(len(criticism)),
        )
    ] if criticism else []
    return out_praise, out_criticism


def _confidence(ratings: list[RatingEvidence], comments: list[CommentEvidence]) -> str:
    reliable = [r for r in ratings if r.signal not in {"unknown", "low_data"}]
    comment_samples = sum(len(c.samples) for c in comments)
    if len(reliable) >= 2 and any((r.count or 0) >= 100 for r in reliable):
        return "high"
    if reliable or comment_samples >= 3:
        return "medium"
    return "low"


def _summary_points(ratings: list[RatingEvidence], comments: list[CommentEvidence]) -> list[str]:
    points: list[str] = []
    for r in ratings:
        if r.signal in {"strong", "positive"}:
            points.append(f"{r.source} 口碑为 {r.signal}，可作为正向证据。")
        elif r.signal in {"mixed", "weak"}:
            points.append(f"{r.source} 信号为 {r.signal}，回答时要说明争议。")
    if any(c.samples for c in comments):
        points.append("短评样本可用于提炼具体夸点/吐槽点，但要标注来源。")
    if any(c.hidden_for_spoiler for c in comments):
        points.append("当前为无剧透模式，短评原文已隐藏，最终回答不要展开剧情细节。")
    return points[:6]


class ReviewSubjectTool(Tool):
    name = "review_subject"
    description = (
        "统一融合某作品的评价证据：Bangumi 评分/分布、短评样本，以及 game/galgame 的批判空间/VNDB。"
        "用于『如何评价/好不好/适合我吗』。默认无剧透，会隐藏短评原文；需要剧情评价先 assess_spoiler_policy。"
    )
    args_model = ReviewSubjectArgs
    result_model = ReviewFusionResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.comments = GetCommentsTool()
        self.egs = SearchErogameScapeTool()
        self.vndb = SearchVNTool()
        self.anilist = SearchAniListTool()
        self.musicbrainz = SearchMusicBrainzTool()

    async def run(self, args: ReviewSubjectArgs) -> ToolResult[ReviewFusionResult]:
        raw = await self.client.get_subject(args.subject_id)
        detail = SubjectDetail.from_raw(raw)
        title = args.title_hint or detail.name_cn or detail.name
        subject_type = SUBJECT_TYPE_NAME.get(detail.type or 0, str(detail.type or "unknown"))

        ratings: list[RatingEvidence] = []
        extra_sources: list[Citation] = []
        source_matrix: list[SourceAvailability] = [
            SourceAvailability(source="Bangumi", role="中文圈条目/评分/收藏锚点", status="used")
        ]
        if detail.rating:
            ratings.append(
                RatingEvidence(
                    source="Bangumi",
                    score=detail.rating.score,
                    scale=10,
                    rank=detail.rating.rank,
                    count=detail.rating.total,
                    signal=_bangumi_signal(detail.rating.score, detail.rating.total, detail.rating.count),
                    note="中文圈收藏/评分口碑；rating.count 可判断两极分化。",
                    url=f"https://bgm.tv/subject/{detail.id}",
                )
            )

        comments: list[CommentEvidence] = []
        if args.include_comments:
            if args.spoiler_level == "none":
                comments.append(
                    CommentEvidence(
                        source="Bangumi 短评",
                        hidden_for_spoiler=True,
                        note="无剧透模式下不返回短评原文，避免短评剧透。",
                        url=f"https://bgm.tv/subject/{detail.id}/comments",
                    )
                )
                source_matrix.append(
                    SourceAvailability(source="Bangumi 短评", role="话语/口碑样本", status="hidden", note="无剧透模式隐藏原文")
                )
            else:
                cres = await self.comments.run(CommentsArgs(subject_id=detail.id, query=args.focus, limit=8))
                if cres.ok and cres.data:
                    comments.append(
                        CommentEvidence(
                            source="Bangumi 短评",
                            samples=cres.data.comments,
                            note="用户短评样本，适合提炼夸点/吐槽点。",
                            url=f"https://bgm.tv/subject/{detail.id}/comments",
                        )
                    )
                    source_matrix.append(
                        SourceAvailability(source="Bangumi 短评", role="话语/口碑样本", status="used")
                    )

        if detail.type == 2:  # anime
            anilist = await self.anilist.run(AniListArgs(keyword=detail.name or title, type="anime", limit=3))
            if anilist.ok and anilist.data and anilist.data.results:
                item = anilist.data.results[0]
                ratings.append(
                    RatingEvidence(
                        source="AniList",
                        score=item.score,
                        scale=100,
                        count=None,
                        signal=_score_signal(item.score, None, 100),
                        note="英文圈动画评分，满分 100；用 Bangumi 日文原名检索。",
                        url=f"https://anilist.co/anime/{item.id}",
                    )
                )
                source_matrix.append(SourceAvailability(source="AniList", role="英文圈动画评分/别名", status="used"))

        if detail.type == 1:  # book / manga / novel
            anilist = await self.anilist.run(AniListArgs(keyword=detail.name or title, type="manga", limit=3))
            if anilist.ok and anilist.data and anilist.data.results:
                item = anilist.data.results[0]
                ratings.append(
                    RatingEvidence(
                        source="AniList",
                        score=item.score,
                        scale=100,
                        count=None,
                        signal=_score_signal(item.score, None, 100),
                        note="英文圈漫画/书籍条目评分，满分 100；轻小说可能覆盖不完整。",
                        url=f"https://anilist.co/manga/{item.id}",
                    )
                )
                source_matrix.append(SourceAvailability(source="AniList", role="英文圈漫画/书籍评分/别名", status="used"))

        if detail.type == 4:  # game / galgame
            egs = await self.egs.run(EGSArgs(keyword=title, limit=3))
            if egs.ok and egs.data and egs.data.results:
                item = egs.data.results[0]
                ratings.append(
                    RatingEvidence(
                        source="ErogameScape/批判空间",
                        score=item.median,
                        scale=100,
                        count=item.vote_count,
                        signal=_score_signal(item.median, item.vote_count, 100),
                        note=f"日本 galgame 圈中央值；品牌 {item.brand or '未知'}。",
                        url=item.url,
                    )
                )
                source_matrix.append(SourceAvailability(source="ErogameScape/批判空间", role="日本 gal 圈评分", status="used"))
            vn = await self.vndb.run(VNSearchArgs(keyword=title, limit=3))
            if vn.ok and vn.data and vn.data.results:
                item = vn.data.results[0]
                ratings.append(
                    RatingEvidence(
                        source="VNDB",
                        score=item.rating,
                        scale=100,
                        count=item.votecount,
                        signal=_score_signal(item.rating, item.votecount, 100),
                        note="国际 VN 圈评分，满分 100。",
                        url=f"https://vndb.org/{item.id}",
                    )
                )
                source_matrix.append(SourceAvailability(source="VNDB", role="国际 VN 圈评分/别名/发售日", status="used"))

        if detail.type == 3:
            mb = await self.musicbrainz.run(MusicBrainzArgs(keyword=detail.name or title, limit=3))
            extra_sources.extend(mb.sources)
            if mb.ok and mb.data and mb.data.results:
                first = mb.data.results[0]
                note_bits = [first.title]
                if first.artist:
                    note_bits.append(first.artist)
                if first.first_release_date:
                    note_bits.append(first.first_release_date)
                source_matrix.append(
                    SourceAvailability(
                        source="MusicBrainz",
                        role="音乐元数据/发行信息",
                        status="used",
                        note=" / ".join(note_bits),
                    )
                )
            else:
                source_matrix.append(
                    SourceAvailability(
                        source="MusicBrainz",
                        role="音乐元数据/发行信息",
                        status="unavailable",
                        note=mb.error or "未检索到匹配条目",
                    )
                )
            source_matrix.append(SourceAvailability(source="音乐平台评论", role="音乐评论/歌单口碑", status="link_only", note="当前仅导航，不抓评论"))
        if detail.type == 6:
            source_matrix.append(SourceAvailability(source="Web/官方信息", role="三次元补充信息", status="link_only", note="当前以 Bangumi 和 web_search 兜底"))

        caveats = []
        if args.spoiler_level == "none":
            caveats.append("无剧透模式：不要展开结局、反转、后期真相。")
        if detail.type == 4:
            has_vn_evidence = any(r.source in {"ErogameScape/批判空间", "VNDB"} for r in ratings)
            if has_vn_evidence:
                caveats.append("galgame/视觉小说评价需区分中文 Bangumi、日本批判空间、国际 VNDB 三个圈层。")
            else:
                caveats.append("本作未在批判空间/VNDB 命中，可能不是 galgame/视觉小说；gal 圈外部评分不适用，以 Bangumi game 数据为准。")
        source_groups: list[SourceGroup] = []
        source_routing_notes: list[str] = []
        consensus = _consensus(ratings)
        if detail.type == 4:
            source_groups, source_routing_notes, consensus = _galgame_source_groups(ratings)
        praise, criticism = _pick_aspects(comments)
        aspect_opinions = _extract_aspect_opinions(comments)
        aspect_summary = _build_aspect_summary(aspect_opinions)

        result = ReviewFusionResult(
            subject_id=detail.id,
            title=title,
            subject_type=subject_type,
            spoiler_level=args.spoiler_level,
            ratings=ratings,
            comments=comments,
            praise=praise,
            criticism=criticism,
            aspect_opinions=aspect_opinions,
            aspect_summary=aspect_summary,
            consensus=consensus,
            confidence=_confidence(ratings, comments),
            caveats=caveats,
            source_matrix=source_matrix,
            source_groups=source_groups,
            source_routing_notes=source_routing_notes,
            suggested_summary_points=_summary_points(ratings, comments),
        )
        sources = [Citation(title=f"Bangumi — {title}", url=f"https://bgm.tv/subject/{detail.id}", source="bangumi", image=detail.image)]
        for r in ratings:
            if r.url and r.source != "Bangumi":
                sources.append(Citation(title=f"{r.source} — {title}", url=r.url, source=r.source.lower()))
        sources.extend(extra_sources)
        return ToolResult(ok=True, data=result, sources=sources)


def build_review_tools(client: BangumiClient) -> list[Tool]:
    return [ReviewSubjectTool(client)]
