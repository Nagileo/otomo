"""手搓 ReAct runner（A1/A2）。

两阶段，既能多跳调工具、又能流式吐最终答案，且**不外露裸 CoT**：
  阶段 1（工具循环）：模型决定调哪些工具 → 执行 → 观察回填 → 直到不再调工具。
  阶段 2（最终答案，流式）：去掉工具，让模型基于已查事实流式生成回答。

与 Plan-Execute runner 共享 _common 的底层，二者实现同一 AgentRunner 接口（见 docs/03 §3）。
"""
from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI

from ..config import settings
from ..llm import get_llm
from . import _common as C
from .contracts import (
    AgentEvent,
    AgentRunner,
    AgentState,
    Citation,
    ErrorEvent,
    FinalEvent,
    FollowupEvent,
    ToolCallEvent,
)
from .prompts import COMPOSE_PROMPT, SYSTEM_PROMPT
from .registry import ToolRegistry


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
        C.update_spoiler_state_from_input(state, user_input)
        C.inject_runtime_state(state.messages, state)
        state.messages.append({"role": "user", "content": user_input})

        tools = self.registry.openai_tools()
        sources: list[Citation] = []
        seen_urls: set[str] = set()
        steps = 0
        corrections = 0

        try:
            for ev in C.runtime_state_events(state):
                yield ev

            # ---- 阶段 1：工具循环 ---- #
            for _ in range(self.max_iters):
                resp = await self.llm.chat.completions.create(
                    model=self.model,
                    messages=C.trim_messages(state.messages),
                    tools=tools,
                    tool_choice="auto",
                )
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    if C.has_leak(msg.content) and corrections < 2:  # 工具调用写成了文本 → 纠正重试
                        corrections += 1
                        state.messages.append({"role": "assistant", "content": msg.content or ""})
                        state.messages.append({"role": "system", "content": C.CORRECT_FC})
                        continue
                    break
                async for ev in C.step_tools(self.registry, msg, state.messages, sources, seen_urls):
                    if isinstance(ev, ToolCallEvent):
                        steps += 1
                    yield ev

            # ---- 阶段 2：流式最终答案 ---- #
            compose = C.trim_messages(state.messages) + [{"role": "system", "content": COMPOSE_PROMPT}]
            parts: list[str] = []
            leaked: list[bool] = []
            async for ev in C.stream_answer(self.llm, self.model, compose, tools, leaked):
                parts.append(ev.text)
                yield ev

            answer = C.strip_leak("".join(parts))
            if C.should_fallback_answer(answer, leaked):
                answer = await C.compose_fallback(self.llm, self.model, compose) or \
                    "抱歉，这次没能整理出回答，请再问一次或换个问法。"
            state.messages.append({"role": "assistant", "content": answer})
            state.status = "done"
            yield FinalEvent(answer=answer, sources=sources, steps=steps)
            followups = await C.gen_followups(self.llm, self.model, C.trim_messages(state.messages))
            if followups:
                yield FollowupEvent(questions=followups)

        except Exception as e:  # noqa: BLE001
            state.status = "failed"
            yield ErrorEvent(message=f"{type(e).__name__}: {e}")
