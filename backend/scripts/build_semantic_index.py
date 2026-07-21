"""构建全站语义召回索引（推荐第一刀）。

现状：bge 只做重排——候选池里没有的救不回来，而标签召回是精确匹配（"百合"召不回
标"GL"的），长尾盲区在**召回侧**。本脚本把热度靠前的动画（评分人数过千）拉出来，
bge embedding 落盘成本地索引；推荐时用户向量取语义 top-K 直接进候选池，补标签盲区。

方法：sort=heat 按年份分片各拉 N（Bangumi search 单查 total 上限 1000），去重、
过滤评分人数 >= min-votes，取 名称+标签 文本做 bge 向量，落盘 npz(ids/vecs/meta)。
几千部 bge-small 几分钟建完，零外部依赖；索引缺失时推荐自动跳过语义召回（不硬失败）。

用法（backend/ 下）：
  python -m scripts.build_semantic_index --since 2010 --min-votes 1000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.tools._rag import _embedder  # noqa: E402
from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.recommend.tool import _taste_text  # noqa: E402

INDEX_PATH = Path(__file__).resolve().parents[1] / "otomo" / "data" / "semantic_index.npz"


async def _collect(client: BangumiClient, since: int, min_votes: int, per_year: int) -> dict[int, dict]:
    from datetime import date

    pool: dict[int, dict] = {}
    for year in range(since, date.today().year + 1):
        offset = 0
        kept_this_year = 0
        while offset < per_year:
            r = await client.search_subjects(
                keyword="", subject_type=2, sort="heat", limit=50, offset=offset,
                air_date=[f">={year}-01-01", f"<{year + 1}-01-01"],
            )
            rows = r.get("data") or []
            if not rows:
                break
            for it in rows:
                sid = it.get("id")
                rating = it.get("rating") or {}
                if not sid or int(rating.get("total") or 0) < min_votes:
                    continue
                if sid in pool:
                    continue
                pool[sid] = {
                    "id": int(sid),
                    "name": it.get("name_cn") or it.get("name") or "",
                    "tags": [t.get("name") for t in (it.get("tags") or []) if isinstance(t, dict) and t.get("name")][:12],
                    "score": rating.get("score"),
                    "total": int(rating.get("total") or 0),
                }
                kept_this_year += 1
            offset += 50
        print(f"  {year}: 累计 {len(pool)}（本年 +{kept_this_year}）")
    return pool


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", type=int, default=2010)
    ap.add_argument("--min-votes", type=int, default=1000, help="最低评分人数（过滤长尾冷门）")
    ap.add_argument("--per-year", type=int, default=300, help="每年最多取多少部（heat 序）")
    ap.add_argument("--out", default=str(INDEX_PATH))
    args = ap.parse_args()

    async with BangumiClient() as client:
        print(f"拉取 {args.since}~今 评分人数>={args.min_votes} 的热门动画…")
        pool = await _collect(client, args.since, args.min_votes, args.per_year)
    if not pool:
        sys.exit("没有拉到任何条目")

    items = list(pool.values())
    texts = [_taste_text(it["name"], it["tags"]) for it in items]
    print(f"共 {len(items)} 部，开始 bge 向量化…")
    emb = _embedder()
    vecs = emb.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=True)
    vecs = np.asarray(vecs, dtype=np.float32)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        ids=np.array([it["id"] for it in items], dtype=np.int64),
        vecs=vecs,
        meta=np.array([json.dumps(it, ensure_ascii=False) for it in items], dtype=object),
    )
    print(f"落盘 {out}: {len(items)} 部 × {vecs.shape[1]} 维")


if __name__ == "__main__":
    asyncio.run(main())
