"""Otomo MCP Server：把 ACGN 知识图谱工具暴露给任何 MCP 客户端（Claude Desktop/Code、Cursor…）。

    python -m otomo.mcp_server        # stdio transport

设计：
- 复用 ToolRegistry —— 工具 schema 直接从 openai_schema() 转 MCP inputSchema，
  执行走 registry.dispatch，零重复实现；Otomo 加新工具时白名单即插即用。
- 只暴露**只读公共知识**白名单：搜索/条目/评价/导视/在哪看/资源 RSS/巡礼/音乐/
  生日/热门/对比/考据。用户态（记忆/收藏写回/推荐画像）不暴露——MCP 客户端没有
  Otomo 的用户会话概念，写操作也绝不该给外部宿主。
- 客户端配置示例（claude_desktop_config.json）见根 README「MCP Server」一节。

依赖：pip install -e ".[mcp]"（backend/ 下）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .factory import build_registry
from .memory import LongTermMemory
from .tools.bangumi.client import BangumiClient
from .tools.moegirl.client import MoegirlClient

# 只读公共知识白名单（不含用户态/写操作/多模态上传类）
EXPOSED_TOOLS: set[str] = {
    # 图谱检索与事实
    "search_subjects", "get_subject", "search_characters", "search_persons",
    "get_subject_characters", "get_subject_persons", "get_subject_relations",
    "get_subject_episodes", "get_character_persons", "get_person_subjects", "check_subjects",
    # 评价与对比
    "review_subject", "compare_subjects", "rank_erogamescape", "search_visual_novels", "search_anilist",
    # 导视与热度
    "season_guide_brief", "list_season_anime", "list_year_anime", "get_broadcast_calendar",
    "get_trending_subjects",
    # 观看与资源
    "where_to_watch", "get_anime_release_feeds",
    # 巡礼 / 音乐 / 生日 / IP
    "get_pilgrimage_map", "plan_pilgrimage_trip", "anime_music_themes", "search_anime_themes",
    "get_character_birthdays", "franchise_map",
    # 设定考据
    "lore_search", "explain_acgn_meme",
}


def _to_mcp_tool(schema: dict[str, Any]) -> types.Tool:
    fn = schema["function"]
    return types.Tool(
        name=fn["name"],
        description=(fn.get("description") or "")[:1000],
        inputSchema=fn.get("parameters") or {"type": "object", "properties": {}},
    )


def _result_text(result: Any) -> str:
    """工具结果 → 文本：优先结构化 data 的 JSON（宿主模型可自行解析），失败退 observation 文本。"""
    try:
        if result.ok and result.data is not None:
            payload = result.data.model_dump(mode="json", exclude_none=True)
            sources = [s.model_dump(mode="json") for s in (result.sources or [])][:10]
            if sources:
                payload["_sources"] = sources
            return json.dumps(payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return result.to_observation()


async def main() -> None:
    server: Server = Server("otomo", version="0.1.0")

    async with BangumiClient() as client:
        registry = build_registry(client, MoegirlClient(), LongTermMemory())
        exposed = {
            name: tool for name, tool in registry._tools.items()
            if name in EXPOSED_TOOLS and not getattr(tool, "is_write", False)
        }
        missing = EXPOSED_TOOLS - set(exposed)
        if missing:
            print(f"[otomo-mcp] 白名单中未注册的工具（忽略）: {sorted(missing)}", file=sys.stderr)

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [_to_mcp_tool(t.openai_schema()) for t in exposed.values()]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
            if name not in exposed:
                return [types.TextContent(type="text", text=f"unknown or unexposed tool: {name}")]
            result = await registry.dispatch(name, json.dumps(arguments or {}, ensure_ascii=False))
            return [types.TextContent(type="text", text=_result_text(result))]

        print(f"[otomo-mcp] serving {len(exposed)} tools over stdio", file=sys.stderr)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
