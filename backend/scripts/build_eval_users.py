"""构建离线评测用户池：好友图两跳抽样公开收藏用户。

R4 评测此前只有自己一个 hold-out 用户（3 试验），HR 波动大到无法指导调参。
本脚本从种子用户的好友图做两跳 BFS，筛"看过 >= min-watched 且收藏公开"的用户
落盘 eval/holdout_users.json，给 eval_recommend --users-file / --search 当评测集。

只读公开数据（好友页 + 收藏计数），带限速；用户名仅用于离线评测不入库。

用法（backend/ 下）：
  python -m scripts.build_eval_users --seed-user luorily --max-users 40
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.user_analysis.tool import _fetch_friends  # noqa: E402


async def _watched_count(client: BangumiClient, username: str) -> int:
    try:
        page = await client.get_user_collections(username, 2, 2, limit=1)
    except Exception:  # noqa: BLE001 - 私密收藏/不存在 → 0
        return 0
    return int(page.get("total") or 0)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed-user", default="", help="不传则用当前 token 账号")
    ap.add_argument("--min-watched", type=int, default=100)
    ap.add_argument("--max-users", type=int, default=40)
    ap.add_argument("--per-hop", type=int, default=24, help="每个用户最多展开的好友数")
    ap.add_argument("--out", default="../eval/holdout_users.json")
    args = ap.parse_args()

    async with BangumiClient() as client:
        seed = args.seed_user or (await client.get_me())["username"]
        visited: set[str] = {seed}
        frontier = [seed]
        candidates: list[str] = []
        for hop in (1, 2):
            nxt: list[str] = []
            for user in frontier:
                friends, note = await _fetch_friends(user, args.per_hop)
                if note:
                    print(f"  [{user}] {note}")
                for fr in friends:
                    if fr.username not in visited:
                        visited.add(fr.username)
                        candidates.append(fr.username)
                        nxt.append(fr.username)
            print(f"hop {hop}: 新增 {len(nxt)} 个候选（累计 {len(candidates)}）")
            frontier = nxt
            if len(candidates) >= args.max_users * 4:
                break

        rows: list[dict] = []
        for user in candidates:
            n = await _watched_count(client, user)
            mark = "✓" if n >= args.min_watched else " "
            print(f"  {mark} {user}: 看过 {n}")
            if n >= args.min_watched:
                rows.append({"username": user, "watched_total": n})
            if len(rows) >= args.max_users:
                break

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"seed": seed, "min_watched": args.min_watched, "users": rows},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n落盘 {out}: {len(rows)} 个评测用户")


if __name__ == "__main__":
    asyncio.run(main())
