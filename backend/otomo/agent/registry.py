"""工具注册表：聚合工具、产出 OpenAI tools schema、按名分发。"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .contracts import Citation, Tool, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def openai_tools(self, include_write: bool = False) -> list[dict[str, Any]]:
        return [
            t.openai_schema()
            for t in self._tools.values()
            if include_write or not getattr(t, "is_write", False)
        ]

    async def dispatch(self, name: str, arguments_json: str, *, allow_write: bool = False) -> ToolResult:
        """解析 LLM 给的 JSON 参数 → 校验 → 执行。任何异常都收敛成 ok=False 的 ToolResult。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        if getattr(tool, "is_write", False) and not allow_write:
            return ToolResult(ok=False, error=f"write tool requires explicit user confirmation: {name}")
        try:
            raw = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as e:
            return ToolResult(ok=False, error=f"bad JSON arguments: {e}")
        try:
            args = tool.args_model.model_validate(raw)
        except ValidationError as e:
            return ToolResult(ok=False, error=f"args validation failed: {e}")
        try:
            return await tool.run(args)
        except Exception as e:  # noqa: BLE001 — 工具不该把 agent 拖崩
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")


__all__ = ["ToolRegistry", "Citation"]
