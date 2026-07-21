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

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...memory import LongTermMemory
from ...memory.models import UserAspectProfile
from ...profile import compute_taste_profile
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..curation import curated_recall_candidates
from ..erogamescape.tool import EGSRankArgs, RankErogameScapeTool
from ..review.tool import ReviewSubjectArgs, ReviewSubjectTool, _ASPECT_HINTS

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


def _unique(values: list[str]) -> list[str]:
    return [v for v in dict.fromkeys(str(x).strip() for x in values if str(x).strip())]


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


# tool.py 在 otomo/tools/recommend/ → parents[1]=otomo/tools；索引落在 otomo/data/
_SEMANTIC_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "semantic_index.npz"
_semantic_index_cache: dict[str, Any] = {}


def _load_semantic_index() -> dict[str, Any] | None:
    """全站语义召回索引（scripts.build_semantic_index 产出）。缺失返回 None（不硬失败）。"""
    if "loaded" in _semantic_index_cache:
        return _semantic_index_cache.get("index")
    _semantic_index_cache["loaded"] = True
    _semantic_index_cache["index"] = None
    if not _SEMANTIC_INDEX_PATH.exists():
        return None
    try:
        import json as _json

        import numpy as _np

        data = _np.load(_SEMANTIC_INDEX_PATH, allow_pickle=True)
        _semantic_index_cache["index"] = {
            "ids": data["ids"],
            "vecs": data["vecs"],
            "meta": [_json.loads(m) for m in data["meta"]],
        }
    except Exception:  # noqa: BLE001
        _semantic_index_cache["index"] = None
    return _semantic_index_cache["index"]


def _semantic_recall(user_texts: list[str], seen: set[int], top_k: int) -> list[dict]:
    """用户高分作品向量 → 全站索引近邻 top_k（排除已看）。返回候选 meta 列表。"""
    index = _load_semantic_index()
    if index is None or not user_texts:
        return []
    import numpy as _np

    from .._rag import _embedder

    emb = _embedder()
    uvecs = emb.encode(user_texts, normalize_embeddings=True)
    uvec = _np.asarray(uvecs, dtype=_np.float32).mean(axis=0)
    norm = float((uvec ** 2).sum() ** 0.5)
    if norm > 0:
        uvec = uvec / norm
    sims = index["vecs"] @ uvec  # 已归一，点积=余弦
    order = _np.argsort(-sims)
    out: list[dict] = []
    for i in order:
        sid = int(index["ids"][i])
        if sid in seen:
            continue
        meta = dict(index["meta"][i])
        meta["_sim"] = float(sims[i])
        out.append(meta)
        if len(out) >= top_k:
            break
    return out


def _semantic_scores(user_texts: list[str], cand_texts: list[str]) -> list[float]:
    """bge 语义相似：用户高分作品文本的均值向量 vs 每个候选，池内 min-max 归一到 [0,1]。

    文本 = 名称 + 社区标签（标签才是口味语义的载体）。同步 CPU 计算（bge-small ~百毫秒级），
    调用方走 asyncio.to_thread；模型缺失/加载失败由调用方捕获降级。
    """
    from .._rag import _embedder

    emb = _embedder()
    uvecs = emb.encode(user_texts, normalize_embeddings=True)
    uvec = uvecs.mean(axis=0)
    norm = (uvec ** 2).sum() ** 0.5
    if norm > 0:
        uvec = uvec / norm
    cvecs = emb.encode(cand_texts, normalize_embeddings=True)
    sims = [float((cv * uvec).sum()) for cv in cvecs]
    lo, hi = min(sims), max(sims)
    if hi - lo < 1e-6:
        return [0.0] * len(sims)
    return [(s - lo) / (hi - lo) for s in sims]


