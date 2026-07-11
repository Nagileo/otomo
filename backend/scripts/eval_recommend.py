"""R4 · 离线 leave-N-out 推荐评测（docs/19）。

方法：把用户收藏中评分 >= min-rate 的作品随机 hold-out N 部/试验 → 用其余收藏建画像
跑推荐 → 看被藏起来的高分作能否进 top-K。指标 HR@K（= 命中数/N）与 NDCG@K。
对比三组召回配置：纯标签 / +图谱 / 全开(+CF+aspect+策展)，量化各召回通道的真实贡献。

诚实性处理：
- hold-out 通过**包装 client** 实现（get_all_user_collections 滤掉被藏作品）——画像构建
  和"已收藏过滤"同时排除，不改生产代码；其余方法透传。
- 续作是 easy win（画像里有前作时续作天然易召回），单独标记 series 命中并给
  HR@K(去续作) 一列，防止指标虚高。
- 三组共享同一 client 进程内缓存：同一试验内各配置看到同一份外部数据，公平可比。

用法（backend/ 下）：
  python -m scripts.eval_recommend --trials 3 --holdout 5 --k 10 --seed 42
  python -m scripts.eval_recommend --username nagi --json-report ../eval/recommend_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.recommend.tool import RecommendArgs, RecommendTool, _series_key  # noqa: E402

_STYPE_NUM = {"anime": 2, "book": 1, "music": 3, "game": 4, "real": 6}

# 三组召回配置（use_series 是入口回溯后处理、非召回通道，各组保持默认开；
# enrich_evidence 只补展示证据不参与排序，全关提速）
CONFIGS: dict[str, dict[str, Any]] = {
    "纯标签": dict(use_graph=False, use_cf=False, use_aspect_profile=False,
                   use_curation=False, use_external_recall=False),
    "+图谱": dict(use_graph=True, use_cf=False, use_aspect_profile=False,
                  use_curation=False, use_external_recall=False),
    "全开": dict(use_graph=True, use_cf=True, use_aspect_profile=True,
                 use_curation=True, use_external_recall=True),
}


class HoldoutClient:
    """包装真实 client：收藏读取里滤掉 hold-out 作品，其余方法透传。
    这让画像构建与 seen 过滤同时"看不见"被藏作品——它们才可能被召回。"""

    def __init__(self, inner: BangumiClient, holdout_ids: set[int]) -> None:
        self._inner = inner
        self._holdout = holdout_ids

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def get_all_user_collections(self, *args, **kwargs):
        items = await self._inner.get_all_user_collections(*args, **kwargs)
        return [
            it for it in items
            if int((it.get("subject") or {}).get("id") or 0) not in self._holdout
        ]


def _series_stems(name: str) -> set[str]:
    """easy-win 标记用的系列主干：在工具自身 _series_key 之上，再按日式副标题
    波浪线（玉响～hitotose～ / 玉响～more aggressive～）切出主干。只处理这个明确
    模式、不做激进前缀匹配（"少女乐队"vs"少女歌剧"这类同前缀不同作不能误伤）；
    保守启发式，奇异命名可能漏标（漏标只会让 easy-win 少扣、指标更保守）。"""
    key = _series_key(name)
    stems = {key}
    for sep in ("～", "〜"):
        stem = key.split(sep)[0].strip()
        if len(stem) >= 2:
            stems.add(stem)
    return stems


def _ndcg_at_k(hit_ranks: list[int], n_relevant: int, k: int) -> float:
    dcg = sum(1.0 / math.log2(rank + 1) for rank in hit_ranks)  # rank 从 1 起
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(n_relevant, k) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def _fmt(mean: float, std: float) -> str:
    return f"{mean:.3f}±{std:.3f}"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--username", default="", help="不传则用当前 token 账号")
    ap.add_argument("--subject-type", default="anime", choices=list(_STYPE_NUM))
    ap.add_argument("--min-rate", type=int, default=8, help="hold-out 池的最低用户评分")
    ap.add_argument("--holdout", type=int, default=5, help="每次试验藏起的作品数 N")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--k", type=int, default=10, help="top-K（受工具 limit<=20 约束）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json-report", default="")
    args = ap.parse_args()
    k = min(args.k, 20)

    async with BangumiClient() as client:
        username = args.username or (await client.get_me())["username"]
        stype = _STYPE_NUM[args.subject_type]
        all_items = await client.get_all_user_collections(username, stype, None, max_items=1000)
        pool = [
            it for it in all_items
            if int(it.get("rate") or 0) >= args.min_rate and (it.get("subject") or {}).get("id")
        ]
        if len(pool) < args.holdout + 3:
            sys.exit(f"评分>={args.min_rate} 的收藏只有 {len(pool)} 部，不够 hold-out（需要 >= {args.holdout + 3}）")
        print(f"用户 {username} · {args.subject_type} 收藏 {len(all_items)} 部，"
              f"评分>={args.min_rate} 池 {len(pool)} 部 · {args.trials} 试验 × hold-out {args.holdout} · K={k}\n")

        rng = random.Random(args.seed)
        report: dict[str, Any] = {"username": username, "params": vars(args), "trials": []}
        # scores[config] = list of (hr, ndcg, hr_noseries)
        scores: dict[str, list[tuple[float, float, float]]] = {name: [] for name in CONFIGS}

        for trial in range(1, args.trials + 1):
            held = rng.sample(pool, args.holdout)
            held_ids = {int(it["subject"]["id"]) for it in held}
            held_names = {int(it["subject"]["id"]): (it["subject"].get("name_cn") or it["subject"].get("name") or "?")
                          for it in held}
            # 画像侧（未被藏）作品的系列键——用于标记"续作 easy win"命中
            profile_series: set[str] = set()
            for it in all_items:
                if int((it.get("subject") or {}).get("id") or 0) not in held_ids:
                    profile_series |= _series_stems(it["subject"].get("name_cn") or it["subject"].get("name") or "")
            proxy = HoldoutClient(client, held_ids)
            print(f"—— 试验 {trial}：藏起 {'、'.join(held_names.values())}")
            trial_rec: dict[str, Any] = {"held": [{"id": i, "name": n} for i, n in held_names.items()], "configs": {}}

            for name, flags in CONFIGS.items():
                tool = RecommendTool(proxy)
                res = await tool.run(RecommendArgs(
                    subject_type=args.subject_type, username=username, limit=k,
                    enrich_evidence=False, **flags,
                ))
                if not res.ok or res.data is None:
                    print(f"   {name}: 推荐失败 {res.error}")
                    scores[name].append((0.0, 0.0, 0.0))
                    trial_rec["configs"][name] = {"error": res.error}
                    continue
                top = [int(it.id) for it in res.data.items[:k]]
                why_by_id = {int(it.id): it.why_recalled for it in res.data.items[:k]}
                hits = [(rank, sid) for rank, sid in enumerate(top, 1) if sid in held_ids]
                hit_ranks = [r for r, _ in hits]
                series_hits = [
                    sid for _, sid in hits
                    if _series_stems(held_names.get(sid, "")) & profile_series
                ]
                hr = len(hits) / args.holdout
                ndcg = _ndcg_at_k(hit_ranks, args.holdout, k)
                hr_ns = (len(hits) - len(series_hits)) / args.holdout
                scores[name].append((hr, ndcg, hr_ns))
                detail = "、".join(
                    f"{held_names[sid]}@{r}" + ("(续作)" if sid in series_hits else "")
                    for r, sid in hits
                ) or "无命中"
                print(f"   {name}: HR={hr:.2f} NDCG={ndcg:.3f}  命中 {detail}")
                trial_rec["configs"][name] = {
                    "top_k": top, "hits": [{"rank": r, "id": sid, "name": held_names[sid],
                                            "series_easy_win": sid in series_hits,
                                            "why_recalled": why_by_id.get(sid, [])} for r, sid in hits],
                    "hr": hr, "ndcg": ndcg, "hr_no_series": hr_ns,
                }
            report["trials"].append(trial_rec)
            print()

        print(f"{'配置':<8}{'HR@' + str(k):<16}{'NDCG@' + str(k):<16}{'HR@' + str(k) + '(去续作)':<16}")
        summary: dict[str, Any] = {}
        for name, rows in scores.items():
            hrs, ndcgs, hrns = ([r[i] for r in rows] for i in range(3))
            std = statistics.pstdev if len(rows) > 1 else lambda _: 0.0
            line = (_fmt(statistics.mean(hrs), statistics.pstdev(hrs) if len(rows) > 1 else 0.0),
                    _fmt(statistics.mean(ndcgs), statistics.pstdev(ndcgs) if len(rows) > 1 else 0.0),
                    _fmt(statistics.mean(hrns), statistics.pstdev(hrns) if len(rows) > 1 else 0.0))
            print(f"{name:<8}{line[0]:<16}{line[1]:<16}{line[2]:<16}")
            summary[name] = {"hr_mean": statistics.mean(hrs), "ndcg_mean": statistics.mean(ndcgs),
                             "hr_no_series_mean": statistics.mean(hrns), "runs": len(rows)}
        report["summary"] = summary

        if args.json_report:
            out = Path(args.json_report)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nJSON 报告 → {out}")


if __name__ == "__main__":
    asyncio.run(main())
