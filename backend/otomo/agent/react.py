"""手搓 ReAct runner（A1）。

两阶段，既能多跳调工具、又能流式吐最终答案，且**不外露裸 CoT**：
  阶段 1（工具循环，非流式）：模型决定调哪些工具 → 执行 → 观察回填 → 直到不再调工具。
                              期间只把 tool_call / observation 作为结构化事件吐出。
  阶段 2（最终答案，流式）：去掉工具，让模型基于已查事实流式生成面向用户的回答。

未来 LangGraph 版实现同一 AgentRunner 接口，即可一键 A/B（见 docs/03 §3）。
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ..config import settings
from ..llm import get_llm
from .contracts import (
    AgentEvent,
    AgentRunner,
    AgentState,
    AnswerDeltaEvent,
    Citation,
    ErrorEvent,
    FinalEvent,
    ObservationEvent,
    ToolCallEvent,
    ToolResult,
)
from .prompts import COMPOSE_PROMPT, SYSTEM_PROMPT
from .registry import ToolRegistry


def _summarize(result: ToolResult) -> str:
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
    # 单条详情
    return d.get("name_cn") or d.get("name") or "ok"


# DeepSeek 等模型偶尔把工具调用写成 DSML 文本塞进 content，而非结构化 tool_calls 字段
_LEAK_MARKERS = ("｜DSML｜", "DSML", "tool_calls", "invoke name", "<｜")
_CORRECT_FC = (
    "请使用函数调用（tool calls）来调用工具，不要在回复正文里输出 invoke / DSML / tool_calls 等标记。"
    "若信息已足够，请直接给出最终自然语言答案。"
)


def _has_leak(text: str | None) -> bool:
    return bool(text) and any(m in text for m in _LEAK_MARKERS)


def _strip_leak(text: str) -> str:
    """截断到第一个工具标记之前。"""
    idx = len(text)
    for m in _LEAK_MARKERS:
        j = text.find(m)
        if j != -1:
            idx = min(idx, j)
    return text[:idx].strip()


class ReActRunner(AgentRunner):
    def __init__(
        self,
        registry: ToolRegistry,
        llm: AsyncOpenAI | None = None,
        model: str | None = None,
        max_iters: int | None = None,
    ) -> None:
        self.registry = registry
        self.llm = llm or get_llm()
        self.model = model or settings.llm_model
        self.max_iters = max_iters or settings.agent_max_iters

    async def stream(
        self, user_input: str, state: AgentState | None = None
    ) -> AsyncIterator[AgentEvent]:
        state = state or AgentState()
        if not state.messages:
            state.messages.append({"role": "system", "content": SYSTEM_PROMPT})
        state.messages.append({"role": "user", "content": user_input})

        tools = self.registry.openai_tools()
        sources: list[Citation] = []
        seen_urls: set[str] = set()
        steps = 0
        corrections = 0

        try:
            # ---- 阶段 1：工具循环 ---- #
            for _ in range(self.max_iters):
                resp = await self.llm.chat.completions.create(
                    model=self.model,
                    messages=state.messages,
                    tools=tools,
                    tool_choice="auto",
                )
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    # 模型把工具调用写成了 DSML 文本 → 纠正后重试本轮，别误当成最终答案
                    if _has_leak(msg.content) and corrections < 2:
                        corrections += 1
                        state.messages.append({"role": "assistant", "content": msg.content or ""})
                        state.messages.append({"role": "system", "content": _CORRECT_FC})
                        continue
                    break

                state.messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )

                for tc in msg.tool_calls:
                    steps += 1
                    args = _safe_json(tc.function.arguments)
                    yield ToolCallEvent(name=tc.function.name, args=args)
                    result = await self.registry.dispatch(tc.function.name, tc.function.arguments)
                    for c in result.sources:
                        if c.url not in seen_urls:
                            seen_urls.add(c.url)
                            sources.append(c)
                    yield ObservationEvent(
                        name=tc.function.name,
                        ok=result.ok,
                        summary=_summarize(result),
                        sources=result.sources,
                    )
                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result.to_observation(),
                        }
                    )

            # ---- 阶段 2：流式生成最终答案（无工具，不外露 CoT）---- #
            compose_messages = state.messages + [{"role": "system", "content": COMPOSE_PROMPT}]
            answer_parts: list[str] = []
            # 带 tools 但 tool_choice="none"：显式禁止再调工具，避免模型把工具标记当文本吐出
            stream = await self.llm.chat.completions.create(
                model=self.model,
                messages=compose_messages,
                tools=tools,
                tool_choice="none",
                stream=True,
            )
            acc = ""
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    acc += delta.content
                    if _has_leak(acc):  # 一旦冒出工具标记就停止吐，避免脏内容流到前端
                        break
                    answer_parts.append(delta.content)
                    yield AnswerDeltaEvent(text=delta.content)

            answer = _strip_leak("".join(answer_parts) or acc)
            state.messages.append({"role": "assistant", "content": answer})
            state.status = "done"
            yield FinalEvent(answer=answer, sources=sources, steps=steps)

        except Exception as e:  # noqa: BLE001
            state.status = "failed"
            yield ErrorEvent(message=f"{type(e).__name__}: {e}")


def _safe_json(s: str) -> dict[str, Any]:
    import json

    try:
        return json.loads(s or "{}")
    except json.JSONDecodeError:
        return {"_raw": s}
