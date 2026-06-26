"""组装：把 Bangumi 工具注册进 registry，产出指定范式的 runner。"""
from __future__ import annotations

from typing import Literal

from .agent.adaptive import AdaptiveRunner
from .agent.contracts import AgentRunner
from .agent.plan_execute import PlanExecuteRunner
from .agent.react import ReActRunner
from .agent.registry import ToolRegistry
from .memory import LongTermMemory
from .tools.anilist import build_anilist_tools
from .tools.comments import build_comment_tools
from .tools.community import build_community_tools
from .tools.bangumi import build_bangumi_tools
from .tools.bangumi.client import BangumiClient
from .tools.erogamescape import build_erogamescape_tools
from .tools.moegirl import build_moegirl_tools
from .tools.moegirl.client import MoegirlClient
from .tools.musicbrainz import build_musicbrainz_tools
from .tools.profile import build_profile_tools
from .tools.recommend import build_recommend_tools
from .tools.review import build_review_tools
from .tools.season import build_season_tools
from .tools.spoiler import build_spoiler_tools
from .tools.user_analysis import build_user_analysis_tools
from .tools.videos import build_video_tools
from .tools.vndb import build_vndb_tools
from .tools.watchorder import build_watchorder_tools
from .tools.websearch import build_websearch_tools
from .tools.wiki import build_wiki_tools
from .tools.yuc import build_yuc_tools

RunnerKind = Literal["react", "plan", "adaptive", "langgraph"]


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
    for tool in build_comment_tools(client):
        registry.register(tool)
    for tool in build_community_tools():
        registry.register(tool)
    for tool in build_vndb_tools():
        registry.register(tool)
    for tool in build_erogamescape_tools():
        registry.register(tool)
    for tool in build_musicbrainz_tools():
        registry.register(tool)
    for tool in build_anilist_tools():
        registry.register(tool)
    for tool in build_profile_tools(client, ltm or LongTermMemory()):
        registry.register(tool)
    for tool in build_recommend_tools(client):
        registry.register(tool)
    for tool in build_review_tools(client):
        registry.register(tool)
    for tool in build_season_tools(client):
        registry.register(tool)
    for tool in build_spoiler_tools():
        registry.register(tool)
    for tool in build_user_analysis_tools(client):
        registry.register(tool)
    for tool in build_yuc_tools():
        registry.register(tool)
    for tool in build_watchorder_tools(client):
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
    if kind == "langgraph":  # 框架对照实现（需 pip install -e ".[langgraph]"）
        from .agent.langgraph_runner import LangGraphRunner
        return LangGraphRunner(registry)
    return AdaptiveRunner(registry)
