"""Spoiler policy tool.

It converts a user prompt into a conservative spoiler policy. The default is
no spoiler. Explicit permission is required for full spoilers; progress such as
"I watched episode 5" is stored as a hard boundary for episode comments.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Tool, ToolResult

SpoilerLevel = Literal["none", "mild", "full"]

_NO_SPOILER = ("别剧透", "无剧透", "不要剧透", "不剧透", "没看", "准备看", "刚开始看")
_ALLOW_FULL = ("可以剧透", "能剧透", "狠狠剧透", "全剧透", "我看完了", "已经看完", "补完了")
_SPOILER_INTENT = ("结局", "最后", "真相", "反转", "黑幕", "凶手", "死了", "后面", "后续", "烂尾")
_MILD_INTENT = ("大概讲", "讲什么", "设定", "基调", "前几集", "开头", "入坑", "适合我")


class SpoilerPolicyArgs(BaseModel):
    user_prompt: str = Field(..., description="User prompt")
    default_level: SpoilerLevel = Field("none", description="Default spoiler level")


class SpoilerPolicyResult(BaseModel):
    level: SpoilerLevel
    needs_followup: bool = False
    followup_question: str | None = None
    progress_episode: int | None = None
    risk_keywords: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


def _progress_episode(text: str) -> int | None:
    patterns = [
        r"看到第\s*(\d+)\s*[集话話]",
        r"看[到完了]*\s*(\d+)\s*[集话話]",
        r"第\s*(\d+)\s*[集话話]",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    return None


def assess_spoiler_policy(prompt: str, default_level: SpoilerLevel = "none") -> SpoilerPolicyResult:
    text = prompt.strip()
    progress = _progress_episode(text)
    no_hits = [k for k in _NO_SPOILER if k in text]
    allow_hits = [k for k in _ALLOW_FULL if k in text]
    risk_hits = [k for k in _SPOILER_INTENT if k in text]
    mild_hits = [k for k in _MILD_INTENT if k in text]

    if no_hits:
        level: SpoilerLevel = "none"
        needs_followup = False
    elif allow_hits:
        level = "full"
        needs_followup = False
    elif risk_hits:
        level = "none"
        needs_followup = True
    elif mild_hits:
        level = "mild"
        needs_followup = False
    else:
        level = default_level
        needs_followup = False

    rules: list[str] = []
    if level == "none":
        rules.append("不讲结局、反转、后期真相；未看作品只给题材、风格、制作、无剧透评价。")
    if level == "mild":
        rules.append("只讲开局设定、基调和早期角色关系，不讲重大反转和结局。")
    if level == "full":
        rules.append("用户已明确允许剧透，可以讨论完整剧情，但仍应先标注剧透。")
    if progress is not None:
        rules.append(f"分集讨论和剧情信息只使用第 {progress} 集及以前的内容。")

    return SpoilerPolicyResult(
        level=level,
        needs_followup=needs_followup,
        followup_question="这个问题会涉及后续剧情/结局。你希望无剧透、轻微剧透，还是完整剧透？" if needs_followup else None,
        progress_episode=progress,
        risk_keywords=risk_hits,
        rules=rules,
    )


class AssessSpoilerPolicyTool(Tool):
    name = "assess_spoiler_policy"
    description = (
        "Judge spoiler policy: none/mild/full and whether a follow-up is needed. "
        "Use before plot, ending, episode discussions, external comments, or meme pages."
    )
    args_model = SpoilerPolicyArgs
    result_model = SpoilerPolicyResult

    async def run(self, args: SpoilerPolicyArgs) -> ToolResult[SpoilerPolicyResult]:
        return ToolResult(ok=True, data=assess_spoiler_policy(args.user_prompt, args.default_level))


def build_spoiler_tools() -> list[Tool]:
    return [AssessSpoilerPolicyTool()]
