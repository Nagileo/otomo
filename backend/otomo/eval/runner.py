"""Eval runner（A2）：跑 golden cases → 校验 → 打分卡。

    python -m otomo.eval.runner                 # 跑全部
    python -m otomo.eval.runner --limit 3        # 只跑前 3 条（省 API）
    python -m otomo.eval.runner --id bocchi_year # 只跑某条

默认读 <repo>/eval/golden_cases.yaml。需 .env 配好 LLM_API_KEY。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

from ..agent.contracts import FinalEvent, ToolCallEvent
from ..factory import build_runner
from ..tools.bangumi.client import BangumiClient
from .verifier import CaseResult, GoldenCase, verify

for _stream in (sys.stdout, sys.stderr):  # Windows GBK 兜底
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

DEFAULT_CASES = Path(__file__).resolve().parents[3] / "eval" / "golden_cases.yaml"
GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def load_cases(path: Path) -> list[GoldenCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GoldenCase.model_validate(d) for d in data]


async def run_one(runner, case: GoldenCase) -> CaseResult:
    answer = ""
    tools: list[str] = []
    async for ev in runner.stream(case.question):
        if isinstance(ev, ToolCallEvent):
            tools.append(ev.name)
        elif isinstance(ev, FinalEvent):
            answer = ev.answer
    return verify(case, answer, tools)


async def main_async(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.path))
    if args.id:
        cases = [c for c in cases if c.id == args.id]
    if args.limit:
        cases = cases[: args.limit]

    client = BangumiClient()
    runner = build_runner(client, args.runner)
    print(f"{DIM}runner={args.runner}{RESET}\n")
    results: list[CaseResult] = []
    try:
        for case in cases:
            print(f"{DIM}[{case.kind}] {case.id}{RESET}  {case.question}")
            res = await run_one(runner, case)
            results.append(res)
            mark = f"{GREEN}PASS{RESET}" if res.passed else f"{RED}FAIL{RESET}"
            print(f"  {mark}  {DIM}tools={res.tools_called}{RESET}")
            for c in res.checks:
                if not c.passed:
                    print(f"    {RED}✗ {c.label}{RESET}")
            print(f"  {DIM}答：{res.answer[:120].replace(chr(10),' ')}…{RESET}\n")
    finally:
        await client.aclose()

    passed = sum(r.passed for r in results)
    total = len(results)
    print(f"{BOLD}== 通过 {passed}/{total} =={RESET}")
    by_kind: dict[str, list[int]] = {}
    for r in results:
        by_kind.setdefault(r.kind, [0, 0])
        by_kind[r.kind][0] += int(r.passed)
        by_kind[r.kind][1] += 1
    for kind, (p, t) in by_kind.items():
        print(f"  {kind}: {p}/{t}")
    return 0 if passed == total else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(DEFAULT_CASES))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--id", default="")
    ap.add_argument("--runner", choices=["react", "plan"], default="react")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
