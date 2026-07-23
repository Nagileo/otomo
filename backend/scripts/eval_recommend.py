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
from otomo.tools.recommend.tool import RecommendArgs, RecommendTool, RerankWeights, _series_key  # noqa: E402

_STYPE_NUM = {"anime": 2, "book": 1, "music": 3, "game": 4, "real": 6}

# 三组召回配置（use_series 是入口回溯后处理、非召回通道，各组保持默认开；
# enrich_evidence 只补展示证据不参与排序，全关提速）
CONFIGS: dict[str, dict[str, Any]] = {
    "纯标签": dict(use_graph=False, use_cf=False, use_aspect_profile=False,
                   use_curation=False, use_external_recall=False, use_semantic=False),
    "+图谱": dict(use_graph=True, use_cf=False, use_aspect_profile=False,
                  use_curation=False, use_external_recall=False, use_semantic=False),
    "全开": dict(use_graph=True, use_cf=True, use_aspect_profile=True,
                 use_curation=True, use_external_recall=True, use_semantic=False),
    "全开+语义重排": dict(use_graph=True, use_cf=True, use_aspect_profile=True,
                          use_curation=True, use_external_recall=True, use_semantic=True),
    "全开+语义召回": dict(use_graph=True, use_cf=True, use_aspect_profile=True,
                          use_curation=True, use_external_recall=True, use_semantic=True,
                          use_semantic_recall=True),
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


def _sample_weights(rng: random.Random) -> RerankWeights:
    """随机采样一组 rerank 权重（默认值 × log-uniform [0.4, 2.5]）。

    诚实说明：离线匿名评测里 aspect/mood/memory 罚项不激活（无 LTM、无本轮对话），
    可被离线指标观测的只有 4 个活跃权重——其余维持默认，调它们需要在线反馈数据。
    """
    d = RerankWeights()

    def s(v: float, lo: float = 0.4, hi: float = 2.5) -> float:
        return round(v * math.exp(rng.uniform(math.log(lo), math.log(hi))), 3)

    return RerankWeights(
        graph_per_hit=s(d.graph_per_hit),
        cf_cap=s(d.cf_cap),
        external_cap=s(d.external_cap),
        quality_popular=s(d.quality_popular),
    )


async def eval_user(
    client: BangumiClient,
    username: str,
    args: argparse.Namespace,
    k: int,
    rng: random.Random,
    configs: dict[str, tuple[dict[str, Any], RerankWeights | None]],
    *,
    quiet: bool = False,
) -> dict[str, list[tuple[float, float, float]]]:
    """单用户全部 trials×configs 的 (hr, ndcg, hr_no_series)。收藏池不足返回 {}。"""
    stype = _STYPE_NUM[args.subject_type]
    try:
        all_items = await client.get_all_user_collections(username, stype, None, max_items=1000)
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"  [{username}] 收藏拉取失败 {type(e).__name__}")
        return {}
    pool = [
        it for it in all_items
        if int(it.get("rate") or 0) >= args.min_rate and (it.get("subject") or {}).get("id")
    ]
    if len(pool) < args.holdout + 3:
        if not quiet:
            print(f"  [{username}] 评分>={args.min_rate} 池仅 {len(pool)} 部，跳过")
        return {}
    scores: dict[str, list[tuple[float, float, float]]] = {name: [] for name in configs}
    for _trial in range(args.trials):
        held = rng.sample(pool, args.holdout)
        held_ids = {int(it["subject"]["id"]) for it in held}
        held_names = {int(it["subject"]["id"]): (it["subject"].get("name_cn") or it["subject"].get("name") or "?")
                      for it in held}
        profile_series: set[str] = set()
        for it in all_items:
            if int((it.get("subject") or {}).get("id") or 0) not in held_ids:
                profile_series |= _series_stems(it["subject"].get("name_cn") or it["subject"].get("name") or "")
        proxy = HoldoutClient(client, held_ids)
        for name, (flags, weights) in configs.items():
            tool = RecommendTool(proxy, rerank_weights=weights)
            res = await tool.run(RecommendArgs(
                subject_type=args.subject_type, username=username, limit=k,
                enrich_evidence=False, **flags,
            ))
            if not res.ok or res.data is None:
                scores[name].append((0.0, 0.0, 0.0))
                continue
            top = [int(it.id) for it in res.data.items[:k]]
            hits = [(rank, sid) for rank, sid in enumerate(top, 1) if sid in held_ids]
            series_hits = [sid for _, sid in hits if _series_stems(held_names.get(sid, "")) & profile_series]
            scores[name].append((
                len(hits) / args.holdout,
                _ndcg_at_k([r for r, _ in hits], args.holdout, k),
                (len(hits) - len(series_hits)) / args.holdout,
            ))
    return scores


