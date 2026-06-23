"""组装：把 Bangumi 工具注册进 registry，产出指定范式的 runner。"""
from __future__ import annotations

from typing import Literal

from .agent.adaptive import AdaptiveRunner
from .agent.contracts import AgentRunner
from .agent.plan_execute import PlanExecuteRunner
from .agent.react import ReActRunner
from .agent.registry import ToolRegistry
from .tools.bangumi import build_bangumi_tools
from .tools.bangumi.client import BangumiClient

RunnerKind = Literal["react", "plan", "adaptive"]


def build_registry(client: BangumiClient) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_bangumi_tools(client):
        registry.register(tool)
    return registry


def build_runner(client: BangumiClient, kind: RunnerKind = "adaptive") -> AgentRunner:
    registry = build_registry(client)
    if kind == "plan":
        return PlanExecuteRunner(registry)
    if kind == "react":
        return ReActRunner(registry)
    return AdaptiveRunner(registry)
