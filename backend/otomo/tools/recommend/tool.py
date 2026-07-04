"""在线内容推荐工具（B-online，多策略召回 + 平衡打分）。

漏斗（在线只能内容/知识侧——Bangumi API 无跨用户共现）：
  多路召回并集 → 排除已看 → 评分补全 → 统一平衡打分 → 去重+多样性 → top-N。agent 据 reasons 解释。

召回 provider（治"热门已看完"的饱和，见 docs/06 §8）：
  1) 标签召回：口味/心境标签 heat 召回（explore 用次级标签拓展；niche 往热度榜深处挖）
  2) 图谱召回：你最爱作品的监督/制作组/原作 → 他们你没看的其他作品（强信号、最契合 LLM+图谱）

打分（已平衡，避免图谱压过标签）：affinity(标签贴合) + 封顶的 graph_bonus + 质量项。
  · 普通：质量=人气×口碑（rank）。  · niche：质量=高分 × 低人气（score 高、投票少）。
评分补全：图谱召回的候选 person_subjects 不带 rating，对 top 候选按需 get_subject 拉评分，才能按质量排。
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...memory import LongTermMemory
from ...memory.models import UserAspectProfile
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..erogamescape.tool import EGSRankArgs, RankErogameScapeTool
from ..review.tool import ReviewSubjectArgs, ReviewSubjectTool, _ASPECT_HINTS, _ASPECT_LABELS

_RECALL_PER_TAG = 50
_MAX_RECALL_TAGS = 8
_MAX_COLLECT = 1000
_NICHE_OFFSET = 30        # 冷门模式：跳过最热门，挖热度榜中尾部
_GRAPH_FAV = 3
_GRAPH_ROLES = {"导演", "监督", "动画制作", "原作", "系列构成", "脚本", "制作"}
_GRAPH_WORKS = 8         # 图谱召回每个 staff/制作组最多取 N 部（防高产制作组霸榜、保多样性）
_ENRICH_TOP = 18         # 对前 N 个缺评分/缺名的候选按需补 get_subject
_CF_SEEDS = 8            # 取用户最爱的前 N 部作协同召回种子
_CF_NBR = 20             # 每个种子取 i2i 的前 N 个邻居
_CF_CAP = 1.5            # cf_bonus 封顶（协同是强个性化信号，略高于 graph 的 1.2）
_SERIES_MAX_HOP = 2      # 系列入口回溯最大级数（S3→S2→S1 这种链）
# 旁支关系（平行作品，不替换、只"提一嘴"）：外传 / 世界观分支 / 番外
_SIDE_REL = {"外传", "相同世界观", "不同世界观", "番外篇"}
_CROSS_MEDIA_REL = {"原作", "改编", "书籍", "漫画", "小说", "游戏", "音乐", "衍生"}
_COLD_START_TAGS: dict[str, list[str]] = {
    "anime": ["日常", "治愈", "恋爱", "奇幻", "搞笑", "百合"],
    "book": ["漫画", "轻小说", "恋爱", "日常", "奇幻"],
    "game": ["galgame", "视觉小说", "恋爱", "治愈", "剧情"],
    "music": ["OST", "OP", "ED", "角色歌"],
    "real": ["日剧", "电影"],
}
_BOOK_SUBTYPE_TAGS: dict[str, list[str]] = {
    "comic": ["漫画", "コミック", "连载", "少年漫画", "少女漫画", "百合漫画"],
    "light_novel": ["轻小说", "ライトノベル", "ラノベ", "文库", "电击文库"],
    "novel": ["小说", "小説", "文学", "单行本"],
}
_MUSIC_SUBTYPE_TAGS: dict[str, list[str]] = {
    "ost": ["OST", "原声", "Soundtrack", "サントラ", "音乐集"],
    "theme_song": ["OP", "ED", "主题歌", "主題歌", "片头曲", "片尾曲"],
    "character_song": ["角色歌", "Character Song", "キャラソン"],
    "artist": ["声优", "歌手", "专辑", "Album", "Single"],
}
_BOOK_SUBTYPE_LABEL = {
    "comic": "漫画",
    "light_novel": "轻小说",
    "novel": "小说",
    "book": "书籍",
}
_MUSIC_SUBTYPE_LABEL = {
    "ost": "OST/原声",
    "theme_song": "OP/ED/主题歌",
    "character_song": "角色歌",
    "artist": "艺人/专辑",
    "music": "音乐",
}

_I2I_CACHE: dict[str, dict] = {}


def _load_i2i(subject_type: str) -> dict:
    """加载 recsys-offline 导出的 item-item 相似度表（离线 CF 反哺在线的产物）。

    按 i2i_{subject_type}.json 找；缺失/损坏→空表，使协同召回这一路静默跳过（优雅降级）。
    进程内缓存，避免每次请求重读。
    """
    if subject_type not in _I2I_CACHE:
        path = os.path.join(settings.cf_i2i_dir, f"i2i_{subject_type}.json")
        try:
            with open(path, encoding="utf-8") as f:
                _I2I_CACHE[subject_type] = json.load(f)
        except (OSError, json.JSONDecodeError):
            _I2I_CACHE[subject_type] = {"items": {}, "meta": {}}
    return _I2I_CACHE[subject_type]

_MOOD_TAG_MAP: dict[str, list[str]] = {
    "不费脑": ["日常", "治愈", "轻松"], "轻松": ["日常", "治愈", "搞笑"],
    "治愈": ["治愈", "日常"], "催泪": ["催泪", "感动"], "感动": ["催泪", "感动"],
    "热血": ["热血", "战斗"], "燃": ["热血", "战斗"], "致郁": ["致郁", "暗黑"],
    "虐": ["致郁", "虐心"], "甜": ["恋爱", "甜"], "搞笑": ["搞笑", "日常"],
    "恐怖": ["恐怖", "惊悚"], "悬疑": ["悬疑", "推理"],
}

_SERIES_RE = re.compile(
    r"(第?[0-9０-９一二三四五六七八九十]+[季期部篇]|Ⅱ|Ⅲ|Ⅳ|Ⅴ|剧场版|总集篇|OVA|OAD|"
    r"Final|完结篇|后篇|前篇|新章|续篇?|\s+|[:：].*$|[0-9０-９]+$)"
)


def _series_key(name: str) -> str:
    return _SERIES_RE.sub("", name or "").strip().lower()


def _norm_title(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalnum())


_SAFE_EDITION_TOKENS = (
    "extendededition", "全年龄版", "通常版", "完全版", "remaster", "remastered", "hd",
    "ps4", "ps3", "psv", "switch", "ns", "pc", "steam", "edition", "版",
)


def _safe_edition_delta(longer: str, shorter: str) -> bool:
    if not longer.startswith(shorter):
        return False
    delta = longer[len(shorter):]
    return bool(delta) and any(tok in delta for tok in _SAFE_EDITION_TOKENS)


def _tag_names(x: dict) -> set[str]:
    out: set[str] = set()
    for t in x.get("tags") or []:
        if isinstance(t, dict):
            name = t.get("name")
        else:
            name = str(t)
        if name:
            out.add(str(name))
    return out


def _candidate_aspects(values: list[str] | set[str]) -> set[str]:
    text = " ".join(str(v) for v in values if str(v).strip())
    hits = {
        aspect for aspect, keys in _ASPECT_HINTS.items()
        if any(k.lower() in text.lower() for k in keys)
    }
    # Domain tags that are not written like review aspects but still imply a user-facing dimension.
    if any(k in text for k in ("日常", "单元剧", "慢热", "空气系", "轻松", "治愈")):
        hits.add("pacing")
    if any(k in text for k in ("百合", "恋爱", "友情", "群像", "党争", "后宫")):
        hits.add("character")
    if any(k in text for k in ("galgame", "视觉小说", "ADV", "AVG", "剧情")):
        hits.add("text")
        hits.add("story")
    if any(k in text for k in ("音乐", "OST", "OP", "ED", "角色歌")):
        hits.add("music")
    return hits or {"general"}


def _contains_any(text: str, keys: list[str]) -> bool:
    lower = text.lower()
    return any(k.lower() in lower for k in keys)


def _classify_book_subtype(values: list[str] | set[str]) -> str:
    text = " ".join(str(v) for v in values if str(v).strip())
    if _contains_any(text, _BOOK_SUBTYPE_TAGS["light_novel"]):
        return "light_novel"
    if _contains_any(text, _BOOK_SUBTYPE_TAGS["comic"]):
        return "comic"
    if _contains_any(text, _BOOK_SUBTYPE_TAGS["novel"]):
        return "novel"
    return "book"


def _classify_music_subtype(values: list[str] | set[str]) -> str:
    text = " ".join(str(v) for v in values if str(v).strip())
    for key in ("ost", "theme_song", "character_song", "artist"):
        if _contains_any(text, _MUSIC_SUBTYPE_TAGS[key]):
            return key
    return "music"


def _subtype_tags(subject_type: str, book_subtype: str, music_subtype: str) -> list[str]:
    if subject_type == "book" and book_subtype != "auto":
        return _BOOK_SUBTYPE_TAGS.get(book_subtype, [])[:4]
    if subject_type == "music" and music_subtype != "auto":
        return _MUSIC_SUBTYPE_TAGS.get(music_subtype, [])[:4]
    return []


def _aspect_profile_summary(profile: UserAspectProfile | None) -> dict:
    if profile is None:
        return {}
    return {
        "subject_type": profile.subject_type,
        "likes": [x.model_dump(mode="json") for x in profile.likes[:6]],
        "dislikes": [x.model_dump(mode="json") for x in profile.dislikes[:6]],
        "sample_count": profile.sample_count,
        "extraction_source": profile.extraction_source,
        "updated_at": profile.updated_at,
    }


def _egs_mapping_confidence(egs_title: str, subject: dict) -> tuple[float, str]:
    """Return confidence for EGS title -> Bangumi subject.

    We deliberately reject loose partial matches. A wrong galgame mapping is worse than no mapping:
    ランス10 must not become ランス9, and サクラノ刻 must not become サクラノ詩.
    """
    egs_key = _norm_title(egs_title)
    if not egs_key:
        return 0.0, "empty_egs_title"
    keys = {
        _norm_title(subject.get("name")),
        _norm_title(subject.get("name_cn")),
    }
    keys.discard("")
    if egs_key in keys:
        return 1.0, "exact_title"
    for key in keys:
        if _safe_edition_delta(key, egs_key) or _safe_edition_delta(egs_key, key):
            return 0.86, "edition_delta"
    return 0.0, "title_mismatch"


def _expand_moods(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        out.extend(_MOOD_TAG_MAP.get(t, [t]))
    return list(dict.fromkeys(out))


def _quality_popular(rating: dict) -> float:
    """人气+口碑：排名越前越高（0~1）。"""
    rk = rating.get("rank") or 0
    return 1.0 / (1.0 + rk ** 0.5) if rk > 0 else 0.0


def _quality_niche(rating: dict) -> float:
    """冷门佳作：以**高分为主**，低人气只做微调（避免把平庸冷门顶上来）。"""
    score = rating.get("score") or 0
    if score < 7.5:  # 冷门"佳作"——分数不达标的不算
        return 0.0
    total = rating.get("total") or 0
    rarity = 1.0 / (1.0 + total / 800.0)  # 投票越少越高
    return (score / 10.0) + 0.6 * rarity  # 分数主导，稀有度微调


def _eps_value(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _blank(x: dict) -> dict:
    img = x.get("images") or {}
    return {
        "name": x.get("name_cn") or x.get("name"),
        "matched": set(), "graph": set(), "weight": 0.0,
        "rating": x.get("rating") or {},
        "image": img.get("common") or img.get("medium") or img.get("grid"),
        "eps": _eps_value(x.get("eps") or x.get("total_episodes")),
        "cf": 0.0, "cf_from": set(),
        "external": set(), "external_evidence": [], "external_boost": 0.0,
        "external_mappings": [],
        "tags": _tag_names(x),
    }


def _blank_id() -> dict:
    """协同召回新候选（i2i 只给 subject_id+score，name/rating/image 待 enrich 补）。"""
    return {
        "name": None, "matched": set(), "graph": set(), "weight": 0.0,
        "rating": {}, "image": None, "eps": None, "cf": 0.0, "cf_from": set(),
        "external": set(), "external_evidence": [], "external_boost": 0.0,
        "external_mappings": [],
        "tags": set(),
    }


class RecommendArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(
        None, description="额外/心境标签（如 ['治愈','百合']），与口味标签合并召回；心境推荐时由你提炼"
    )
    limit: int = Field(8, ge=1, le=20)
    username: str | None = Field(None, description="不传则用当前账号（需 token）")
    niche: bool = Field(False, description="冷门挖宝：偏高分低人气、少推大热门（重度用户/想挖宝时用）")
    explore: bool = Field(False, description="口味拓展：用次级标签探索邻近题材，跳出核心舒适区")
    use_graph: bool = Field(True, description="图谱召回：从你最爱作品的监督/制作组找其未看的其他作品")
    use_cf: bool = Field(True, description="协同召回：看过你爱的作品的人还看了啥（离线 CF，需 i2i 表；无表则自动跳过）")
    use_series: bool = Field(True, description="系列入口回溯：若推荐项是续集而你没看前作，自动换成入口作（别莫名其妙推你第二季）")
    use_external_recall: bool = Field(True, description="game/galgame 推荐时，是否用批判空间排行做前置召回并映射回 Bangumi")
    enrich_evidence: bool = Field(True, description="为推荐结果补充统一评价证据；game 会补批判空间/VNDB，默认开启")
    use_aspect_profile: bool = Field(True, description="是否读取长期记忆中的 aspect 好球区/雷区参与 rerank 与解释")
    exclude_ids: list[int] = Field(default_factory=list, max_length=80, description="本轮要排除的 Bangumi subject_id；用于 critiquing 换一批")
    prefer_tags: list[str] = Field(default_factory=list, max_length=12, description="critiquing/澄清得到的临时偏好标签，只作用于本轮")
    avoid_tags: list[str] = Field(default_factory=list, max_length=12, description="critiquing 得到的临时避雷标签，只作用于本轮，不自动写长期记忆")
    max_episodes: int | None = Field(None, ge=1, le=200, description="短篇约束；候选 eps 超过该值会被过滤")
    cross_media: bool = Field(False, description="跨媒体召回：如从用户喜欢的动画召回原作漫画/轻小说/game/music")
    book_subtype: Literal["auto", "comic", "light_novel", "novel"] = Field(
        "auto", description="subject_type=book 时的媒介细分；漫画/轻小说/小说推荐时显式设置"
    )
    music_subtype: Literal["auto", "ost", "theme_song", "character_song", "artist"] = Field(
        "auto", description="subject_type=music 时的音乐细分；OST/OPED/角色歌/艺人专辑推荐时显式设置"
    )


class RecEvidence(BaseModel):
    source: str
    score: float | None = None
    scale: int | None = None
    count: int | None = None
    signal: str = "unknown"
    note: str = ""


class ExternalMappingEvidence(BaseModel):
    source: str
    external_title: str
    external_id: int | str | None = None
    bangumi_id: int
    mapping_confidence: float
    matched_by: str
    conflict_reason: str | None = None


class RecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    score: float
    reasons: list[str]
    explicit_tag_matches: list[str] = Field(default_factory=list)
    bangumi_score: float | None = None
    rank: int | None = None
    image: str | None = None
    review_consensus: str | None = None
    evidence: list[RecEvidence] = Field(default_factory=list)
    external_mappings: list[ExternalMappingEvidence] = Field(default_factory=list)
    quality_badges: list[str] = Field(default_factory=list)
    aspect_matches: list[str] = Field(default_factory=list)
    aspect_warnings: list[str] = Field(default_factory=list)
    source_routes: list[str] = Field(default_factory=list)
    media_subtype: str | None = None
    media_notes: list[str] = Field(default_factory=list)


class RecommendResult(BaseModel):
    subject_type: str
    based_on_tags: list[str]
    mode: str = "normal"
    items: list[RecItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    applied_constraints: list[str] = Field(default_factory=list)
    aspect_profile_summary: dict = Field(default_factory=dict)
    cold_start_questions: list[str] = Field(default_factory=list)
    critique_chips: list[str] = Field(default_factory=list)
    mapping_warnings: list[str] = Field(default_factory=list)  # EGS→Bangumi 未安全对齐/歧义（可观测，不静默丢）
    media_strategy: dict = Field(default_factory=dict)


class RecommendTool(Tool):
    name = "recommend_subjects"
    description = (
        "根据用户口味推荐 TA 没看过的作品，支持 anime/book/music/game/real。"
        "多路召回（标签 + 图谱：你爱的作品的监督/制作组的其他作品）。"
        "重度用户/挖冷门设 niche=true；想跳出舒适区设 explore=true。"
        "用户对上轮推荐说换一批/短一点/更冷门/不要某题材时，用 exclude_ids/max_episodes/niche/avoid_tags 做 critiquing 重推。"
        "喜欢某动画想推原作/相关漫画小说游戏音乐时设 cross_media=true。"
        "用于『据我口味推荐 / 类似X / 挖点冷门 / 换个口味 / 今天想看治愈的』。"
    )
    args_model = RecommendArgs
    result_model = RecommendResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory | None = None) -> None:
        self.client = client
        self.ltm = ltm
        self.reviewer = ReviewSubjectTool(client)
        self.egs_rank = RankErogameScapeTool()

    async def _tag_recall(self, cand, stype, recall_tags, user_tags, maxw, mood, seen, niche):
        offset = _NICHE_OFFSET if niche else 0
        for tag in recall_tags:
            w = maxw if tag in mood else user_tags.get(tag, 1.0)
            offsets = [0, 50, 100] if tag in mood else [offset]
            for off in offsets:
                res = await self.client.search_subjects(
                    "", stype, sort="heat", limit=_RECALL_PER_TAG, tags=[tag], offset=off
                )
                batch = res.get("data", []) or []
                for x in batch:
                    sid = x.get("id")
                    if not sid or sid in seen:
                        continue
                    c = cand.setdefault(sid, _blank(x))
                    c["tags"].update(_tag_names(x))
                    c["matched"].add(tag)
                    c["weight"] += w
                if len(batch) < _RECALL_PER_TAG:
                    break

    async def _graph_recall(self, cand, stype, fav_ids, seen):
        for sid in fav_ids[:_GRAPH_FAV]:
            persons = await self.client.get_subject_persons(sid)
            picks = [p for p in (persons or []) if p.get("relation") in _GRAPH_ROLES and p.get("id")][:2]
            for p in picks:
                works = await self.client.get_person_subjects(p["id"])
                works = [w for w in (works or []) if w.get("type") == stype and w.get("id")]
                for w in works[:_GRAPH_WORKS]:  # 每制作组限量，防霸榜
                    wid = w["id"]
                    if wid in seen:
                        continue
                    c = cand.setdefault(wid, _blank(w))
                    c["tags"].update(_tag_names(w))
                    c["graph"].add(f"同{p.get('relation')}·{p.get('name')}")

    def _cf_recall(self, cand: dict, fav_ids: list[int], seen: set[int], i2i: dict) -> None:
        """协同召回：用户最爱作品的 i2i 邻居（看过 X 的人也看 Y）。补在线缺失的协同信号。

        i2i score 量纲随模型(BM25/ALS)波动，故用 **rank 衰减** 1/(1+rank) 累加，量纲稳定、跨模型可比；
        多个种子召回到同一邻居会累加（共识越强分越高）。i2i 是内存 dict，无 IO，故同步。
        """
        items = i2i.get("items") or {}
        if not items:
            return
        for sid in fav_ids[:_CF_SEEDS]:
            for rank, pair in enumerate((items.get(str(sid)) or [])[:_CF_NBR]):
                nbr_sid = int(pair[0])
                if nbr_sid in seen:
                    continue
                c = cand.get(nbr_sid)
                if c is None:
                    c = cand[nbr_sid] = _blank_id()
                c["cf"] += 1.0 / (1 + rank)
                c["cf_from"].add(sid)

    async def _cross_media_recall(self, cand: dict, target_stype: int, source_items: list[dict], seen: set[int]) -> None:
        seeds = sorted(source_items, key=lambda it: -(it.get("rate") or 0))[:_CF_SEEDS]
        for item in seeds:
            sid = (item.get("subject") or {}).get("id")
            src_name = (item.get("subject") or {}).get("name_cn") or (item.get("subject") or {}).get("name")
            if not sid:
                continue
            try:
                rels = await self.client.get_subject_relations(sid)
            except Exception:  # noqa: BLE001
                continue
            for rel in rels or []:
                rid = rel.get("id")
                if not rid or rid in seen or rel.get("type") != target_stype:
                    continue
                relation = str(rel.get("relation") or "")
                if relation and not any(k in relation for k in _CROSS_MEDIA_REL):
                    continue
                c = cand.setdefault(rid, _blank(rel))
                c["graph"].add(f"跨媒体关系：从《{src_name or sid}》到{relation or '相关条目'}")

    async def _external_game_recall(self, cand: dict, seen: set[int], limit: int) -> list[str]:
        """Use EGS ranking as a front recall source, then anchor candidates back to Bangumi IDs.

        返回 mapping_warnings：未能安全对齐 / 歧义的 EGS 条目（可观测，避免静默丢弃）。
        """
        warnings: list[str] = []
        res = await self.egs_rank.run(
            EGSRankArgs(sort="median", limit=min(max(limit * 4, 12), 40), min_votes=80, erogame_only=True)
        )
        if not res.ok or not res.data:
            return warnings
        for egs in res.data.results[: min(limit * 3, 18)]:
            try:
                raw = await self.client.search_subjects(egs.title, SUBJECT_TYPE["game"], limit=5)
            except Exception:  # noqa: BLE001
                continue
            subjects = raw.get("data") or []
            matches: list[tuple[float, str, dict]] = []
            for s in subjects:
                conf, matched_by = _egs_mapping_confidence(egs.title, s)
                if conf >= 0.85:
                    matches.append((conf, matched_by, s))
            matches.sort(key=lambda x: -x[0])
            if not matches:
                warnings.append(f"批判空间《{egs.title}》(中央值 {egs.median or '?'}) 未能安全对齐 Bangumi 条目，已跳过")
                continue
            if len(matches) > 1 and matches[0][0] == matches[1][0]:
                warnings.append(f"批判空间《{egs.title}》匹配到多个同分 Bangumi 候选(歧义)，已跳过避免错配")
                continue
            conf, matched_by, best = matches[0]
            if not best or not best.get("id") or best["id"] in seen:
                continue
            sid = best["id"]
            c = cand.setdefault(sid, _blank(best))
            c["tags"].update(_tag_names(best))
            rank = f"#{egs.rank_position}" if egs.rank_position else "排行候选"
            c["external"].add(
                f"批判空间{rank} 中央值 {egs.median or '未知'} / 数据数 {egs.vote_count or 0}"
                f"（EGS《{egs.title}》→ Bangumi，{matched_by}, conf={conf:.2f}）"
            )
            c["external_boost"] = max(c.get("external_boost", 0.0), min(((egs.median or 0) / 100) * 0.65, 0.65))
            c["external_evidence"].append(
                RecEvidence(
                    source="ErogameScape/批判空间",
                    score=egs.median,
                    scale=100,
                    count=egs.vote_count,
                    signal="strong" if (egs.median or 0) >= 80 and (egs.vote_count or 0) >= 80 else "positive",
                    note=f"{rank}；品牌 {egs.brand or '未知'}；映射 {matched_by} conf={conf:.2f}；EGS题名《{egs.title}》",
                )
            )
            c["external_mappings"].append(
                ExternalMappingEvidence(
                    source="ErogameScape/批判空间",
                    external_title=egs.title,
                    external_id=egs.id,
                    bangumi_id=sid,
                    mapping_confidence=round(conf, 4),
                    matched_by=matched_by,
                )
            )
        return warnings

    async def _enrich(self, ranked_ids: list[tuple[int, dict]]) -> None:
        """对前 N 个信息不全（缺评分或缺名，多来自图谱/协同召回）的候选按需补 get_subject。"""
        n = 0
        for sid, c in ranked_ids:
            if n >= _ENRICH_TOP:
                break
            if c["name"] and c["rating"].get("score"):
                continue
            n += 1
            try:
                raw = await self.client.get_subject(sid)
                if not c["name"]:
                    c["name"] = raw.get("name_cn") or raw.get("name")
                    img = raw.get("images") or {}
                    c["image"] = c["image"] or img.get("common") or img.get("medium") or img.get("grid")
                c["rating"] = raw.get("rating") or c["rating"]
                c["eps"] = c.get("eps") or _eps_value(raw.get("eps") or raw.get("total_episodes"))
                c["tags"].update(_tag_names(raw))
            except Exception:  # noqa: BLE001
                pass

    async def _series_context(self, sid: int, stype: int, seen: set[int]):
        """一次查 relations，同时拿：①续集→回溯入口（顺序关系，要替换） ②同 IP 旁支（平行关系，只提示）。

        返回 (entry, siblings)：
          entry = (sid, name, image, rating)，或 None（本身是入口 / 前作已看，不替换）；
          siblings = 旁支作品名列表（外传 / 世界观分支 / 番外，"提一嘴"用）。
        """
        rels = await self.client.get_subject_relations(sid)
        siblings = [
            n for n in dict.fromkeys(
                (r.get("name_cn") or r.get("name")) for r in (rels or [])
                if r.get("relation") in _SIDE_REL and r.get("type") == stype and r.get("id")
            ) if n
        ][:3]

        cur, last, cur_rels = sid, None, rels
        for _ in range(_SERIES_MAX_HOP):
            pre = next(
                (r for r in (cur_rels or [])
                 if r.get("relation") == "前传" and r.get("type") == stype
                 and r.get("id") and r["id"] not in seen),
                None,
            )
            if not pre:
                break
            cur, last = pre["id"], pre
            cur_rels = await self.client.get_subject_relations(cur)

        entry = None
        if last is not None:
            try:
                raw = await self.client.get_subject(cur)
                img = raw.get("images") or {}
                entry = (cur, raw.get("name_cn") or raw.get("name"),
                         img.get("common") or img.get("medium"), raw.get("rating") or {})
            except Exception:  # noqa: BLE001
                entry = (cur, last.get("name_cn") or last.get("name"), None, {})
        return entry, siblings

    async def run(self, args: RecommendArgs) -> ToolResult[RecommendResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        await emit_tool_progress(tool=self.name, summary="解析推荐目标与用户身份", current=1, total=6)
        if args.username:
            username = args.username
        else:
            try:
                me = await self.client.get_me()
            except Exception:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    error="未提供 username 且无法获取当前账号（需要有效 BANGUMI_TOKEN）；请改用 username 指定要推荐的用户。",
                )
            username = me.get("username") or str(me.get("id"))

        excluded = {int(x) for x in args.exclude_ids if x}
        await emit_tool_progress(tool=self.name, summary=f"拉取 @{username} 的 {args.subject_type} 收藏", current=2, total=6)
        items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")} | excluded
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        memory_dislikes: list[str] = []
        aspect_profile: UserAspectProfile | None = None
        if self.ltm is not None:
            mem = self.ltm.load_user(username)
            memory_dislikes = [it.value for it in mem.dislikes if it.value.strip()]
            if args.use_aspect_profile:
                aspect_profile = mem.aspect_profiles.get(args.subject_type)
        all_tags = [t["tag"] for t in profile.top_tags]
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:8]}
        maxw = max(user_tags.values()) if user_tags else 1.0

        subtype_focus_tags = _subtype_tags(args.subject_type, args.book_subtype, args.music_subtype)
        mood = _expand_moods(list(dict.fromkeys((args.tags or []) + args.prefer_tags + subtype_focus_tags)))
        core = all_tags[2:8] if args.explore else all_tags[:6]  # explore：用次级标签拓展
        recall_tags = list(dict.fromkeys(mood + core))[:_MAX_RECALL_TAGS]
        cold_start_questions: list[str] = []
        if len(watched) < 5 and not (args.tags or args.prefer_tags):
            cold_start_questions = [
                "你最近想看治愈日常、恋爱还是剧情向？",
                "想要冷门挖宝还是先来几部稳的？",
                "有明确避雷吗，比如党争、致郁、后宫、长篇？",
            ]
            recall_tags = list(dict.fromkeys(recall_tags + _COLD_START_TAGS.get(args.subject_type, [])))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        await emit_tool_progress(
            tool=self.name,
            summary="多路召回候选",
            current=3,
            total=6,
            note="标签/外部排行/图谱/协同/跨媒体",
        )
        await self._tag_recall(cand, stype, recall_tags, user_tags, maxw, mood, seen, args.niche)
        mapping_warnings: list[str] = []
        if args.subject_type == "game" and args.use_external_recall:
            mapping_warnings = await self._external_game_recall(cand, seen, args.limit)

        # 用户最爱作品（按评分降序）——图谱召回与协同召回共用作种子
        fav_sorted = sorted(watched, key=lambda it: -(it.get("rate") or 0))
        fav_ids = [it["subject"]["id"] for it in fav_sorted if it.get("subject", {}).get("id")]
        fav_names = {
            it["subject"]["id"]: (it["subject"].get("name_cn") or it["subject"].get("name"))
            for it in fav_sorted if it.get("subject", {}).get("id")
        }
        if args.use_graph:
            await self._graph_recall(cand, stype, fav_ids, seen)
        if args.use_cf:  # 协同召回（离线 CF 反哺）：无 i2i 表则 _load_i2i 返回空、静默跳过
            self._cf_recall(cand, fav_ids, seen, _load_i2i(args.subject_type))
        if args.cross_media:
            if args.subject_type != "anime":
                source_items = await self.client.get_all_user_collections(
                    username, SUBJECT_TYPE["anime"], collection_type=2, max_items=300
                )
            else:
                source_items = []
                for source_type in ("book", "game"):
                    source_items.extend(
                        await self.client.get_all_user_collections(
                            username, SUBJECT_TYPE[source_type], collection_type=2, max_items=200
                        )
                    )
            await self._cross_media_recall(cand, stype, source_items, seen)
        await emit_tool_progress(
            tool=self.name,
            summary=f"召回完成：{len(cand)} 个候选，开始补评分与封面",
            current=4,
            total=6,
        )

        def affinity(c: dict) -> float:
            return c["weight"] / maxw  # ≈ 加权命中标签数

        def graph_bonus(c: dict) -> float:
            # 图谱是弱信号（同制作组≠一定喜欢），封顶 0.9，低于协同(1.5)：协同>图谱
            return min(len(c["graph"]), 2) * 0.45

        def cf_bonus(c: dict) -> float:
            return min(c.get("cf", 0.0), _CF_CAP)  # 封顶，避免协同召回压过标签口味

        def external_bonus(c: dict) -> float:
            return min(c.get("external_boost", 0.0), 0.65)

        mood_set = set(mood)

        def memory_avoidance_hits(c: dict) -> list[str]:
            if not memory_dislikes:
                return []
            haystack = [str(x) for x in (c.get("tags") or set()) | (c.get("matched") or set())]
            if c.get("name"):
                haystack.append(str(c["name"]))
            hits: list[str] = []
            for value in memory_dislikes:
                key = _norm_title(value)
                if not key:
                    continue
                for h in haystack:
                    hk = _norm_title(h)
                    if hk and (key == hk or key in hk or hk in key):
                        hits.append(value)
                        break
            return list(dict.fromkeys(hits))[:4]

        def memory_penalty(c: dict) -> float:
            return -2.2 if memory_avoidance_hits(c) else 0.0

        def temporary_avoidance_hits(c: dict) -> list[str]:
            haystack = [str(x) for x in (c.get("tags") or set()) | (c.get("matched") or set())]
            if c.get("name"):
                haystack.append(str(c["name"]))
            hits: list[str] = []
            for value in args.avoid_tags:
                key = _norm_title(value)
                if not key:
                    continue
                if any((hk := _norm_title(h)) and (key == hk or key in hk or hk in key) for h in haystack):
                    hits.append(value)
            return list(dict.fromkeys(hits))[:4]

        def temporary_penalty(c: dict) -> float:
            return -1.5 if temporary_avoidance_hits(c) else 0.0

        def candidate_aspects(c: dict) -> set[str]:
            values = list(c.get("tags") or set()) + list(c.get("matched") or set())
            if c.get("name"):
                values.append(str(c["name"]))
            return _candidate_aspects(values)

        def media_subtype(c: dict) -> str | None:
            values = list(c.get("tags") or set()) + list(c.get("matched") or set())
            if c.get("name"):
                values.append(str(c["name"]))
            if args.subject_type == "book":
                return _classify_book_subtype(values)
            if args.subject_type == "music":
                return _classify_music_subtype(values)
            return None

        def media_notes(c: dict) -> list[str]:
            subtype = media_subtype(c)
            notes: list[str] = []
            if args.subject_type == "book" and subtype:
                notes.append(f"book 分型：{_BOOK_SUBTYPE_LABEL.get(subtype, subtype)}")
                if args.book_subtype != "auto" and subtype != args.book_subtype:
                    notes.append("未完全命中本轮 book 分型，已降权")
            if args.subject_type == "music" and subtype:
                notes.append(f"music 分型：{_MUSIC_SUBTYPE_LABEL.get(subtype, subtype)}")
                if args.music_subtype != "auto" and subtype != args.music_subtype:
                    notes.append("未完全命中本轮 music 分型，已降权")
            if args.subject_type == "music":
                notes.append("MusicBrainz 可作为专辑/艺人/发行时间元数据补充，不作为口碑评分源")
            return notes[:4]

        def media_subtype_penalty(c: dict) -> float:
            subtype = media_subtype(c)
            if args.subject_type == "book" and args.book_subtype != "auto":
                return 0.35 if subtype == args.book_subtype else -1.15
            if args.subject_type == "music" and args.music_subtype != "auto":
                return 0.35 if subtype == args.music_subtype else -0.85
            return 0.0

        def aspect_like_hits(c: dict) -> list[str]:
            if aspect_profile is None:
                return []
            aspects = candidate_aspects(c)
            hits = [
                f"{pref.label}({pref.weight:.2f})"
                for pref in aspect_profile.likes
                if pref.aspect in aspects and pref.confidence >= 0.35
            ]
            return hits[:4]

        def aspect_dislike_hits(c: dict) -> list[str]:
            if aspect_profile is None:
                return []
            aspects = candidate_aspects(c)
            hits = [
                f"{pref.label}({pref.weight:.2f})"
                for pref in aspect_profile.dislikes
                if pref.aspect in aspects and pref.confidence >= 0.35
            ]
            return hits[:4]

        def aspect_bonus(c: dict) -> float:
            if aspect_profile is None:
                return 0.0
            aspects = candidate_aspects(c)
            bonus = sum(
                min(pref.weight, 1.0) * 0.28 * pref.confidence
                for pref in aspect_profile.likes
                if pref.aspect in aspects
            )
            penalty = sum(
                min(pref.weight, 1.0) * 0.42 * pref.confidence
                for pref in aspect_profile.dislikes
                if pref.aspect in aspects
            )
            return max(min(bonus - penalty, 0.7), -0.9)

        def explicit_tag_adjust(c: dict) -> float:
            """用户这轮显式说的口味/心境要比历史画像更硬。

            例如"今天想看治愈"时，历史画像里的热血/战斗候选不能靠总画像分数顶上来。
            """
            if not mood_set:
                return 0.0
            return 0.8 if c["matched"] & mood_set else -1.2

        # 预排（不含质量）→ 给 top 候选补名/评分 → 终排（含质量）
        prelim = sorted(
            cand.items(),
            key=lambda kv: -(
                affinity(kv[1]) + graph_bonus(kv[1]) + cf_bonus(kv[1])
                + external_bonus(kv[1]) + explicit_tag_adjust(kv[1])
                + memory_penalty(kv[1]) + temporary_penalty(kv[1]) + aspect_bonus(kv[1])
                + media_subtype_penalty(kv[1])
            ),
        )[:40]
        await self._enrich(prelim)
        await emit_tool_progress(
            tool=self.name,
            summary=f"证据补全完成：{len(prelim)} 个候选，开始重排",
            current=5,
            total=6,
        )

        def score(c: dict) -> float:
            if args.niche:  # 挖冷门：协同偏热门，权重压低
                return (0.5 * affinity(c) + 0.5 * graph_bonus(c)
                        + 0.4 * cf_bonus(c) + external_bonus(c)
                        + explicit_tag_adjust(c) + memory_penalty(c) + temporary_penalty(c)
                        + aspect_bonus(c) + media_subtype_penalty(c) + 2.0 * _quality_niche(c["rating"]))
            return (
                affinity(c) + graph_bonus(c) + cf_bonus(c) + external_bonus(c)
                + explicit_tag_adjust(c) + memory_penalty(c) + temporary_penalty(c)
                + aspect_bonus(c) + media_subtype_penalty(c) + 1.2 * _quality_popular(c["rating"])
            )

        def cf_reason(c: dict) -> list[str]:
            if not c["cf_from"]:
                return []
            names = [n for n in (fav_names.get(s) for s in c["cf_from"]) if n]
            if not names:
                return ["相似口味用户的选择"]
            return [f"看过《{names[0]}》的人也在看" + (" 等" if len(c["cf_from"]) > 1 else "")]

        ranked = sorted(prelim, key=lambda kv: -score(kv[1]))
        out: list[RecItem] = []
        seen_series: set[str] = set()
        seen_ids: set[int] = set()
        pool_limit = min(args.limit * 2, 20) if args.enrich_evidence else args.limit
        for sid, c in ranked:
            if not c["name"]:
                continue  # 协同召回候选未被 enrich 补到名，跳过
            eps = c.get("eps")
            if args.max_episodes is not None and isinstance(eps, int) and eps > args.max_episodes:
                continue
            r_id, r_name, r_img, r_rating, extra = sid, c["name"], c.get("image"), c["rating"], []
            if args.use_series:
                entry, siblings = await self._series_context(sid, stype, seen)
                if entry:  # 续集且前作未看 → 换成入口作（顺序关系）
                    r_id, r_name, r_img, r_rating = entry
                    extra.append(f"系列入口（《{c['name']}》的前作，建议从这部入坑）")
                if siblings:  # 同 IP 旁支 → 提一嘴（平行关系，不替换）
                    extra.append("同 IP 还有：" + "、".join(f"《{s}》" for s in siblings))
            sk = _series_key(r_name)
            if r_id in seen_ids or sk in seen_series:  # 入口去重（多个续集回溯到同一入口）
                continue
            seen_ids.add(r_id)
            seen_series.add(sk)
            explicit_matches = sorted(c["matched"] & mood_set)
            reasons = sorted(c["matched"]) + sorted(c["graph"]) + sorted(c.get("external", set())) + cf_reason(c) + extra
            avoid_hits = memory_avoidance_hits(c)
            if avoid_hits:
                reasons.append("命中长期记忆避雷，已降权：" + "、".join(avoid_hits))
            temp_hits = temporary_avoidance_hits(c)
            if temp_hits:
                reasons.append("命中本轮临时避雷，已降权：" + "、".join(temp_hits))
            aspect_matches = aspect_like_hits(c)
            aspect_warnings = aspect_dislike_hits(c)
            if aspect_matches:
                reasons.append("aspect 好球区命中：" + "、".join(aspect_matches))
            if aspect_warnings:
                reasons.append("aspect 雷区命中，已降权：" + "、".join(aspect_warnings))
            if mood_set and not explicit_matches:
                reasons.append("未命中本轮显式标签，作为画像邻近补充")
            subtype = media_subtype(c)
            subtype_notes = media_notes(c)
            reasons.extend(n for n in subtype_notes if n not in reasons)
            out.append(RecItem(
                id=r_id, name=r_name, score=round(score(c), 3),
                reasons=reasons,
                explicit_tag_matches=explicit_matches,
                bangumi_score=(r_rating or {}).get("score"),
                rank=(r_rating or {}).get("rank"),
                image=r_img,
                evidence=list(c.get("external_evidence", [])),
                external_mappings=list(c.get("external_mappings", [])),
                aspect_matches=aspect_matches,
                aspect_warnings=aspect_warnings,
                media_subtype=subtype,
                media_notes=subtype_notes,
            ))
            if len(out) >= pool_limit:
                break

        if args.enrich_evidence:
            await emit_tool_progress(
                tool=self.name,
                summary=f"融合评价证据：{len(out)} 个候选",
                current=5,
                total=6,
            )
            await self._enrich_review_evidence(out, aspect_profile, args.subject_type)
            out.sort(key=lambda x: -x.score)
            out = out[: args.limit]

        await emit_tool_progress(tool=self.name, summary=f"推荐完成：输出 {len(out)} 个候选", current=6, total=6)

        mode = "niche" if args.niche else ("explore" if args.explore else "normal")
        notes: list[str] = []
        if mood_set:
            strict_count = sum(1 for it in out if it.explicit_tag_matches)
            if strict_count == 0:
                notes.append("没有找到未看且命中本轮显式标签的高置信候选；当前结果为画像邻近补充。")
            elif strict_count < len(out):
                notes.append("部分候选未命中本轮显式标签，只能作为画像邻近补充。")
        if memory_dislikes:
            notes.append("已按长期记忆避雷项降权：" + "、".join(memory_dislikes[:8]))
        applied_constraints: list[str] = []
        if args.exclude_ids:
            applied_constraints.append(f"已排除上轮/指定候选 {len(args.exclude_ids)} 个")
        if args.max_episodes is not None:
            applied_constraints.append(f"短篇约束：eps ≤ {args.max_episodes}")
        if args.avoid_tags:
            applied_constraints.append("本轮临时避雷：" + "、".join(args.avoid_tags[:8]))
        if args.prefer_tags:
            applied_constraints.append("本轮偏好：" + "、".join(args.prefer_tags[:8]))
        if args.cross_media:
            applied_constraints.append("已启用跨媒体召回")
        if args.subject_type == "book" and args.book_subtype != "auto":
            applied_constraints.append(f"book 分型约束：{_BOOK_SUBTYPE_LABEL.get(args.book_subtype, args.book_subtype)}")
        if args.subject_type == "music" and args.music_subtype != "auto":
            applied_constraints.append(f"music 分型约束：{_MUSIC_SUBTYPE_LABEL.get(args.music_subtype, args.music_subtype)}")
        if aspect_profile:
            notes.append(f"已使用 {args.subject_type} aspect 情感画像参与重排。")
        if cold_start_questions:
            notes.append("收藏/评分样本偏少，当前结果同时给出冷启动澄清问题。")
        media_strategy = {
            "subject_type": args.subject_type,
            "book_subtype": args.book_subtype,
            "music_subtype": args.music_subtype,
            "focus_tags": subtype_focus_tags,
            "policy": (
                "book 条目混含漫画/轻小说/小说，显式分型会进入召回与降权；不确定候选保留但标注。"
                if args.subject_type == "book"
                else "music 条目以 Bangumi 社区锚点为主，MusicBrainz 只补元数据，不参与口碑评分。"
                if args.subject_type == "music"
                else ""
            ),
        }
        return ToolResult(
            ok=True,
            data=RecommendResult(
                subject_type=args.subject_type, based_on_tags=recall_tags, mode=mode, items=out,
                notes=notes,
                applied_constraints=applied_constraints,
                aspect_profile_summary=_aspect_profile_summary(aspect_profile),
                cold_start_questions=cold_start_questions,
                critique_chips=[
                    "换一批，先排除这些",
                    "这些太长了，换几部短一点的",
                    "更冷门一点",
                    "不要这个题材，换个方向",
                    "按我的好球区再收紧一点",
                ],
                mapping_warnings=mapping_warnings,
                media_strategy=media_strategy,
            ),
            sources=[
                Citation(title=it.name, url=f"https://bgm.tv/subject/{it.id}", source="bangumi", image=it.image)
                for it in out
            ],
        )

    async def _enrich_review_evidence(
        self,
        items: list[RecItem],
        aspect_profile: UserAspectProfile | None = None,
        subject_type: str = "anime",
    ) -> None:
        for item in items:
            try:
                res = await self.reviewer.run(
                    ReviewSubjectArgs(
                        subject_id=item.id,
                        title_hint=item.name,
                        include_comments=False,
                        spoiler_level="none",
                    )
                )
            except Exception:  # noqa: BLE001
                continue
            if not res.ok or not res.data:
                continue
            item.review_consensus = res.data.consensus
            review_evidence = [
                RecEvidence(
                    source=r.source,
                    score=r.score,
                    scale=r.scale,
                    count=r.count,
                    signal=r.signal,
                    note=r.note,
                )
                for r in res.data.ratings
            ]
            seen = {(e.source, e.score, e.scale, e.count, e.note) for e in item.evidence}
            item.evidence.extend(
                e for e in review_evidence if (e.source, e.score, e.scale, e.count, e.note) not in seen
            )
            if subject_type == "game":
                for group in res.data.source_groups:
                    item.source_routes.append(f"{group.group}：{group.consensus}")
                item.source_routes.extend(res.data.source_routing_notes[:2])
            if aspect_profile and res.data.aspect_summary:
                like_aspects = {p.aspect: p for p in aspect_profile.likes}
                dislike_aspects = {p.aspect: p for p in aspect_profile.dislikes}
                for summary in res.data.aspect_summary:
                    if summary.aspect in like_aspects and summary.dominant_sentiment == "positive":
                        pref = like_aspects[summary.aspect]
                        text = f"评价证据支持你的好球区：{pref.label}"
                        if text not in item.aspect_matches:
                            item.aspect_matches.append(text)
                        item.score = round(item.score + 0.16 * pref.weight * pref.confidence, 3)
                    if summary.aspect in dislike_aspects and summary.dominant_sentiment in {"negative", "mixed"}:
                        pref = dislike_aspects[summary.aspect]
                        text = f"评价证据触及你的雷区：{pref.label}"
                        if text not in item.aspect_warnings:
                            item.aspect_warnings.append(text)
                        item.score = round(item.score - 0.2 * pref.weight * pref.confidence, 3)
            bonus = _review_bonus(item.evidence)
            if bonus:
                item.score = round(item.score + bonus, 3)
            item.quality_badges = _quality_badges(item.evidence)
            item.reasons.extend(item.quality_badges)
            for text in item.aspect_matches[:3]:
                if text not in item.reasons:
                    item.reasons.append(text)
            for text in item.aspect_warnings[:3]:
                if text not in item.reasons:
                    item.reasons.append(text)


def _review_bonus(evidence: list[RecEvidence]) -> float:
    total = 0.0
    for ev in evidence:
        if ev.signal == "strong":
            total += 0.18
        elif ev.signal == "positive":
            total += 0.1
        elif ev.signal == "mixed":
            total -= 0.06
        elif ev.signal == "weak":
            total -= 0.18
    return max(min(total, 0.45), -0.35)


def _quality_badges(evidence: list[RecEvidence]) -> list[str]:
    badges: list[str] = []
    for ev in evidence:
        if ev.signal in {"strong", "positive"}:
            score = f"{ev.score:g}/{ev.scale}" if ev.score is not None and ev.scale else "口碑正向"
            badges.append(f"{ev.source} {score}")
        elif ev.signal in {"mixed", "weak"}:
            badges.append(f"{ev.source} 口碑有争议")
    return badges[:3]
