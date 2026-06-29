"""轻量可观测：每次 agent run 的结构化 trace 落地 JSONL（可回放 / 分析延迟与工具用量）。

- **本地 trace**（本模块）：always-on、零外部依赖，写 `backend/trajectories/traces.jsonl`（已 gitignore）。
- **Langfuse**（可选，见 llm.py）：配 `LANGFUSE_*` 则 LLM 调用自动进 Langfuse 平台（prompt/token/延迟）。

可观测绝不能拖垮主流程：落盘失败一律吞掉。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from .agent.contracts import ClaimCheckEvent
from .claim_verifier import verify_answer_claims
from .config import settings

_TRACE_DIR = Path(__file__).resolve().parents[1] / "trajectories"  # backend/trajectories（gitignored）
_SENSITIVE_KEY = re.compile(r"(token|authorization|api[_-]?key|password|secret|cookie)", re.I)


def _summarize_event(ev: Any) -> dict:
    """把一个 AgentEvent 压成 trace 里的精简记录（流式 answer_delta 不逐条记）。"""
    t = ev.type
    d: dict[str, Any] = {"type": t}
    if t == "tool_call":
        d["name"], d["args"] = ev.name, ev.args
    elif t == "observation":
        d["name"], d["ok"], d["entities"] = ev.name, ev.ok, len(ev.entities)
    elif t == "claim_check":
        d["support_rate"] = ev.support_rate
        d["unsupported_count"] = ev.unsupported_count
    elif t == "plan":
        d["summary"] = ev.summary
    elif t == "reflect":
        d["complete"] = ev.complete
    elif t == "final":
        d["answer_len"], d["sources"], d["steps"] = len(ev.answer), len(ev.sources), ev.steps
    elif t == "followup":
        d["n"] = len(ev.questions)
    elif t == "error":
        d["message"] = ev.message
    return d


def _append(rec: dict) -> None:
    try:
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_TRACE_DIR / "traces.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 可观测不能拖垮主流程
        pass


def _append_named(filename: str, rec: dict) -> None:
    try:
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_TRACE_DIR / filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            out[key] = "<redacted>" if _SENSITIVE_KEY.search(key) else _redact(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_redact(x, depth=depth + 1) for x in value[:80]]
    if isinstance(value, str):
        if _SENSITIVE_KEY.search(value[:80]):
            return "<redacted>"
        return value[:2000]
    return value


def _obs_for_verifier(ev: Any) -> dict[str, Any]:
    return {
        "name": ev.name,
        "ok": ev.ok,
        "summary": ev.summary,
        "sources": [s.model_dump(mode="json", exclude_none=True) for s in ev.sources],
        "entities": [e.model_dump(mode="json", exclude_none=True) for e in ev.entities],
        "data": _redact(ev.data) if settings.trajectory_store_observations else None,
    }


async def traced_stream(runner, message: str, state, meta: dict) -> AsyncIterator:
    """包裹 runner.stream：原样透传事件，run 结束把结构化 trace 落 JSONL。

    记录：时间、session/runner、问题、事件序列（含每步工具与返回实体数）、工具序列、
    最终答案摘要、端到端耗时——足够回放与分析延迟 / 工具用量 / 失败率。
    """
    rec: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **meta, "message": message, "events": [], "tools": [],
    }
    rl_rec: dict[str, Any] = {
        "schema": "otomo.rl_trajectory.v1",
        "ts": rec["ts"],
        **meta,
        "query": message,
        "tool_calls": [],
        "observations": [],
        "answer": "",
        "claim_check": None,
        "duration_ms": 0,
        "status": "ok",
    }
    t0 = time.monotonic()
    final_answer = ""
    status = "ok"
    try:
        async for ev in runner.stream(message, state):
            if ev.type != "answer_delta":  # 流式增量太碎，不逐条记
                rec["events"].append(_summarize_event(ev))
            if ev.type == "tool_call":
                rec["tools"].append(ev.name)
                rl_rec["tool_calls"].append({"name": ev.name, "args": _redact(ev.args)})
            elif ev.type == "observation":
                rl_rec["observations"].append(_obs_for_verifier(ev))
            elif ev.type == "final":
                final_answer = ev.answer
                rl_rec["answer"] = final_answer
            elif ev.type == "error":
                status = "error"
                rl_rec["status"] = "error"
            yield ev
            if ev.type == "final":
                claim_check = verify_answer_claims(final_answer, rl_rec["observations"])
                rl_rec["claim_check"] = claim_check.model_dump(mode="json", exclude_none=True)
                claim_event = ClaimCheckEvent(**rl_rec["claim_check"])
                rec["events"].append(_summarize_event(claim_event))
                yield claim_event
    finally:
        rec["duration_ms"] = round((time.monotonic() - t0) * 1000)
        rec["n_tools"] = len(rec["tools"])
        rec["answer"] = final_answer[:200]
        rec["status"] = status
        _append(rec)
        rl_rec["duration_ms"] = rec["duration_ms"]
        rl_rec["status"] = status
        if settings.trajectory_capture_enabled:
            _append_named("rl_runs.jsonl", _redact(rl_rec))