def _taste_text(name: str, tags: Any) -> str:
    tag_list = [str(t) for t in (tags or []) if str(t).strip()][:8]
    return f"{name or ''} {' '.join(tag_list)}".strip()


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
    scenario: Literal["general", "tonight", "season", "backlog", "gal_intro", "cross_media"] = Field(
        "general",
        description="推荐场景：general 通用 / tonight 今晚看 / season 当季追番 / backlog 想看列表清理 / gal_intro galgame 入门 / cross_media 跨媒体延伸",
    )
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
    use_curation: bool = Field(True, description="是否用精选 Bangumi 目录作为低权重策展召回")
    use_semantic: bool = Field(True, description="bge 语义相似特征：候选与你高分作品的标签语义距离进重排（已验证 +7.5% NDCG；模型缺失自动跳过）")
    use_semantic_recall: bool = Field(False, description="实验性：用全站语义索引做召回补标签盲区（小样本消融未见增益甚至稀释，默认关，待大样本验证）")
    export_features: bool = Field(False, description="调试/训练用：在每个候选上附 features 特征向量（LTR 训练数据），生产不用")
    use_friends: bool = Field(False, description="好友圈社交召回：好友们想看/高分的未看作品进候选（要抓好友收藏，较慢；用户提到好友/圈子时开启）")
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
    why_recalled: list[str] = Field(default_factory=list)
    fit_points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    heat: dict = Field(default_factory=dict)
    next_step: list[str] = Field(default_factory=list)
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
    features: dict[str, float] | None = None  # export_features=True 时填；LTR 训练用


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
    scenario: str = "general"
    feedback_policy: dict = Field(default_factory=dict)


