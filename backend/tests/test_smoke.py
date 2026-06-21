"""无网络 smoke 测试：契约、注册表分发、工具 schema 生成。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from otomo.agent.contracts import Tool, ToolResult
from otomo.agent.registry import ToolRegistry
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


@pytest.mark.asyncio
async def test_registry_dispatch_ok():
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await reg.dispatch("echo", '{"text": "hi"}')
    assert result.ok and result.data.echoed == "hi"


@pytest.mark.asyncio
async def test_registry_bad_args():
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = await reg.dispatch("echo", "{}")  # 缺 text
    assert not result.ok and "validation" in (result.error or "")


@pytest.mark.asyncio
async def test_registry_unknown_tool():
    reg = ToolRegistry()
    result = await reg.dispatch("nope", "{}")
    assert not result.ok and "unknown" in (result.error or "")


def test_bangumi_tools_build_and_schema():
    tools = build_bangumi_tools(BangumiClient())
    names = {t.name for t in tools}
    assert {"search_subjects", "get_character_persons", "get_person_subjects"} <= names
    for t in tools:
        s = t.openai_schema()
        assert s["function"]["name"] == t.name
