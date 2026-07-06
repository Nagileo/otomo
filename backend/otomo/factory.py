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
from .tools.animethemes import build_animethemes_tools
from .tools.aspect_profile import build_aspect_profile_tools
from .tools.calendar import build_calendar_tools
from .tools.comments import build_comment_tools
from .tools.community import build_community_tools
from .tools.curation import build_curation_tools
from .tools.bangumi import build_bangumi_tools
from .tools.bangumi.client import BangumiClient
from .tools.explorer import build_explorer_tools
from .tools.discovery import build_discovery_tools
from .tools.erogamescape import build_erogamescape_tools
from .tools.moegirl import build_moegirl_tools
from .tools.moegirl.client import MoegirlClient
from .tools.memory import build_memory_tools
from .tools.musicbrainz import build_musicbrainz_tools
from .tools.multimodal import build_multimodal_tools
from .tools.pilgrimage import build_pilgrimage_tools
from .tools.pixiv import build_pixiv_tools
from .tools.profile import build_profile_tools
from .tools.product_loop import build_product_loop_tools
from .tools.recommend import build_recommend_tools
from .tools.release import build_release_tools
from .tools.review import build_review_tools
from .tools.season import build_season_tools
from .tools.spoiler import build_spoiler_tools
from .tools.user_analysis import build_user_analysis_tools
from .tools.videos import build_video_tools
from .tools.vndb import build_vndb_tools
from .tools.watch import build_watch_tools
from .tools.watchorder import build_watchorder_tools
from .tools.websearch import build_websearch_tools
from .tools.wiki import build_wiki_tools
from .tools.writeback import build_writeback_tools
from .tools.yuc import build_yuc_tools

RunnerKind = Literal["react", "plan", "adaptive", "langgraph"]


def build_registry(
    client: BangumiClient,
    moegirl: MoegirlClient | None = None,
    ltm: LongTermMemory | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    shared_ltm = ltm or LongTermMemory()
    for tool in build_bangumi_tools(client):
        registry.register(tool)
    for tool in build_explorer_tools(client):
        registry.register(tool)
    for tool in build_discovery_tools(client):
        registry.register(tool)
    for tool in build_curation_tools(client):
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
    for tool in build_animethemes_tools():
        registry.register(tool)
    for tool in build_multimodal_tools(client):
        registry.register(tool)
    for tool in build_pixiv_tools():
        registry.register(tool)
    for tool in build_pilgrimage_tools(client):
        registry.register(tool)
    for tool in build_memory_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_writeback_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_aspect_profile_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_calendar_tools(client):
        registry.register(tool)
    for tool in build_profile_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_product_loop_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_recommend_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_release_tools(client, shared_ltm):
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
    for tool in build_watch_tools(client):
        registry.register(tool)
    for tool in build_watchorder_tools(client, shared_ltm):
        registry.register(tool)
    for tool in build_websearch_tools():
        registry.register(tool)
    for tool in build_video_tools():
        registry.register(tool)
    return registry


def build_runner(
    client: BangumiClient,
    moegirl: MoegirlClient | None = None,
    kind: RunnerKind = "adaptive",
    ltm: LongTermMemory | None = None,
) -> AgentRunner:
    registry = build_registry(client, moegirl, ltm)
    if kind == "plan":
        return PlanExecuteRunner(registry)
    if kind == "react":
        return ReActRunner(registry)
    if kind == "langgraph":  # 框架对照实现（需 pip install -e ".[langgraph]"）
        from .agent.langgraph_runner import LangGraphRunner
        return LangGraphRunner(registry)
    return AdaptiveRunner(registry)
