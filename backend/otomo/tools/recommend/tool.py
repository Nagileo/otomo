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

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_RECALL_PER_TAG = 50
_MAX_RECALL_TAGS = 8
_MAX_COLLECT = 800
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


def _blank(x: dict) -> dict:
    img = x.get("images") or {}
    return {
        "name": x.get("name_cn") or x.get("name"),
        "matched": set(), "graph": set(), "weight": 0.0,
        "rating": x.get("rating") or {},
        "image": img.get("common") or img.get("medium") or img.get("grid"),
        "cf": 0.0, "cf_from": set(),
    }


def _blank_id() -> dict:
    """协同召回新候选（i2i 只给 subject_id+score，name/rating/image 待 enrich 补）。"""
    return {
        "name": None, "matched": set(), "graph": set(), "weight": 0.0,
        "rating": {}, "image": None, "cf": 0.0, "cf_from": set(),
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


class RecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    score: float
    reasons: list[str]
    bangumi_score: float | None = None
    rank: int | None = None
    image: str | None = None


class RecommendResult(BaseModel):
    subject_type: str
    based_on_tags: list[str]
    mode: str = "normal"
    items: list[RecItem] = Field(default_factory=list)


class RecommendTool(Tool):
    name = "recommend_subjects"
    description = (
        "根据用户口味推荐 TA 没看过的作品，支持 anime/book/music/game/real。"
        "多路召回（标签 + 图谱：你爱的作品的监督/制作组的其他作品）。"
        "重度用户/挖冷门设 niche=true；想跳出舒适区设 explore=true。"
        "用于『据我口味推荐 / 类似X / 挖点冷门 / 换个口味 / 今天想看治愈的』。"
    )
    args_model = RecommendArgs
    result_model = RecommendResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _tag_recall(self, cand, stype, recall_tags, user_tags, maxw, mood, seen, niche):
        offset = _NICHE_OFFSET if niche else 0
        for tag in recall_tags:
            res = await self.client.search_subjects(
                "", stype, sort="heat", limit=_RECALL_PER_TAG, tags=[tag], offset=offset
            )
            w = maxw if tag in mood else user_tags.get(tag, 1.0)
            for x in res.get("data", []):
                sid = x.get("id")
                if not sid or sid in seen:
                    continue
                c = cand.setdefault(sid, _blank(x))
                c["matched"].add(tag)
                c["weight"] += w

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
        username = args.username or (await self.client.get_me()).get("username")

        items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")}
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        all_tags = [t["tag"] for t in profile.top_tags]
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:8]}
        maxw = max(user_tags.values()) if user_tags else 1.0

        mood = _expand_moods(args.tags or [])
        core = all_tags[2:8] if args.explore else all_tags[:6]  # explore：用次级标签拓展
        recall_tags = list(dict.fromkeys(mood + core))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        await self._tag_recall(cand, stype, recall_tags, user_tags, maxw, mood, seen, args.niche)

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

        def affinity(c: dict) -> float:
            return c["weight"] / maxw  # ≈ 加权命中标签数

        def graph_bonus(c: dict) -> float:
            # 图谱是弱信号（同制作组≠一定喜欢），封顶 0.9，低于协同(1.5)：协同>图谱
            return min(len(c["graph"]), 2) * 0.45

        def cf_bonus(c: dict) -> float:
            return min(c.get("cf", 0.0), _CF_CAP)  # 封顶，避免协同召回压过标签口味

        # 预排（不含质量）→ 给 top 候选补名/评分 → 终排（含质量）
        prelim = sorted(
            cand.items(), key=lambda kv: -(affinity(kv[1]) + graph_bonus(kv[1]) + cf_bonus(kv[1]))
        )[:30]
        await self._enrich(prelim)

        def score(c: dict) -> float:
            if args.niche:  # 挖冷门：协同偏热门，权重压低
                return (0.5 * affinity(c) + 0.5 * graph_bonus(c)
                        + 0.4 * cf_bonus(c) + 2.0 * _quality_niche(c["rating"]))
            return affinity(c) + graph_bonus(c) + cf_bonus(c) + 1.2 * _quality_popular(c["rating"])

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
        for sid, c in ranked:
            if not c["name"]:
                continue  # 协同召回候选未被 enrich 补到名，跳过
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
            out.append(RecItem(
                id=r_id, name=r_name, score=round(score(c), 3),
                reasons=sorted(c["matched"]) + sorted(c["graph"]) + cf_reason(c) + extra,
                bangumi_score=(r_rating or {}).get("score"),
                rank=(r_rating or {}).get("rank"),
                image=r_img,
            ))
            if len(out) >= args.limit:
                break

        mode = "niche" if args.niche else ("explore" if args.explore else "normal")
        return ToolResult(
            ok=True,
            data=RecommendResult(
                subject_type=args.subject_type, based_on_tags=recall_tags, mode=mode, items=out
            ),
            sources=[
                Citation(title=it.name, url=f"https://bgm.tv/subject/{it.id}", source="bangumi", image=it.image)
                for it in out
            ],
        )