async def _multi_user_eval(args: argparse.Namespace, k: int) -> None:
    """多用户评测/权重搜索：指标 = 全部 (user, trial) 的平均，比单用户 3 试验稳得多。"""
    spec = json.loads(Path(args.users_file).read_text(encoding="utf-8"))
    users = [u["username"] for u in spec.get("users") or []][: args.max_eval_users]
    if not users:
        sys.exit(f"{args.users_file} 里没有评测用户")
    rng = random.Random(args.seed)
    if args.search:
        full_flags = CONFIGS["全开"]
        configs: dict[str, tuple[dict[str, Any], RerankWeights | None]] = {"默认权重": (full_flags, None)}
        for i in range(args.search):
            configs[f"w{i + 1}"] = (full_flags, _sample_weights(rng))
    else:
        configs = {name: (flags, None) for name, flags in CONFIGS.items()}
    print(f"{len(users)} 用户 × {args.trials} 试验 × {len(configs)} 配置 · hold-out {args.holdout} · K={k}\n")

    agg: dict[str, list[tuple[float, float, float]]] = {name: [] for name in configs}
    async with BangumiClient() as client:
        for idx, user in enumerate(users, 1):
            print(f"—— [{idx}/{len(users)}] {user}")
            per = await eval_user(client, user, args, k, rng, configs)
            for name, rows in per.items():
                agg[name].extend(rows)

    rows_out: list[tuple[str, float, float, float, int]] = []
    for name, rows in agg.items():
        if not rows:
            continue
        rows_out.append((
            name,
            statistics.mean(r[0] for r in rows),
            statistics.mean(r[1] for r in rows),
            statistics.mean(r[2] for r in rows),
            len(rows),
        ))
    rows_out.sort(key=lambda x: -x[2])  # 按 NDCG 排
    print(f"\n{'配置':<10}{'HR@' + str(k):<10}{'NDCG@' + str(k):<12}{'HR(去续作)':<12}{'样本':<6}")
    for name, hr, ndcg, hrns, n in rows_out:
        print(f"{name:<10}{hr:<10.3f}{ndcg:<12.3f}{hrns:<12.3f}{n:<6}")
    report = {
        "users": users, "params": vars(args),
        "results": [
            {"config": name, "hr": hr, "ndcg": ndcg, "hr_no_series": hrns, "samples": n,
             "weights": (configs[name][1].model_dump() if configs[name][1] else None)}
            for name, hr, ndcg, hrns, n in rows_out
        ],
    }
    if args.search and rows_out:
        best = rows_out[0][0]
        if configs[best][1] is not None:
            print(f"\n最优配置 {best}: {configs[best][1].model_dump()}")
        else:
            print("\n默认权重仍是最优——搜索未找到显著更好的配置（样本诚实说话）。")
    if args.json_report:
        out = Path(args.json_report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out}")


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
    ap.add_argument("--users-file", default="", help="多用户评测：scripts.build_eval_users 产出的 json")
    ap.add_argument("--max-eval-users", type=int, default=12)
    ap.add_argument("--search", type=int, default=0, help="随机搜索 N 组 rerank 权重（另含默认基线，需 --users-file）")
    args = ap.parse_args()
    k = min(args.k, 20)

    if args.users_file:
        await _multi_user_eval(args, k)
        return

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
