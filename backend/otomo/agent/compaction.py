"""Turn-aware conversation compaction.

The stored AgentState contains internal tool-call messages as well as visible
dialogue. Sending a sliding slice forever both grows SQLite and can split an
assistant tool call from its tool result. This module summarizes old complete
turns and preserves recent turns atomically.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import settings
from .contracts import AgentState

SUMMARY_MARKER = "[[OTOMO_CONVERSATION_SUMMARY]]"
RUNTIME_MARKER = "[[OTOMO_RUNTIME_STATE]]"

_SUMMARY_PROMPT = """你是 Otomo 的会话压缩器。把旧对话压成可供后续多轮使用的状态摘要。

只保留：
- 用户明确表达的当前任务、约束、指代对象和已确认决定；
- 已经在对话中形成的结论及其不确定性；
- 尚未完成的问题、待确认动作；
- 与下一轮指代消解有关的作品/角色/人物名。

不要新增事实，不要把模型猜测写成用户偏好，不要记录 API key、token、cookie 等秘密。
长期偏好由独立 memory 管理，这里只总结会话上下文。输出简洁中文，不要 JSON。"""


def _is_managed_system(message: dict[str, Any]) -> bool:
    if message.get("role") != "system":
        return False
    content = str(message.get("content") or "")
    return content.startswith(SUMMARY_MARKER) or content.startswith(RUNTIME_MARKER)


def _split_turns(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Return stable global system messages and complete user-led turns."""
    head: list[dict[str, Any]] = []
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    seen_user = False
    for message in messages:
        if _is_managed_system(message):
            continue
        if message.get("role") == "user":
            if current:
                turns.append(current)
            current = [message]
            seen_user = True
        elif not seen_user:
            # The initial SYSTEM_PROMPT is global. Other system messages before
            # the first user are also safe to retain.
            head.append(message)
        else:
            current.append(message)
    if current:
        turns.append(current)
    return head, turns


def _visible_transcript(turns: list[list[dict[str, Any]]], limit: int) -> str:
    rows: list[str] = []
    for turn in turns:
        for message in turn:
            role = message.get("role")
            if role not in {"user", "assistant"} or message.get("tool_calls"):
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            label = "用户" if role == "user" else "Otomo"
            rows.append(f"{label}: {content[:1200]}")
    return "\n".join(rows)[-limit:]


def _fallback_summary(previous: str, transcript: str, limit: int) -> str:
    pieces = []
    if previous:
        pieces.append(previous)
    if transcript:
        pieces.append("旧对话摘录：\n" + transcript)
    return "\n\n".join(pieces)[-limit:]


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return sum(
        len(str(message.get("content") or ""))
        + len(json.dumps(message.get("tool_calls") or [], ensure_ascii=False))
        for message in messages
    )


async def compact_agent_state(
    state: AgentState | None,
    llm: Any | None = None,
    model: str | None = None,
    *,
    force: bool = False,
) -> bool:
    """Compact old complete turns and keep recent tool-call pairs intact."""
    if state is None:
        return False
    threshold = max(20, int(settings.conversation_compaction_threshold))
    char_threshold = max(20000, int(settings.conversation_compaction_threshold_chars))
    oversized = _message_chars(state.messages) > char_threshold
    if not force and len(state.messages) <= threshold and not oversized:
        return False

    head, turns = _split_turns(state.messages)
    keep_turns = max(2, int(settings.conversation_compaction_keep_turns))
    if oversized and len(turns) > 2:
        # A few tool-heavy turns can exceed context before the message-count
        # threshold. Keep at least two recent turns, then add older turns only
        # while the live tail remains comfortably below the char threshold.
        live_budget = max(10000, char_threshold // 2)
        selected = 0
        selected_chars = 0
        for turn in reversed(turns):
            turn_chars = _message_chars(turn)
            if selected >= 2 and (selected >= keep_turns or selected_chars + turn_chars > live_budget):
                break
            selected += 1
            selected_chars += turn_chars
        keep_turns = max(2, selected)
    if len(turns) <= keep_turns:
        return False
    old_turns = turns[:-keep_turns]
    recent_turns = turns[-keep_turns:]
    previous = str(state.short_term.get("conversation_summary") or "").strip()
    transcript = _visible_transcript(old_turns, int(settings.conversation_compaction_input_chars))
    if not transcript and not previous:
        return False

    summary = ""
    if llm is not None and model:
        payload = "\n\n".join(
            x
            for x in [
                f"已有摘要：\n{previous}" if previous else "",
                f"待压缩对话：\n{transcript}" if transcript else "",
            ]
            if x
        )
        try:
            response = await llm.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": payload},
                ],
            )
            summary = str(response.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001 - deterministic fallback preserves the turn
            summary = ""
    limit = max(1000, int(settings.conversation_compaction_summary_chars))
    summary = (summary or _fallback_summary(previous, transcript, limit))[-limit:]
    if not summary:
        return False

    state.short_term["conversation_summary"] = summary
    summary_message = {
        "role": "system",
        "content": f"{SUMMARY_MARKER}\n以下是更早完整轮次的压缩摘要，不能覆盖本轮工具证据：\n{summary}",
    }
    state.messages = [
        *head,
        summary_message,
        *(message for turn in recent_turns for message in turn),
    ]
    # Presentation is strictly per-turn and must never leak into the next one.
    state.short_term.pop("presentation", None)
    return True


def restore_state(target: AgentState, snapshot: AgentState) -> None:
    """Restore a mutable cached AgentState in place after cancellation."""
    payload = snapshot.model_dump(mode="python")
    restored = AgentState.model_validate(json.loads(json.dumps(payload, ensure_ascii=False)))
    target.messages = restored.messages
    target.short_term = restored.short_term
    target.evidence_pool = restored.evidence_pool
    target.status = restored.status


__all__ = ["SUMMARY_MARKER", "compact_agent_state", "restore_state"]
