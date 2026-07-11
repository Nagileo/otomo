"""Eval runner（A2/A3）：跑 golden cases → 校验 → 打分卡（含图谱级 set-F1 / 路径有效率）。

    python -m otomo.eval.runner                       # 跑全部（默认 golden_cases.yaml）
    python -m otomo.eval.runner --limit 3              # 只跑前 3 条（省 API）
    python -m otomo.eval.runner --id gen_xxx_cv        # 只跑某条
    python -m otomo.eval.runner --path ../eval/generated_cases.yaml --runner adaptive

需 .env 配好 LLM_API_KEY（图谱级 set-F1 要 LLM 做开放实体抽取）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from ..agent.contracts import AgentState, FinalEvent, ObservationEvent, ToolCallEvent
from ..config import settings
from ..factory import build_runner
from ..llm import get_llm
from ..tools.bangumi.client import BangumiClient
from ..tools.moegirl.client import MoegirlClient
from .verifier import CaseResult, Check, GoldenCase, ToolStep, verify

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


async def _run_turn(runner, question: str, state: AgentState | None = None) -> tuple[str, list[ToolStep]]:
    """跑一轮，收集结构化 trace（ToolCall 配对 Observation 的返回实体）。"""
    answer = ""
    trace: list[ToolStep] = []
    pending: ToolStep | None = None
    async for ev in runner.stream(question, state):
        if isinstance(ev, ToolCallEvent):
            pending = ToolStep(name=ev.name, args=ev.args)
        elif isinstance(ev, ObservationEvent):
            if pending and pending.name == ev.name:
                pending.entities = ev.entities
                pending.has_data = ev.data is not None
                trace.append(pending)
                pending = None
        elif isinstance(ev, FinalEvent):
            answer = ev.answer
    return answer, trace


async def run_one(runner, case: GoldenCase, llm, model: str, client: BangumiClient) -> CaseResult:
    """跑一条 case；支持单轮与同 session 多轮 turns。"""
    if case.turns:
        state = AgentState()
        turn_payloads: list[dict] = []
        all_tools: list[str] = []
        aggregate_checks: list[Check] = []
        last_answer = ""
        all_passed = True
        for idx, turn in enumerate(case.turns, start=1):
            answer, trace = await _run_turn(runner, turn.question, state)
            last_answer = answer
            all_tools.extend(step.name for step in trace)
            turn_case = GoldenCase(
                id=f"{case.id}#turn{idx}",
                question=turn.question,
                kind=case.kind,
                expect_contains=turn.expect_contains,
                expect_any=turn.expect_any,
                expect_absent=turn.expect_absent,
                expect_tools=turn.expect_tools,
                forbid_tools=turn.forbid_tools,
                expect_panels=turn.expect_panels,
                min_tools=turn.min_tools,
                note=turn.note,
                truth_entities=turn.truth_entities,
                truth_path=turn.truth_path,
            )
            turn_result = await verify(turn_case, answer, trace, llm, model, client)
            all_passed = all_passed and turn_result.passed
            aggregate_checks.append(Check(label=f"turn {idx} passed", passed=turn_result.passed))
            turn_payloads.append(turn_result.model_dump(mode="json"))
        return CaseResult(
            id=case.id,
            kind=case.kind,
            passed=all_passed,
            checks=aggregate_checks,
            answer=last_answer,
            tools_called=all_tools,
            turns=turn_payloads,
        )

    answer, trace = await _run_turn(runner, case.question)
    return await verify(case, answer, trace, llm, model, client)


async def main_async(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.path))
    if args.id:
        cases = [c for c in cases if c.id == args.id]
    if args.limit:
        cases = cases[: args.limit]

    client = BangumiClient()
    moegirl = MoegirlClient()
    # 记忆沙箱：eval 里的偏好/反馈写入（"别再推校园恋爱"等 case）绝不能落进真实
    # cache/ltm 污染日常画像；每次跑 eval 用一次性临时目录，case 内多轮读写自洽。
    import tempfile

    from ..memory import LongTermMemory

    ltm = LongTermMemory(base_dir=Path(tempfile.mkdtemp(prefix="otomo-eval-ltm-")))
    print(f"{DIM}ltm sandbox={ltm.base}{RESET}")
    runner = build_runner(client, moegirl, args.runner, ltm=ltm)
    llm, model = get_llm(), settings.llm_model
    print(f"{DIM}runner={args.runner}{RESET}\n")
    results: list[CaseResult] = []
    try:
        for case in cases:
            headline = case.question or f"{len(case.turns)} turns"
            print(f"{DIM}[{case.kind}] {case.id}{RESET}  {headline}")
            res = await run_one(runner, case, llm, model, client)
            results.append(res)
            mark = f"{GREEN}PASS{RESET}" if res.passed else f"{RED}FAIL{RESET}"
            print(f"  {mark}  {DIM}tools={res.tools_called}{RESET}")
            m = res.metrics
            if m.set_f1 is not None:
                print(f"  {DIM}set-F1={m.set_f1}  (P={m.set_precision} R={m.set_recall} 幻觉={m.hallucinated}){RESET}")
            if m.path_valid is not None:
                pv = f"{GREEN}✓{RESET}" if m.path_valid else f"{RED}✗{RESET}"
                print(f"  {DIM}路径有效 {pv}{RESET}")
            for c in res.checks:
                if not c.passed:
                    print(f"    {RED}✗ {c.label}{RESET}")
            if res.turns:
                for turn in res.turns:
                    status = "PASS" if turn.get("passed") else "FAIL"
                    print(f"    {DIM}{turn.get('id')}: {status} tools={turn.get('tools_called', [])}{RESET}")
            print(f"  {DIM}答：{res.answer[:120].replace(chr(10), ' ')}…{RESET}\n")
    finally:
        await client.aclose()
        await moegirl.aclose()

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
    # —— 图谱级聚合指标 —— #
    f1s = [r.metrics.set_f1 for r in results if r.metrics.set_f1 is not None]
    pvs = [r.metrics.path_valid for r in results if r.metrics.path_valid is not None]
    if f1s:
        print(f"  {BOLD}平均 set-F1{RESET}: {sum(f1s) / len(f1s):.3f}  （{len(f1s)} 条图谱级）")
    if pvs:
        print(f"  {BOLD}路径有效率{RESET}: {sum(pvs) / len(pvs) * 100:.0f}%  （{len(pvs)} 条）")
    if args.json_report:
        report_path = Path(args.json_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if passed == total else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(DEFAULT_CASES))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--id", default="")
    ap.add_argument("--runner", choices=["react", "plan", "adaptive", "langgraph"], default="react")
    ap.add_argument("--json-report", default="", help="可选：把 CaseResult 列表写入 JSON，供 CI artifact 使用")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
