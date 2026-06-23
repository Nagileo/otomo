"""自动 benchmark 生成器（A3）。

从 Bangumi 图谱**程序化构造可验证 golden cases**：答案直接取自 API 真值（声优/制作公司/年份），
无需人工标注，且可随时扩展规模。生成的题面向 agent，校验靠 expect_contains（API 真值）+ min_tools。

    python -m otomo.eval.generate --n 10 --out ../eval/generated_cases.yaml

注意：题面用 Bangumi 的原始名（agent 会从工具结果里拿到同名，grounding 使子串校验稳定）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

from ..tools.bangumi.client import SUBJECT_TYPE, BangumiClient
from .verifier import GoldenCase

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

DEFAULT_OUT = Path(__file__).resolve().parents[3] / "eval" / "generated_cases.yaml"

# 种子作品（用关键词搜，取首个动画结果）；可任意扩展以放大 benchmark 规模
SEED_KEYWORDS = [
    "孤独摇滚", "葬送的芙莉莲", "命运石之门", "紫罗兰永恒花园", "摇曳露营",
    "进击的巨人", "CLANNAD", "凉宫春日的忧郁", "莉可丽丝", "辉夜大小姐",
    "JOJO的奇妙冒险", "鬼灭之刃", "我的青春恋爱物语果然有问题", "约定的梦幻岛", "间谍过家家",
]


def _year(date: str | None) -> str | None:
    return date[:4] if date and len(date) >= 4 else None


async def gen_for_keyword(client: BangumiClient, kw: str) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    res = await client.search_subjects(kw, SUBJECT_TYPE["anime"], limit=1)
    data = res.get("data") or []
    if not data:
        return cases
    s = data[0]
    sid = s.get("id")
    name = s.get("name_cn") or s.get("name")
    if not (sid and name):
        return cases

    # 1) 年份（single_hop）
    detail = await client.get_subject(sid)
    yr = _year(detail.get("date"))
    if yr:
        cases.append(GoldenCase(
            id=f"gen_{sid}_year", question=f"动画《{name}》是哪一年首播的？",
            kind="single_hop", expect_contains=[yr], min_tools=1,
            note="auto: subject.date",
        ))

    # 2) 主角声优（two_hop）—— 真值取自 subject_characters 的 actors
    chars = await client.get_subject_characters(sid)
    main = next((c for c in chars if c.get("relation") == "主角" and c.get("actors")), None)
    main = main or next((c for c in chars if c.get("actors")), None)
    if main:
        cv = (main["actors"][0] or {}).get("name")
        cname = main.get("name")
        if cv and cname:
            cases.append(GoldenCase(
                id=f"gen_{sid}_cv", question=f"动画《{name}》里 {cname} 的声优是谁？",
                kind="two_hop", expect_contains=[cv], min_tools=1,
                note="auto: subject→character→actor",
            ))

    # 3) 动画制作公司（two_hop）
    persons = await client.get_subject_persons(sid)
    studio = next((p for p in persons if p.get("relation") == "动画制作" and p.get("name")), None)
    if studio:
        cases.append(GoldenCase(
            id=f"gen_{sid}_studio", question=f"动画《{name}》的动画制作公司是哪家？",
            kind="two_hop", expect_contains=[studio["name"]], min_tools=1,
            note="auto: subject→persons(动画制作)",
        ))
    return cases


async def main_async(args: argparse.Namespace) -> None:
    client = BangumiClient()
    cases: list[GoldenCase] = []
    try:
        for kw in SEED_KEYWORDS[: args.n]:
            try:
                cases += await gen_for_keyword(client, kw)
            except Exception as e:  # noqa: BLE001 — 单个关键词失败不影响整体
                print(f"  跳过「{kw}」：{type(e).__name__}: {e}")
    finally:
        await client.aclose()

    out = Path(args.out)
    dumped = [c.model_dump(exclude_defaults=True) for c in cases]
    out.write_text(
        yaml.safe_dump(dumped, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"生成 {len(cases)} 条可验证 cases → {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--n", type=int, default=len(SEED_KEYWORDS))
    main_async_args = ap.parse_args()
    asyncio.run(main_async(main_async_args))


if __name__ == "__main__":
    main()
