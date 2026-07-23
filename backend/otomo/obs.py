"""轻量可观测：每次 agent run 的结构化 trace 落地 JSONL（可回放 / 分析延迟与工具用量）。

- **本地 trace**（本模块）：always-on、零外部依赖，写 `cache/observations/traces.jsonl`（已 gitignore）。
- **Langfuse**（可选，见 llm.py）：配 `LANGFUSE_*` 则 LLM 调用自动进 Langfuse 平台（prompt/token/延迟）。

可观测绝不能拖垮主流程：落盘失败一律吞掉。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from .agent.contracts import AgentState, ClaimCheckEvent, FinalEvent
from .claim_verifier import verify_answer_claims
from .config import settings

_TRACE_DIR = Path(settings.observation_dir)
_SENSITIVE_KEY = re.compile(r"(token|authorization|api[_-]?key|password|secret|cookie)", re.I)


def _summarize_event(ev: Any) -> dict:
    """把一个 AgentEvent 压成 trace 里的精简记录（流式 answer_delta 不逐条记）。"""
    t = ev.type
    d: dict[str, Any] = {"type": t}
    if t == "tool_call":
        d["name"], d["args"] = ev.name, ev.args
    elif t == "progress":
        d["stage"], d["tool"], d["summary"] = ev.stage, ev.tool, ev.summary
        d["current"], d["total"] = ev.current, ev.total
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


def append_visual_feedback(rec: dict) -> None:
    """Append user-confirmed visual recognition feedback for later evaluation/RL.

    This is deliberately separate from full agent traces: feedback can arrive
    from UI actions after the original run has finished.
    """
    payload = {
        "schema": "otomo.visual_feedback.v1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **rec,
    }
    _append_named("visual_feedback.jsonl", _redact(payload))


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
    # claim 校验始终需要 observation data（在内存中用）；是否落盘由 trajectory_store_observations 决定（见 traced_stream）
    return {
        "name": ev.name,
        "ok": ev.ok,
        "summary": ev.summary,
        "sources": [s.model_dump(mode="json", exclude_none=True) for s in ev.sources],
        "entities": [e.model_dump(mode="json", exclude_none=True) for e in ev.entities],
        "data": _redact(ev.data),
    }


def _current_turn(state: AgentState | None) -> int:
    if state is None:
        return 1
    st = getattr(state, "short_term", {}) or {}
    try:
        return int(st.get("turn_index") or 0) + 1
    except (TypeError, ValueError):
        return 1


def _historical_evidence(state: AgentState | None, *, current_turn: int) -> list[dict[str, Any]]:
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    for obs in (getattr(state, "evidence_pool", []) or [])[-60:]:
        if isinstance(obs, dict) and obs.get("turn") != current_turn:
            out.append(obs)
    return out[-60:]


def _store_evidence_pool(state: AgentState | None, observations: list[dict[str, Any]], *, turn: int) -> None:
    if state is None or not observations:
        return
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    previous = [
        x for x in (getattr(state, "evidence_pool", []) or [])
        if isinstance(x, dict) and x.get("turn") != turn
    ]
    current = [{**obs, "turn": turn, "ts": ts} for obs in observations]
    state.evidence_pool = (previous + current)[-60:]
    state.short_term["turn_index"] = turn


_CLAIM_REVISION_PROMPT = """你是 Otomo 的事实修正器。请只根据「本轮/历史 observation」和「claim verifier 修正建议」修正最终回答。

