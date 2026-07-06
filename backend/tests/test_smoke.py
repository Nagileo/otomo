"""无网络 smoke 测试：契约、注册表分发、工具 schema 生成。

用 asyncio.run 自包含，不依赖 pytest-asyncio 插件，保证跨环境可跑。
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from otomo.agent._common import _looks_like_leak_prefix, should_fallback_answer
from otomo.agent.contracts import Tool, ToolResult
from otomo.agent.registry import ToolRegistry
from otomo.factory import build_registry
from otomo.tools.bangumi import build_bangumi_tools
from otomo.tools.bangumi.client import BangumiClient


class _EchoArgs(BaseModel):
    text: str = Field(..., description="回声内容")


class _EchoData(BaseModel):
    echoed: str


class EchoTool(Tool):
    name = "echo"
    description = "回显"
    args_model = _EchoArgs
    result_model = _EchoData

    async def run(self, args: _EchoArgs) -> ToolResult[_EchoData]:
        return ToolResult(ok=True, data=_EchoData(echoed=args.text))


def test_openai_schema_shape():
    schema = EchoTool().openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "text" in schema["function"]["parameters"]["properties"]


def test_registry_dispatch_ok():
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = asyncio.run(reg.dispatch("echo", '{"text": "hi"}'))
    assert result.ok and result.data.echoed == "hi"


def test_registry_bad_args():
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = asyncio.run(reg.dispatch("echo", "{}"))  # 缺 text
    assert not result.ok and "validation" in (result.error or "")


def test_registry_unknown_tool():
    reg = ToolRegistry()
    result = asyncio.run(reg.dispatch("nope", "{}"))
    assert not result.ok and "unknown" in (result.error or "")


def test_bangumi_tools_build_and_schema():
    tools = build_bangumi_tools(BangumiClient())
    names = {t.name for t in tools}
    assert {"search_subjects", "get_character_persons", "get_person_subjects"} <= names
    for t in tools:
        s = t.openai_schema()
        assert s["function"]["name"] == t.name


def test_phase19_product_loop_tools_are_registered():
    registry = build_registry(BangumiClient())
    names = set(registry._tools.keys())
    assert {
        "watch_cockpit",
        "subject_dossier",
        "franchise_map",
        "monthly_watch_report",
        "anime_music_themes",
        "search_anime_themes",
        "plan_watch_order",
    } <= names


def test_final_answer_fallback_rejects_single_marker_fragments():
    assert should_fallback_answer("<")
    assert should_fallback_answer(">")
    assert should_fallback_answer(">|")
    assert not should_fallback_answer("这是正常回答。")


def test_stream_guard_buffers_only_dangerous_prefixes():
    assert _looks_like_leak_prefix("<")
    assert _looks_like_leak_prefix(">")
    assert _looks_like_leak_prefix("D")
    assert not _looks_like_leak_prefix("D.Gray-man 是一部动画。")
    assert not _looks_like_leak_prefix("这是正常回答。")
