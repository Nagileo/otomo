"""组装：把 Bangumi 工具注册进 registry，产出一个 ReActRunner。"""
from __future__ import annotations

from .agent.react import ReActRunner
from .agent.registry import ToolRegistry
from .tools.bangumi import build_bangumi_tools
from .tools.bangumi.client import BangumiClient


def build_runner(client: BangumiClient) -> ReActRunner:
    registry = ToolRegistry()
    for tool in build_bangumi_tools(client):
        registry.register(tool)
    return ReActRunner(registry)
