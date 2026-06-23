"""两个 runner（ReAct / Plan-Execute）共享的底层：

- 工具标记泄漏处理（DeepSeek 偶把 tool call 写成 DSML 文本）
- 观察摘要
- step_tools：执行一轮工具调用并产出结构化事件（side effect 落到 state/sources）
- stream_answer：无工具的流式最终答案（带泄漏护栏）
- trim_messages：滑动窗口（控制送入 LLM 的上下文长度，配对安全）
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from .contracts import (
    AnswerDeltaEvent,
    Citation,
    ObservationEvent,
    ToolCallEvent,
    ToolResult,
)
from .registry import ToolRegistry

# DeepSeek 等模型偶尔把工具调用写成 DSML 文本塞进 content，而非结构化 tool_calls 字段
LEAK_MARKERS = ("｜DSML｜", "DSML", "tool_calls", "invoke name", "<｜")
CORRECT_FC = (
    "请使用函数调用（tool calls）来调用工具，不要在回复正文里输出 invoke / DSML / tool_calls 等标记。"
    "若信息已足够，请直接给出最终自然语言答案。"
)


def has_leak(text: str | None) -> bool:
    return bool(text) and any(m in text for m in LEAK_MARKERS)


def strip_leak(text: str) -> str:
    idx = len(text)
    for m in LEAK_MARKERS:
        j = text.find(m)
        if j != -1:
            idx = min(idx, j)
    return text[:idx].strip()


def safe_json(s: str | None) -> dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                pass
    return {}


def summarize(result: ToolResult) -> str:
    if not result.ok:
        return f"失败：{result.error}"
    if result.data is None:
        return "ok（无数据）"
    d = result.data.model_dump(exclude_none=True)
    for key in ("subjects", "characters", "persons"):
        if key in d and isinstance(d[key], list):
            names = [it.get("name_cn") or it.get("name") for it in d[key][:5]]
            names = [n for n in names if n]
            return f"{d.get('count', len(d[key]))} 条：{', '.join(names)}" if names else f"{d.get('count', 0)} 条"
    return d.get("name_cn") or d.get("name") or "ok"


def trim_messages(messages: list[dict], max_messages: int = 40) -> list[dict]:
    """滑动窗口：超过阈值时丢弃最旧的轮次，但保留所有 system + 第一条 user，
    且避免让窗口以孤立的 tool 消息开头（否则 OpenAI/DeepSeek 会报错）。"""
    if len(messages) <= max_messages:
        return messages
    head: list[dict] = []
    body: list[dict] = []
    seen_user = False
    for m in messages:
        if m.get("role") == "system" or (m.get("role") == "user" and not seen_user):
            head.append(m)
            if m.get("role") == "user":
                seen_user = True
        else:
            body.append(m)
    keep = max(max_messages - len(head), 0)
    tail = body[-keep:] if keep else []
    while tail and tail[0].get("role") == "tool":  # 别用孤立 tool 消息开头
        tail = tail[1:]
    return head + tail


def assistant_toolcalls_msg(msg: Any) -> dict:
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ],
    }


async def step_tools(
    registry: ToolRegistry,
    msg: Any,
    messages: list[dict],
    sources: list[Citation],
    seen_urls: set[str],
) -> AsyncIterator[Any]:
    """执行一轮（一个 assistant 回合里的全部 tool_calls），产出 ToolCall/Observation 事件。
    side effect：把 assistant 与 tool 结果消息追加进 messages、新来源并入 sources。"""
    messages.append(assistant_toolcalls_msg(msg))
    for tc in msg.tool_calls:
        yield ToolCallEvent(name=tc.function.name, args=safe_json(tc.function.arguments))
        result = await registry.dispatch(tc.function.name, tc.function.arguments)
        for c in result.sources:
            if c.url not in seen_urls:
                seen_urls.add(c.url)
                sources.append(c)
        yield ObservationEvent(
            name=tc.function.name, ok=result.ok, summary=summarize(result), sources=result.sources
        )
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.to_observation()})


async def run_tool_round(
    llm: AsyncOpenAI,
    model: str,
    registry: ToolRegistry,
    messages: list[dict],
    tools: list[dict],
    max_iters: int,
    sources: list[Citation],
    seen_urls: set[str],
) -> AsyncIterator[Any]:
    """一轮"执行"：反复让模型调工具直到它不再调（含 DSML 文本误写的纠正）。
    ReAct / Plan-Execute / Adaptive 三个 runner 共用。side effect 落到 messages/sources。"""
    corrections = 0
    for _ in range(max_iters):
        resp = await llm.chat.completions.create(
            model=model, messages=trim_messages(messages), tools=tools, tool_choice="auto"
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if has_leak(msg.content) and corrections < 2:
                corrections += 1
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({"role": "system", "content": CORRECT_FC})
                continue
            return
        async for ev in step_tools(registry, msg, messages, sources, seen_urls):
            yield ev


async def stream_answer(
    llm: AsyncOpenAI, model: str, messages: list[dict], tools: list[dict]
) -> AsyncIterator[AnswerDeltaEvent]:
    """无工具（tool_choice=none）的流式最终答案；一旦冒出工具标记立即停止吐。"""
    stream = await llm.chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="none", stream=True
    )
    acc = ""
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            acc += delta.content
            if has_leak(acc):
                break
            yield AnswerDeltaEvent(text=delta.content)
