"""Adaptive runner（A2）：按任务复杂度自适应路由。

- 简单任务（单实体 / 1 跳）→ 直接 ReAct 执行，不浪费规划与反思开销。
- 复杂任务（多跳 / 多约束 / 比较聚合）→ 先 plan，再 react 式执行 + 自我反思补救。

即用户要的"复杂才 plan、中期 react；不一直 plan 也不一直 react"。与 ReAct/Plan-Execute 共享 _common、
实现同一 AgentRunner 接口；作为产品默认 runner（纯 react / 纯 plan 保留用于 A/B 对比）。
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
    PlanEvent,
    ReflectEvent,
    ToolCallEvent,
)
from .plan_execute import MAX_REFLECT_ROUNDS
from .prompts import (
    COMPOSE_PROMPT,
    REFLECT_PROMPT,
    ROUTER_PLAN_PROMPT,
    SYNTHESIS_COMPOSE,
    SYSTEM_PROMPT,
)
from .registry import ToolRegistry
from .tool_router import ToolSelector


class AdaptiveRunner(AgentRunner):
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

    async def _chat(self, messages: list[dict], **kw):
        return await self.llm.chat.completions.create(model=self.model, messages=messages, **kw)

    async def stream(
        self, user_input: str, state: AgentState | None = None
    ) -> AsyncIterator[AgentEvent]:
        state = state or AgentState()
        if not state.messages:
            state.messages.append({"role": "system", "content": SYSTEM_PROMPT})
        C.update_spoiler_state_from_input(state, user_input)
        C.begin_presentation_turn(state)
        C.inject_runtime_state(state.messages, state)
        state.messages.append({"role": "user", "content": user_input})

        selector = ToolSelector(self.registry, user_input)
        sources: list[Citation] = []
        seen_urls: set[str] = set()
        steps = 0

        try:
            for ev in C.runtime_state_events(state):
                yield ev

            # ---- 路由：简单→SIMPLE / 复杂→计划 ---- #
            runtime_prompt = C.runtime_state_prompt(state)
            router_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": ROUTER_PLAN_PROMPT},
            ]
            if runtime_prompt:
                router_messages.append({"role": "system", "content": runtime_prompt})
            router_messages.append({"role": "user", "content": user_input})
            router = await self._chat(router_messages)
            routed = C.strip_leak(router.choices[0].message.content or "")
            up = routed.strip().upper()
            compose_prompt = COMPOSE_PROMPT

            if up.startswith("SIMPLE"):
                # 简单任务：直接 ReAct，一轮执行即可
                yield PlanEvent(summary="简单任务 → 直接执行（ReAct）")
                async for ev in C.run_tool_round(
                    self.llm, self.model, self.registry, state.messages, selector, self.max_iters, sources, seen_urls, state
                ):
                    if isinstance(ev, ToolCallEvent):
                        steps += 1
                    yield ev
            elif up.startswith("SYNTHESIS"):
                # 综述档：一次有界检索（RAG/web 为主）后综合，更快，对标豆包单次思考
                yield PlanEvent(summary="综述题 → 一次检索后综合")
                compose_prompt = SYNTHESIS_COMPOSE
                async for ev in C.run_tool_round(
                    self.llm, self.model, self.registry, state.messages, selector,
                    min(self.max_iters, 3), sources, seen_urls, state,
                ):
                    if isinstance(ev, ToolCallEvent):
                        steps += 1
                    yield ev
            else:
                # 复杂任务：plan → execute + reflect 补救
                yield PlanEvent(summary=routed[:400])
                state.messages.append(
                    {"role": "system", "content": f"已制定计划：\n{routed}\n现在按计划调用工具执行。"}
                )
                for rnd in range(MAX_REFLECT_ROUNDS):
                    async for ev in C.run_tool_round(
                        self.llm, self.model, self.registry, state.messages, selector, self.max_iters, sources, seen_urls, state
                    ):
                        if isinstance(ev, ToolCallEvent):
                            steps += 1
                        yield ev
                    reflect = await self._chat(
                        C.trim_messages(state.messages) + [{"role": "system", "content": REFLECT_PROMPT}]
                    )
                    rj = C.safe_json(reflect.choices[0].message.content)
                    complete = bool(rj.get("complete", True))
                    missing = str(rj.get("missing", "")).strip()
                    yield ReflectEvent(complete=complete, note=missing[:200])
                    if complete or rnd == MAX_REFLECT_ROUNDS - 1 or not missing:
                        break
                    state.messages.append(
                        {"role": "system", "content": f"回答还不完整，缺：{missing}。请继续调用工具补齐。"}
                    )

            # ---- 流式最终答案 ---- #
            compose = C.compose_messages(state.messages, state, compose_prompt)
            parts: list[str] = []
            leaked: list[bool] = []
            async for ev in C.stream_answer(self.llm, self.model, compose, None, leaked):
                parts.append(ev.text)
                yield ev

            answer = C.strip_leak("".join(parts))
            if C.should_fallback_answer(answer, leaked):
                answer = await C.compose_fallback(self.llm, self.model, compose) or \
                    "抱歉，这次没能整理出回答，请再问一次或换个问法。"
            answer = C.append_missing_anchors(answer, state)
            state.messages.append({"role": "assistant", "content": answer})
            state.status = "done"
            yield FinalEvent(answer=answer, sources=sources, steps=steps)
            followups = await C.gen_followups(self.llm, self.model, C.trim_messages(state.messages))
            if followups:
                yield FollowupEvent(questions=followups)

        except Exception as e:  # noqa: BLE001
            state.status = "failed"
            yield ErrorEvent(message=f"{type(e).__name__}: {e}")
