"""Rejection sampling pipeline for pre-RL data.

This is intentionally model-API based and training-free:

    python -m otomo.eval.rejection_sampling --prompts prompts.txt --samples 3

Each candidate run is filtered by local claim verification and written as JSONL.
The output is suitable as a seed trajectory dataset for later SFT/DPO/GRPO work.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from ..agent.contracts import AgentState, FinalEvent, ObservationEvent, ToolCallEvent
from ..claim_verifier import verify_answer_claims
from ..factory import build_runner
from ..tools.bangumi.client import BangumiClient
from ..tools.moegirl.client import MoegirlClient

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "trajectories" / "rejection_samples.jsonl"


def load_prompts(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(raw) or []
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and item.get("question"):
                    out.append(str(item["question"]))
            return [x.strip() for x in out if x.strip()]
        if isinstance(data, dict):
            return [str(x).strip() for x in data.get("prompts", []) if str(x).strip()]
        return []
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]


def _obs_record(ev: ObservationEvent) -> dict[str, Any]:
    return {
        "name": ev.name,
        "ok": ev.ok,
        "summary": ev.summary,
        "sources": [s.model_dump(mode="json", exclude_none=True) for s in ev.sources],
        "entities": [e.model_dump(mode="json", exclude_none=True) for e in ev.entities],
        "data": ev.data,
    }


async def sample_one(runner, prompt: str, sample_index: int, min_support: float) -> dict[str, Any]:
    t0 = time.monotonic()
    tool_calls: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    answer = ""
    async for ev in runner.stream(prompt, AgentState()):
        if isinstance(ev, ToolCallEvent):
            tool_calls.append({"name": ev.name, "args": ev.args})
        elif isinstance(ev, ObservationEvent):
            observations.append(_obs_record(ev))
        elif isinstance(ev, FinalEvent):
            answer = ev.answer
    claim_check = verify_answer_claims(answer, observations)
    accepted = claim_check.support_rate >= min_support and claim_check.unsupported_count == 0
    return {
        "schema": "otomo.rejection_sample.v1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prompt": prompt,
        "sample_index": sample_index,
        "tool_calls": tool_calls,
        "observations": observations,
        "answer": answer,
        "claim_check": claim_check.model_dump(mode="json", exclude_none=True),
        "accepted": accepted,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def main_async(args: argparse.Namespace) -> int:
    prompts = load_prompts(Path(args.prompts))
    if args.limit:
        prompts = prompts[: args.limit]
    if not prompts:
        print("no prompts loaded", file=sys.stderr)
        return 1
    out = Path(args.out)
    client = BangumiClient()
    moegirl = MoegirlClient()
    runner = build_runner(client, moegirl, kind=args.runner)
    accepted = 0
    total = 0
    try:
        for prompt in prompts:
            for idx in range(args.samples):
                rec = await sample_one(runner, prompt, idx, args.min_support)
                append_jsonl(out, rec)
                accepted += int(bool(rec["accepted"]))
                total += 1
                mark = "ACCEPT" if rec["accepted"] else "REJECT"
                cc = rec["claim_check"]
                print(
                    f"{mark} support={cc.get('support_rate')} unsupported={cc.get('unsupported_count')} "
                    f"tools={len(rec['tool_calls'])} prompt={prompt[:42]}"
                )
    finally:
        await client.aclose()
        await moegirl.aclose()
    print(f"accepted {accepted}/{total} -> {out}")
    return 0 if accepted else 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", required=True, help="txt/yaml prompt file")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runner", choices=["react", "plan", "adaptive"], default="adaptive")
    ap.add_argument("--min-support", type=float, default=0.85)
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
