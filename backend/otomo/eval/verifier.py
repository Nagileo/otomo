"""Verifier 与 GoldenCase（A2）。

A2 先做**答案级 + 检索级**的可验证校验：
- 答案级：基于 golden case 的 expect_contains/expect_any/expect_absent 做（忽略大小写）子串校验。
- 检索级：expect_tools 要求至少调用过这些工具（验证走对了图谱路径）。
- 拒答用例（kind=refusal）：要求答案表达"查不到/不在范围"，且不得编造（expect_absent）。

后续 A3 升级为对 Bangumi 真值边的 set-F1 / 路径正确性（见 docs/05）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CaseKind = Literal["single_hop", "two_hop", "filter", "refusal"]


class GoldenCase(BaseModel):
    id: str
    question: str
    kind: CaseKind = "two_hop"
    expect_contains: list[str] = Field(default_factory=list)  # 全部须出现
    expect_any: list[str] = Field(default_factory=list)       # 至少一个出现
    expect_absent: list[str] = Field(default_factory=list)    # 都不得出现
    expect_tools: list[str] = Field(default_factory=list)     # 至少调用过这些工具
    min_tools: int = 0                                        # 至少调用过几次工具（防纯记忆作答）
    note: str = ""


class Check(BaseModel):
    label: str
    passed: bool


class CaseResult(BaseModel):
    id: str
    kind: CaseKind
    passed: bool
    checks: list[Check]
    answer: str
    tools_called: list[str]


def verify(case: GoldenCase, answer: str, tools_called: list[str]) -> CaseResult:
    a = answer.lower()
    called = set(tools_called)
    checks: list[Check] = []

    for s in case.expect_contains:
        checks.append(Check(label=f"含「{s}」", passed=s.lower() in a))
    if case.expect_any:
        hit = any(s.lower() in a for s in case.expect_any)
        checks.append(Check(label=f"含任一{case.expect_any}", passed=hit))
    for s in case.expect_absent:
        checks.append(Check(label=f"不含「{s}」", passed=s.lower() not in a))
    for t in case.expect_tools:
        checks.append(Check(label=f"调用过 {t}", passed=t in called))
    if case.min_tools:
        checks.append(
            Check(label=f"至少调用 {case.min_tools} 次工具", passed=len(tools_called) >= case.min_tools)
        )

    # 空答案一律失败
    if not answer.strip():
        checks.append(Check(label="非空回答", passed=False))

    # 通用：答案不得泄漏工具调用标记（如 DeepSeek 的 DSML/tool_calls 标记）
    leak = any(m in a for m in ("dsml", "tool_calls", "invoke name", "<｜"))
    if leak:
        checks.append(Check(label="无工具标记泄漏", passed=False))

    passed = all(c.passed for c in checks) and len(checks) > 0
    return CaseResult(
        id=case.id, kind=case.kind, passed=passed, checks=checks,
        answer=answer, tools_called=tools_called,
    )