class RerankWeights(BaseModel):
    """rerank 打分权重。此前 9 个常数全是手调且从未被数据验证过——
    提出来供 eval_recommend.py --search 离线随机搜索（HR@10/NDCG 反馈回路）。"""
    graph_per_hit: float = 0.45     # 图谱召回每命中一个 staff/制作组
    cf_cap: float = 1.5             # 协同信号封顶
    external_cap: float = 0.65      # 外部证据（EGS/好友/curated）封顶
    aspect_like: float = 0.28       # 长期好球区每命中
    aspect_dislike: float = 0.42    # 长期雷区每命中（罚）
    explicit_hit: float = 0.8       # 本轮显式心境命中
    explicit_miss: float = -1.2     # 显式心境未命中（"今天想看治愈"时热血不能顶上来）
    memory_penalty: float = -2.2    # 长期记忆避雷
    temporary_penalty: float = -1.5 # 本轮 avoid_tags/近期负反馈
    quality_popular: float = 1.2    # 站内口碑项（普通模式）
    semantic: float = 0.5           # bge 语义相似（用户高分作品向量 vs 候选，池内归一）


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

    def __init__(
        self,
        client: BangumiClient,
        ltm: LongTermMemory | None = None,
        rerank_weights: RerankWeights | None = None,
    ) -> None:
        self.client = client
        self.ltm = ltm
        self.w = rerank_weights or RerankWeights()
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

    async def _friends_recall(self, cand: dict, stype: int, username: str, seen: set[int]) -> None:
        """好友圈社交召回：好友们想看（≥2 人）或打了高分（≥2 人评分且均分≥8）的未看作品。
        经典 RecSys 社交信号通道——比全站 CF 更贴近"我的圈子"。失败静默。"""
        from ..user_analysis.tool import _fetch_friends

        try:
            friends, _url = await _fetch_friends(username, 10)
        except Exception:  # noqa: BLE001
            return
        names = [f.username if hasattr(f, "username") else str(f.get("username") or f) for f in friends[:10]]
        if not names:
            return
        peers = await gather_limited(
            [self.client.get_all_user_collections(n, stype, None, max_items=800) for n in names],
            host="bangumi",
            return_exceptions=True,
        )
        wish: dict[int, dict] = {}
        rated: dict[int, dict] = {}
        for name, items in zip(names, peers, strict=False):
            if isinstance(items, Exception):
                continue
            for item in items:
                subj = item.get("subject") or {}
                sid = subj.get("id")
                if not sid or int(sid) in seen:
                    continue
                sid = int(sid)
                t = int(item.get("type") or 0)
                rate = int(item.get("rate") or 0)
                if t == 1:
                    slot = wish.setdefault(sid, {"subj": subj, "n": 0})
                    slot["n"] += 1
                if rate > 0:
                    slot = rated.setdefault(sid, {"subj": subj, "rates": []})
                    slot["rates"].append(rate)
        for sid, slot in wish.items():
            if slot["n"] < 2:
                continue
            c = cand.setdefault(sid, _blank(slot["subj"]))
            c["tags"].update(_tag_names(slot["subj"]))
            c["friends"] = f"{slot['n']} 位好友都想看"
            c["external_boost"] = max(c.get("external_boost", 0.0), min(0.15 + 0.05 * slot["n"], 0.35))
        for sid, slot in rated.items():
            if len(slot["rates"]) < 2:
                continue
            avg = sum(slot["rates"]) / len(slot["rates"])
            if avg < 8:
                continue
            c = cand.setdefault(sid, _blank(slot["subj"]))
            c["tags"].update(_tag_names(slot["subj"]))
            c["friends"] = f"好友圈 {len(slot['rates'])} 人均分 {avg:.1f}"
            c["external_boost"] = max(c.get("external_boost", 0.0), min(0.2 + (avg - 8) * 0.1, 0.4))

    async def _cross_media_recall(self, cand: dict, target_stype: int, source_items: list[dict], seen: set[int]) -> None:
        seeds = sorted(source_items, key=lambda it: -(it.get("rate") or 0))[:_CF_SEEDS]
        jobs = []
        meta: list[tuple[int, str]] = []
        for item in seeds:
            sid = (item.get("subject") or {}).get("id")
            src_name = (item.get("subject") or {}).get("name_cn") or (item.get("subject") or {}).get("name")
            if not sid:
                continue
            meta.append((sid, src_name or str(sid)))
            jobs.append(self.client.get_subject_relations(sid))
        if not jobs:
            return
        for (_sid, src_name), rels in zip(meta, await gather_limited(jobs, host="bangumi"), strict=False):
            if isinstance(rels, Exception):
                continue
            for rel in rels or []:
                rid = rel.get("id")
                if not rid or rid in seen or rel.get("type") != target_stype:
                    continue
                relation = str(rel.get("relation") or "")
                if relation and not any(k in relation for k in _CROSS_MEDIA_REL):
                    continue
                c = cand.setdefault(rid, _blank(rel))
                c["graph"].add(f"跨媒体关系：从《{src_name or _sid}》到{relation or '相关条目'}")

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

        async def search_egs(egs):
            try:
                raw = await self.client.search_subjects(egs.title, SUBJECT_TYPE["game"], limit=5)
            except Exception:  # noqa: BLE001
                return egs, []
            return egs, (raw.get("data") or [])

        egs_items = res.data.results[: min(limit * 3, 18)]
        search_results = await gather_limited([search_egs(egs) for egs in egs_items], host="bangumi")
        for pair in search_results:
            if isinstance(pair, Exception):
                continue
            egs, subjects = pair
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
        jobs = []
        targets: list[tuple[int, dict]] = []
        for sid, c in ranked_ids:
            if len(targets) >= _ENRICH_TOP:
                break
            if c["name"] and c["rating"].get("score"):
                continue
            targets.append((sid, c))
            jobs.append(self.client.get_subject(sid))
        if not jobs:
            return
        for (_sid, c), raw in zip(targets, await gather_limited(jobs, host="bangumi"), strict=False):
            if isinstance(raw, Exception):
                continue
            if not c["name"]:
                c["name"] = raw.get("name_cn") or raw.get("name")
                img = raw.get("images") or {}
                c["image"] = c["image"] or img.get("common") or img.get("medium") or img.get("grid")
            c["rating"] = raw.get("rating") or c["rating"]
            c["eps"] = c.get("eps") or _eps_value(raw.get("eps") or raw.get("total_episodes"))
            c["tags"].update(_tag_names(raw))

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
                username = me.get("username") or str(me.get("id"))
            except Exception:  # noqa: BLE001
                # 匿名冷启动：未登录也能推荐——跳过收藏画像/图谱/CF/记忆，
                # 用会话标签(tags/prefer_tags/scenario)+冷启动标签召回+质量重排。
                username = ""


        scenario = args.scenario
        effective_cross_media = args.cross_media or scenario == "cross_media"
        effective_max_episodes = args.max_episodes
        if scenario == "tonight" and effective_max_episodes is None and args.subject_type == "anime":
            effective_max_episodes = 13
        scenario_prefer: list[str] = []
        scenario_notes: list[str] = []
        if scenario == "tonight":
            scenario_prefer.extend(["短篇", "轻松", "日常"])
            scenario_notes.append("今晚看场景：默认偏短篇、低启动成本、少负担。")
        elif scenario == "season":
            scenario_prefer.extend(["新番", "连载", "TV"])
            scenario_notes.append("当季追番场景：若用户明确问某季，优先使用 season_guide_brief(mode=hot)；recommend_subjects 负责补个性化候选。")
        elif scenario == "backlog":
            scenario_notes.append("补 backlog 场景：会把想看列表作为候选池，不再把想看条目当作已看排除。")
        elif scenario == "gal_intro":
            scenario_prefer.extend(["galgame", "视觉小说", "全年龄", "治愈", "恋爱"])
            scenario_notes.append("galgame 入门场景：Bangumi 召回为主，批判空间/VNDB 作为证据补充。")
        elif scenario == "cross_media":
            scenario_notes.append("跨媒体场景：从你喜欢的动画/书籍/game 关系边召回原作、改编、音乐等条目。")

        excluded = {int(x) for x in args.exclude_ids if x}
        if username:
            await emit_tool_progress(tool=self.name, summary=f"拉取 @{username} 的 {args.subject_type} 收藏", current=2, total=6)
            items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        else:
            await emit_tool_progress(tool=self.name, summary="匿名模式：跳过收藏画像，用会话口味标签推荐", current=2, total=6)
            items = []
        if scenario == "backlog":
            seen = {
                it["subject"]["id"]
                for it in items
                if it.get("subject", {}).get("id") and it.get("type") != 1
            } | excluded
        else:
            seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")} | excluded
        watched = [it for it in items if it.get("type") == 2]
        wishlist = [it for it in items if it.get("type") == 1]
        profile = compute_taste_profile(username, watched)
        memory_dislikes: list[str] = []
        feedback_like_tags: list[str] = []
        feedback_dislike_tags: list[str] = []
        feedback_excluded_ids: set[int] = set()
        feedback_summary: dict = {"positive": 0, "negative": 0, "excluded_ids": []}
        aspect_profile: UserAspectProfile | None = None
        if self.ltm is not None and username:
            mem = self.ltm.load_user(username)
            memory_dislikes = [it.value for it in mem.dislikes if it.value.strip()]
            recent_feedback = mem.feedback[-50:]
            feedback_positive = [f for f in recent_feedback if f.signal in {"like", "more"}]
            feedback_negative = [f for f in recent_feedback if f.signal in {"dislike", "less"}]
            feedback_summary["positive"] = len(feedback_positive)
            feedback_summary["negative"] = len(feedback_negative)
            feedback_excluded_ids = {int(f.subject_id) for f in feedback_negative if f.subject_id}
            excluded |= feedback_excluded_ids
            feedback_summary["excluded_ids"] = sorted(feedback_excluded_ids)[:12]
            async def feedback_tags(feedback_items):
                tags: list[str] = []
                for f in feedback_items[:12]:
                    if not f.subject_id:
                        continue
                    try:
                        raw = await self.client.get_subject(int(f.subject_id))
                    except Exception:  # noqa: BLE001
                        continue
                    tags.extend(t.get("name", "") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name"))
                return _unique(tags)[:10]
            tags_pos, tags_neg = await asyncio.gather(feedback_tags(feedback_positive), feedback_tags(feedback_negative))
            feedback_like_tags = tags_pos
            feedback_dislike_tags = tags_neg
            seen |= feedback_excluded_ids
            if args.use_aspect_profile:
                aspect_profile = mem.aspect_profiles.get(args.subject_type)
        all_tags = [t["tag"] for t in profile.top_tags]
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:8]}
        maxw = max(user_tags.values()) if user_tags else 1.0

        subtype_focus_tags = _subtype_tags(args.subject_type, args.book_subtype, args.music_subtype)
        mood = _expand_moods(list(dict.fromkeys(
            (args.tags or []) + args.prefer_tags + scenario_prefer + feedback_like_tags[:4] + subtype_focus_tags
        )))
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
        if scenario == "backlog":
            for raw in wishlist:
                subj = raw.get("subject") or {}
                sid = subj.get("id")
                if not sid or sid in excluded:
                    continue
                c = cand.setdefault(int(sid), _blank(subj))
                c["tags"].update(_tag_names(subj))
                c["external"].add("来自你的想看列表")
                c["external_boost"] = max(c.get("external_boost", 0.0), 0.35)
        mapping_warnings: list[str] = []
        if args.subject_type == "game" and args.use_external_recall:
            mapping_warnings = await self._external_game_recall(cand, seen, args.limit)
        curation_hits = 0
        if args.use_curation:
            for subj, index in await curated_recall_candidates(
                self.client,
                subject_type=args.subject_type,
                tags=recall_tags,
                seen=seen,
                limit=max(args.limit * 3, 12),
            ):
                sid = subj.get("id")
                if not sid:
                    continue
                c = cand.setdefault(int(sid), _blank(subj))
                c["tags"].update(_tag_names(subj))
                c["external"].add(f"入选 Bangumi 目录《{index.get('title') or index.get('id')}》")
                c["external_boost"] = max(c.get("external_boost", 0.0), float(index.get("weight") or 0.16))
                c["external_evidence"].append(
                    RecEvidence(
                        source="Bangumi 目录",
                        score=None,
                        scale=None,
                        count=None,
                        signal="positive",
                        note=f"入选目录《{index.get('title') or index.get('id')}》；{index.get('note') or '社区策展线索'}",
                    )
                )
                curation_hits += 1

        if args.use_friends and username:
            await self._friends_recall(cand, stype, username, seen)

        # 语义召回（实验性，默认关）：用户高分作品向量 → 全站索引近邻，补标签精确匹配的
        # 盲区（"百合"召不回"GL"）。消融显示小样本上会稀释 top-K，待大样本验证再默认开。
        semantic_hits = 0
        if args.use_semantic_recall and args.subject_type == "anime" and watched:
            user_texts = [
                _taste_text(it["subject"].get("name_cn") or it["subject"].get("name") or "",
                            [t.get("name") for t in (it["subject"].get("tags") or []) if isinstance(t, dict)])
                for it in sorted(watched, key=lambda x: -(x.get("rate") or 0))[:40]
                if (it.get("subject") or {}).get("id") and int(it.get("rate") or 0) >= 7
            ]
            try:
                import asyncio as _aio
                recalled = await _aio.to_thread(_semantic_recall, user_texts, seen, max(args.limit * 3, 20))
            except Exception:  # noqa: BLE001 - 索引缺失/模型故障：静默跳过
                recalled = []
            for meta in recalled:
                sid = int(meta["id"])
                if sid in seen:
                    continue
                c = cand.setdefault(sid, _blank({"id": sid, "name_cn": meta.get("name"),
                                                 "tags": [{"name": t} for t in meta.get("tags") or []],
                                                 "rating": {"score": meta.get("score"), "total": meta.get("total")}}))
                c["tags"].update(meta.get("tags") or [])
                c["external"].add("语义相似召回")
                c["external_boost"] = max(c.get("external_boost", 0.0), min(0.1 + meta.get("_sim", 0.0) * 0.3, 0.35))
                semantic_hits += 1

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
        if effective_cross_media:
            if args.subject_type != "anime":
                source_items = await self.client.get_all_user_collections(
                    username, SUBJECT_TYPE["anime"], collection_type=2, max_items=1000
                )
            else:
                source_items = []
                for source_type in ("book", "game"):
                    source_items.extend(
                        await self.client.get_all_user_collections(
                            username, SUBJECT_TYPE[source_type], collection_type=2, max_items=500
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
            return min(len(c["graph"]), 2) * self.w.graph_per_hit

        def cf_bonus(c: dict) -> float:
            return min(c.get("cf", 0.0), self.w.cf_cap)  # 封顶，避免协同召回压过标签口味

        def external_bonus(c: dict) -> float:
            return min(c.get("external_boost", 0.0), self.w.external_cap)

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
            return self.w.memory_penalty if memory_avoidance_hits(c) else 0.0

        def temporary_avoidance_hits(c: dict) -> list[str]:
            haystack = [str(x) for x in (c.get("tags") or set()) | (c.get("matched") or set())]
            if c.get("name"):
                haystack.append(str(c["name"]))
            hits: list[str] = []
            for value in list(args.avoid_tags) + feedback_dislike_tags[:6]:
                key = _norm_title(value)
                if not key:
                    continue
                if any((hk := _norm_title(h)) and (key == hk or key in hk or hk in key) for h in haystack):
                    hits.append(value)
            return list(dict.fromkeys(hits))[:4]

        def temporary_penalty(c: dict) -> float:
            return self.w.temporary_penalty if temporary_avoidance_hits(c) else 0.0

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
                min(pref.weight, 1.0) * self.w.aspect_like * pref.confidence
                for pref in aspect_profile.likes
                if pref.aspect in aspects
            )
            penalty = sum(
                min(pref.weight, 1.0) * self.w.aspect_dislike * pref.confidence
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
            return self.w.explicit_hit if c["matched"] & mood_set else self.w.explicit_miss

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

        # bge 语义特征：标签召回是精确匹配（"百合"≠"GL"），语义向量补上近义/风格盲区
        sem_scores: dict[int, float] = {}
        if args.use_semantic and watched and prelim:
            user_texts = [
                _taste_text(it["subject"].get("name_cn") or it["subject"].get("name") or "",
                            [t.get("name") for t in (it["subject"].get("tags") or []) if isinstance(t, dict)])
                for it in sorted(watched, key=lambda x: -(x.get("rate") or 0))[:40]
                if (it.get("subject") or {}).get("id") and int(it.get("rate") or 0) >= 7
            ]
            cand_pairs = [(sid, _taste_text(str(c.get("name") or ""), c.get("tags"))) for sid, c in prelim]
            cand_pairs = [(sid, txt) for sid, txt in cand_pairs if txt]
            if user_texts and cand_pairs:
                try:
                    import asyncio as _aio
                    sims = await _aio.to_thread(_semantic_scores, user_texts, [t for _, t in cand_pairs])
                    sem_scores = {sid: s for (sid, _), s in zip(cand_pairs, sims, strict=False)}
                except Exception as e:  # noqa: BLE001 - 模型缺失/加载失败：静默降级不影响主链路
                    scenario_notes.append(f"语义特征不可用（{type(e).__name__}），已按其余信号重排。")

        def semantic_bonus(c: dict, sid: int) -> float:
            return self.w.semantic * sem_scores.get(sid, 0.0)

        def feature_vector(sid: int, c: dict) -> dict[str, float]:
            """LTR 训练用：候选的原始特征分量（未乘权重的语义/质量用裸值，其余为已加权 bonus）。
            scripts.train_ltr 收集 (features, 是否命中 hold-out) 训学习排序，替代手调线性和。"""
            return {
                "affinity": affinity(c),
                "graph": graph_bonus(c),
                "cf": cf_bonus(c),
                "external": external_bonus(c),
                "explicit": explicit_tag_adjust(c),
                "memory_pen": memory_penalty(c),
                "temporary_pen": temporary_penalty(c),
                "aspect": aspect_bonus(c),
                "subtype_pen": media_subtype_penalty(c),
                "semantic": sem_scores.get(sid, 0.0),
                "quality": _quality_popular(c["rating"]),
            }

        def score(sid: int, c: dict) -> float:
            if args.niche:  # 挖冷门：协同偏热门，权重压低
                return (0.5 * affinity(c) + 0.5 * graph_bonus(c)
                        + 0.4 * cf_bonus(c) + external_bonus(c)
                        + explicit_tag_adjust(c) + memory_penalty(c) + temporary_penalty(c)
                        + aspect_bonus(c) + media_subtype_penalty(c) + semantic_bonus(c, sid)
                        + 2.0 * _quality_niche(c["rating"]))
            return (
                affinity(c) + graph_bonus(c) + cf_bonus(c) + external_bonus(c)
                + explicit_tag_adjust(c) + memory_penalty(c) + temporary_penalty(c)
                + aspect_bonus(c) + media_subtype_penalty(c) + semantic_bonus(c, sid)
                + self.w.quality_popular * _quality_popular(c["rating"])
            )

        def cf_reason(c: dict) -> list[str]:
            if not c["cf_from"]:
                return []
            names = [n for n in (fav_names.get(s) for s in c["cf_from"]) if n]
            if not names:
                return ["相似口味用户的选择"]
            return [f"看过《{names[0]}》的人也在看" + (" 等" if len(c["cf_from"]) > 1 else "")]

        ranked = sorted(prelim, key=lambda kv: -score(kv[0], kv[1]))
        out: list[RecItem] = []
        seen_series: set[str] = set()
        seen_ids: set[int] = set()
        pool_limit = min(args.limit * 2, 20) if args.enrich_evidence else args.limit
        if args.export_features:
            pool_limit = 60  # LTR 训练：导出更大候选池以容纳 hold-out 正样本（训练专用路径）
        series_contexts: dict[int, tuple] = {}
        if args.use_series:
            series_targets = [
                (sid, c)
                for sid, c in ranked[: max(pool_limit * 2, args.limit)]
                if c.get("name")
            ][:30]
            if series_targets:
                series_results = await gather_limited(
                    [self._series_context(sid, stype, seen) for sid, _c in series_targets],
                    host="bangumi",
                )
                for (sid, _c), res in zip(series_targets, series_results, strict=False):
                    if not isinstance(res, Exception):
                        series_contexts[sid] = res

        def explanation_parts(c: dict, r_id: int, r_name: str, reasons: list[str], subtype_notes: list[str]) -> tuple[list[str], list[str], list[str], dict, list[str]]:
            why_recalled = []
            if c["matched"]:
                why_recalled.append("标签召回：" + "、".join(sorted(c["matched"])[:4]))
            if c["graph"]:
                why_recalled.append("图谱召回：" + "、".join(sorted(c["graph"])[:2]))
            if c.get("friends"):
                why_recalled.append("好友圈信号：" + str(c["friends"]))
            if c.get("cf_from"):
                why_recalled.extend(cf_reason(c)[:1])
            if c.get("external"):
                why_recalled.append("外部证据：" + "、".join(sorted(c["external"])[:2]))
            fit_points = []
            if explicit_matches := sorted(c["matched"] & mood_set):
                fit_points.append("本轮偏好命中：" + "、".join(explicit_matches[:4]))
            if aspect_like_hits(c):
                fit_points.append("长期好球区：" + "、".join(aspect_like_hits(c)[:3]))
            if subtype_notes:
                fit_points.extend(subtype_notes[:2])
            risks = []
            if aspect_dislike_hits(c):
                risks.append("可能触及雷区：" + "、".join(aspect_dislike_hits(c)[:3]))
            if temporary_avoidance_hits(c):
                risks.append("接近近期避雷/本轮避雷：" + "、".join(temporary_avoidance_hits(c)[:3]))
            if memory_avoidance_hits(c):
                risks.append("接近长期避雷：" + "、".join(memory_avoidance_hits(c)[:3]))
            eps = c.get("eps")
            if effective_max_episodes and eps and eps > effective_max_episodes:
                risks.append(f"篇幅 {eps} 集，超过本轮短篇目标")
            heat = {
                "bangumi_score": (c.get("rating") or {}).get("score"),
                "rank": (c.get("rating") or {}).get("rank"),
                "evidence": [e.model_dump(mode="json", exclude_none=True) for e in c.get("external_evidence", [])[:3]],
                "badges": _quality_badges(list(c.get("external_evidence", []))),
            }
            if scenario == "tonight":
                next_step = [f"今晚先看《{r_name}》第 1 集试口味", "如果觉得节奏不对，反馈“少来这种/换短一点”"]
            elif scenario == "backlog":
                next_step = [f"从想看列表开《{r_name}》1-2 集", "看完后可写回在看或移出想看"]
            elif scenario == "gal_intro":
                next_step = [f"先查《{r_name}》购买/入门入口", "可再问“无剧透评价/适合我吗”"]
            elif scenario == "cross_media":
                next_step = [f"把《{r_name}》作为跨媒体延伸入口", "可继续查原作/改编关系图"]
            else:
                next_step = [f"先看《{r_name}》的无剧透评价", "喜欢/不喜欢后用反馈按钮继续重排"]
            return (
                why_recalled[:5] or reasons[:3],
                fit_points[:5] or reasons[:3],
                risks[:5],
                heat,
                next_step[:4],
            )

        for sid, c in ranked:
            if not c["name"]:
                continue  # 协同召回候选未被 enrich 补到名，跳过
            eps = c.get("eps")
            if effective_max_episodes is not None and isinstance(eps, int) and eps > effective_max_episodes:
                continue
            r_id, r_name, r_img, r_rating, extra = sid, c["name"], c.get("image"), c["rating"], []
            if args.use_series:
                entry, siblings = series_contexts.get(sid) or await self._series_context(sid, stype, seen)
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
            why_recalled, fit_points, risks, heat, next_step = explanation_parts(c, r_id, r_name, reasons, subtype_notes)
            out.append(RecItem(
                id=r_id, name=r_name, score=round(score(r_id, c), 3),
                reasons=reasons,
                why_recalled=why_recalled,
                fit_points=fit_points,
                risks=risks,
                heat=heat,
                next_step=next_step,
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
                features=feature_vector(r_id, c) if args.export_features else None,
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
        notes.extend(scenario_notes)
        if feedback_like_tags or feedback_dislike_tags:
            notes.append(
                "已读取近期推荐反馈：正向标签 "
                + ("、".join(feedback_like_tags[:5]) or "无")
                + "；负向标签 "
                + ("、".join(feedback_dislike_tags[:5]) or "无")
            )
        applied_constraints: list[str] = []
        if args.exclude_ids:
            applied_constraints.append(f"已排除上轮/指定候选 {len(args.exclude_ids)} 个")
        if effective_max_episodes is not None:
            applied_constraints.append(f"短篇约束：eps ≤ {effective_max_episodes}")
        if args.avoid_tags:
            applied_constraints.append("本轮临时避雷：" + "、".join(args.avoid_tags[:8]))
        if args.prefer_tags:
            applied_constraints.append("本轮偏好：" + "、".join(args.prefer_tags[:8]))
        if scenario_prefer:
            applied_constraints.append("场景偏好：" + "、".join(scenario_prefer[:8]))
        if feedback_excluded_ids:
            applied_constraints.append(f"已排除近期负反馈条目 {len(feedback_excluded_ids)} 个")
        if effective_cross_media:
            applied_constraints.append("已启用跨媒体召回")
        if args.use_curation and curation_hits:
            applied_constraints.append(f"已启用 Bangumi 目录策展召回：{curation_hits} 个候选")
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
                scenario=scenario,
                feedback_policy={
                    **feedback_summary,
                    "positive_tags": feedback_like_tags[:10],
                    "negative_tags": feedback_dislike_tags[:10],
                    "principle": "近期反馈作为弱 rerank 信号；本轮显式要求优先。",
                },
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
        async def fetch_review(item: RecItem):
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
                return item, None
            return item, res

        pairs = await gather_limited([fetch_review(item) for item in items], host="bangumi")
        for pair in pairs:
            if isinstance(pair, Exception) or pair is None:
                continue
            item, res = pair
            if res is None:
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
