"""手搓 Plan-and-Execute runner（A2）。

流程：plan（先规划）→ execute（ReAct 式工具循环）→ reflect（自我反思）→ 不完整则补救一轮 → compose。
目的：用计划约束执行、用反思补漏，治 ReAct 在复杂多跳上"钻牛角尖"（无谓地反复交叉验证）。
与 ReActRunner 共享 _common，并实现同一 AgentRunner 接口 → 可一键 A/B（见 docs/03 §3）。
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
from .prompts import COMPOSE_PROMPT, PLAN_PROMPT, REFLECT_PROMPT, SYSTEM_PROMPT
from .registry import ToolRegistry

MAX_REFLECT_ROUNDS = 2  # 执行轮上限（首轮 + 一次反思补救）


class PlanExecuteRunner(AgentRunner):
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

    def _tool_round(self, state, tools, sources, seen_urls) -> AsyncIterator[AgentEvent]:
        """一轮执行：复用共享的 run_tool_round（含 DSML 纠正）。"""
        return C.run_tool_round(
            self.llm, self.model, self.registry, state.messages, tools, self.max_iters, sources, seen_urls
        )

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

        try:
            # ---- 1. PLAN ---- #
            plan_resp = await self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": PLAN_PROMPT},
                    {"role": "user", "content": user_input},
                ]
            )
            plan_text = C.strip_leak(plan_resp.choices[0].message.content or "")
            yield PlanEvent(summary=plan_text[:400])
            state.messages.append(
                {"role": "system", "content": f"已制定计划：\n{plan_text}\n现在按计划调用工具执行。"}
            )

            # ---- 2. EXECUTE + 3. REFLECT（最多 MAX_REFLECT_ROUNDS 轮）---- #
            for rnd in range(MAX_REFLECT_ROUNDS):
                async for ev in self._tool_round(state, tools, sources, seen_urls):
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

            # ---- 4. COMPOSE（流式最终答案）---- #
            compose = C.trim_messages(state.messages) + [{"role": "system", "content": COMPOSE_PROMPT}]
            parts: list[str] = []
            async for ev in C.stream_answer(self.llm, self.model, compose, tools):
                parts.append(ev.text)
                yield ev

            answer = C.strip_leak("".join(parts))
            if not answer.strip():  # 合成泄漏/为空 → 强制纯文本兜底重写
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
