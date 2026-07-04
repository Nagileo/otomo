"""LangGraph 版 runner——与手搓 runner 同接口（AgentRunner），用于"手搓 vs 框架"对比。

刻意**复用现有 ToolRegistry**（把每个 Tool 包成 langchain StructuredTool），证明同一套自建工具
能在两种 runtime 下跑。对比分析见 docs/03 §10。

langchain/langgraph 仅在实例化时 import（懒加载）——没装 `[langgraph]` extra 不影响主项目。
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from ..config import settings
from . import _common as C
from .contracts import (
    AgentEvent,
    AgentRunner,
    AgentState,
    AnswerDeltaEvent,
    ErrorEvent,
    FinalEvent,
    ObservationEvent,
    ToolCallEvent,
)
from .registry import ToolRegistry


def _to_lc_tool(tool, registry: ToolRegistry):
    """把自建 Tool 包成 langchain StructuredTool：执行仍走 registry.dispatch（typed 校验+收敛）。"""
    from langchain_core.tools import StructuredTool

    async def _run(**kwargs) -> str:
        result = await registry.dispatch(tool.name, json.dumps(kwargs, ensure_ascii=False))
        return result.to_observation()

    return StructuredTool.from_function(
        coroutine=_run, name=tool.name, description=tool.description, args_schema=tool.args_model
    )


class LangGraphRunner(AgentRunner):
    def __init__(self, registry: ToolRegistry, model: str | None = None) -> None:
        from langchain_openai import ChatOpenAI
        from langgraph.prebuilt import create_react_agent

        self.registry = registry
        llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "EMPTY",
            model=model or settings.llm_model,
            temperature=0,
        )
        tools = [_to_lc_tool(t, registry) for t in registry._tools.values()]
        self.agent = create_react_agent(llm, tools)

    async def stream(
        self, user_input: str, state: AgentState | None = None
    ) -> AsyncIterator[AgentEvent]:
        """对照实现：用 langgraph 跑完，再把消息序列映射成我们的 AgentEvent。

        注意：prebuilt ReAct 的 token 级流式要走 astream_events（更繁），这里用 ainvoke 跑完再映射，
        最终答案一次性吐——这正是与手搓"两阶段真流式"的一个对比点（见报告）。
        """
        from langchain_core.messages import AIMessage, ToolMessage

        try:
            state = state or AgentState()
            C.update_spoiler_state_from_input(state, user_input)
            for ev in C.runtime_state_events(state):
                yield ev

            messages = []
            runtime_prompt = C.runtime_state_prompt(state)
            if runtime_prompt:
                messages.append(("system", runtime_prompt))
            messages.append(("user", user_input))
            result = await self.agent.ainvoke({"messages": messages})
            steps = 0
            answer = ""
            for m in result.get("messages", []):
                if isinstance(m, AIMessage) and m.tool_calls:
                    for tc in m.tool_calls:
                        steps += 1
                        yield ToolCallEvent(name=tc["name"], args=tc.get("args", {}))
                elif isinstance(m, ToolMessage):
                    name = getattr(m, "name", "tool")
                    payload = C.safe_json(str(m.content))
                    ok = payload.get("ok", True) if isinstance(payload, dict) else True
                    yield ObservationEvent(
                        name=name,
                        ok=ok,
                        summary=str(m.content)[:200],
                        data=C.panel_data_from_payload(name, payload) if ok else None,
                    )
                elif isinstance(m, AIMessage) and not m.tool_calls and m.content:
                    answer = C.strip_leak(m.content if isinstance(m.content, str) else str(m.content))
            if C.should_fallback_answer(answer):
                answer = "抱歉，这次没能整理出回答，请再问一次或换个问法。"
            if answer:
                yield AnswerDeltaEvent(text=answer)
            yield FinalEvent(answer=answer or "（无回答）", sources=[], steps=steps)
        except Exception as e:  # noqa: BLE001
            yield ErrorEvent(message=f"{type(e).__name__}: {e}")
