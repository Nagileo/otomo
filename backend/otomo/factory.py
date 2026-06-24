"""组装：把 Bangumi 工具注册进 registry，产出指定范式的 runner。"""
from __future__ import annotations

from typing import Literal

from .agent.adaptive import AdaptiveRunner
from .agent.contracts import AgentRunner
from .agent.plan_execute import PlanExecuteRunner
from .agent.react import ReActRunner
from .agent.registry import ToolRegistry
from .memory import LongTermMemory
from .tools.bangumi import build_bangumi_tools
from .tools.bangumi.client import BangumiClient
from .tools.moegirl import build_moegirl_tools
from .tools.moegirl.client import MoegirlClient
from .tools.profile import build_profile_tools
from .tools.recommend import build_recommend_tools
from .tools.videos import build_video_tools
from .tools.websearch import build_websearch_tools
from .tools.wiki import build_wiki_tools

RunnerKind = Literal["react", "plan", "adaptive"]


def build_registry(
    client: BangumiClient,
    moegirl: MoegirlClient | None = None,
    ltm: LongTermMemory | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_bangumi_tools(client):
        registry.register(tool)
    if moegirl is not None:
        for tool in build_moegirl_tools(moegirl):
            registry.register(tool)
    for tool in build_wiki_tools():
        registry.register(tool)
    for tool in build_profile_tools(client, ltm or LongTermMemory()):
        registry.register(tool)
    for tool in build_recommend_tools(client):
        registry.register(tool)
    for tool in build_websearch_tools():
        registry.register(tool)
    for tool in build_video_tools():
        registry.register(tool)
    return registry


def build_runner(
    client: BangumiClient, moegirl: MoegirlClient | None = None, kind: RunnerKind = "adaptive"
) -> AgentRunner:
    registry = build_registry(client, moegirl)
    if kind == "plan":
        return PlanExecuteRunner(registry)
    if kind == "react":
        return ReActRunner(registry)
    return AdaptiveRunner(registry)