要求：
- 删除或降级 unsupported/block 事实断言，不要补新事实。
- 如果证据只支持更弱结论，就写“本轮证据只能确认……”。
- 保留用户可用的建议、结构和语气，但不要保留未被证据支持的制作公司、评分、年份、声优、排名等硬事实。
- 只输出修正后的最终回答，不要解释你的修正过程。
"""


def _revision_payload(observations: list[dict[str, Any]], limit: int = 9000) -> str:
    rows = []
    for obs in observations[:30]:
        rows.append(
            {
                "name": obs.get("name"),
                "summary": obs.get("summary"),
                "data": obs.get("data"),
                "sources": obs.get("sources"),
                "entities": obs.get("entities"),
                "turn": obs.get("turn"),
            }
        )
    text = json.dumps(rows, ensure_ascii=False)
    return text[:limit]


def _strip_revision_text(text: str) -> str:
    out = str(text or "").strip()
    for marker in ("｜DSML｜", "DSML", "tool_calls", "invoke name", "<｜"):
        idx = out.find(marker)
        if idx >= 0:
            out = out[:idx].strip()
    return out


async def _maybe_revise_answer(
    runner: Any,
    answer: str,
    claim_check: Any,
    observations: list[dict[str, Any]],
) -> tuple[str, Any | None]:
    if not settings.claim_auto_revision_enabled or not claim_check.needs_revision:
        return "", None
    if not any(str(hint).startswith("block:") for hint in (claim_check.revision_hints or [])):
        return "", None
    llm = getattr(runner, "llm", None)
    model = getattr(runner, "model", None) or settings.llm_model
    if llm is None or not model:
        return "", None
    hints = "\n".join(f"- {x}" for x in claim_check.revision_hints[:8])
    messages = [
        {"role": "system", "content": _CLAIM_REVISION_PROMPT},
        {"role": "user", "content": (
            "原始回答：\n"
            f"{answer}\n\n"
            "claim verifier 修正建议：\n"
            f"{hints}\n\n"
            "本轮/历史 observation：\n"
            f"{_revision_payload(observations)}"
        )},
    ]
    try:
        resp = await llm.chat.completions.create(model=model, messages=messages)
    except Exception:  # noqa: BLE001 - 修正失败不能影响主回答
        return "", None
    revised = _strip_revision_text(resp.choices[0].message.content or "")
    if not revised or revised == answer:
        return "", None
    revised_check = verify_answer_claims(revised, observations)
    improved = (
        revised_check.unsupported_count < claim_check.unsupported_count
        or (claim_check.needs_revision and not revised_check.needs_revision)
        or revised_check.support_rate > claim_check.support_rate
    )
    if not improved:
        return "", None
    revised_check.caveats.insert(0, "已根据 claim verifier 自动删除/降级未支持事实；修正后再次做本轮 evidence graph 校验。")
    return revised, revised_check


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
    turn = _current_turn(state)
    historical_observations = _historical_evidence(state, current_turn=turn)
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
                verifier_observations = [*rl_rec["observations"], *historical_observations]
                claim_check = verify_answer_claims(final_answer, verifier_observations)
                rl_rec["claim_check"] = claim_check.model_dump(mode="json", exclude_none=True)
                if historical_observations:
                    rl_rec["claim_check"].setdefault("caveats", []).insert(
                        0,
                        f"已合并最近 {len(historical_observations)} 条跨轮 observation 作为证据池。",
                    )
                claim_event = ClaimCheckEvent(**rl_rec["claim_check"])
                rec["events"].append(_summarize_event(claim_event))
                yield claim_event
                revised, revised_check = await _maybe_revise_answer(runner, final_answer, claim_check, verifier_observations)
                if revised and revised_check is not None:
                    final_answer = revised
                    rl_rec["answer"] = final_answer
                    revised_final = FinalEvent(answer=final_answer, sources=ev.sources, steps=ev.steps)
                    rec["events"].append({**_summarize_event(revised_final), "auto_revised": True})
                    yield revised_final
                    rl_rec["claim_revision"] = revised_check.model_dump(mode="json", exclude_none=True)
                    revised_claim_event = ClaimCheckEvent(**rl_rec["claim_revision"])
                    rec["events"].append({**_summarize_event(revised_claim_event), "after_auto_revision": True})
                    yield revised_claim_event
    finally:
        rec["duration_ms"] = round((time.monotonic() - t0) * 1000)
        rec["n_tools"] = len(rec["tools"])
        rec["answer"] = final_answer[:200]
        rec["status"] = status
        _append(_redact(rec))
        rl_rec["duration_ms"] = rec["duration_ms"]
        rl_rec["status"] = status
        _store_evidence_pool(state, rl_rec["observations"], turn=turn)
        if settings.trajectory_capture_enabled:
            out = dict(rl_rec)
            if not settings.trajectory_store_observations:  # 落盘时才按开关裁剪 data，不影响内存里的 claim 校验
                out["observations"] = [
                    {k: v for k, v in o.items() if k != "data"} for o in rl_rec["observations"]
                ]
            _append_named("rl_runs.jsonl", _redact(out))
