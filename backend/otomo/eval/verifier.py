"""Verifier 与 GoldenCase（A2 子串级 → A3+ 图谱级）。

两层校验，按 case 是否带 canonical 真值自动分流：
- **子串/检索级（向后兼容）**：expect_contains/any/absent 子串 + expect_tools/min_tools。
- **图谱级（新）**：当 case 带 canonical 真值时——
    · **set-F1**：从答案**开放抽取**同类实体（LLM）→ Bangumi search 锚定 canonical id →
      与真值 id 集合算 P/R/F1。解析不到的名字记为幻觉，拉低 precision。
    · **路径有效性**：从工具轨迹（每步 args + 返回实体）重建 agent 走过的图谱节点，
      校验是否覆盖真值路径（subject→character→person…）。
  真值由 generate.py 从 Bangumi API 程序化取（年份/声优/制作公司…），ID 级、防同名歧义。

这套指标即 Agentic-RL 的奖励地基：结果奖励=set-F1，过程奖励=路径有效率。
"""
from __future__ import annotations

import json
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ..agent.contracts import EntityRef
from ..tools.bangumi.client import BangumiClient

CaseKind = Literal["single_hop", "two_hop", "filter", "refusal", "multi_turn"]
_ETYPE_CN = {"subject": "作品", "person": "人物（声优/制作人员）", "character": "角色"}
_ARG_TYPE = {"subject_id": "subject", "person_id": "person", "character_id": "character"}
_LEAK_MARKERS = ("dsml", "tool_calls", "invoke name", "<｜")


class ToolStep(BaseModel):
    """执行轨迹的一步：工具名 + 入参 + 返回的 canonical 实体（路径重建用）。"""

    name: str
    args: dict = Field(default_factory=dict)
    entities: list[EntityRef] = Field(default_factory=list)
    has_data: bool = False  # 该步 ObservationEvent 是否带结构化面板 data（Phase 1）


class TurnSpec(BaseModel):
    question: str
    expect_contains: list[str] = Field(default_factory=list)
    expect_any: list[str] = Field(default_factory=list)
    expect_absent: list[str] = Field(default_factory=list)
    expect_tools: list[str] = Field(default_factory=list)
    forbid_tools: list[str] = Field(default_factory=list)
    expect_panels: list[str] = Field(default_factory=list)
    min_tools: int = 0
    note: str = ""
    truth_entities: list[EntityRef] = Field(default_factory=list)
    truth_path: list[tuple[str, int]] = Field(default_factory=list)


class GoldenCase(BaseModel):
    id: str
    question: str = ""
    kind: CaseKind = "two_hop"
    turns: list[TurnSpec] = Field(default_factory=list)
    # —— 子串/工具级（向后兼容）——
    expect_contains: list[str] = Field(default_factory=list)
    expect_any: list[str] = Field(default_factory=list)
    expect_absent: list[str] = Field(default_factory=list)
    expect_tools: list[str] = Field(default_factory=list)
    forbid_tools: list[str] = Field(default_factory=list)   # 不应调用的工具（source routing 负向偏好）
    expect_panels: list[str] = Field(default_factory=list)  # 应产出结构化面板 data 的工具（Phase 1）
    min_tools: int = 0
    note: str = ""
    # —— 图谱级真值（canonical；有则启用 set-F1 / 路径校验）——
    truth_entities: list[EntityRef] = Field(default_factory=list)
    truth_path: list[tuple[str, int]] = Field(default_factory=list)


class Check(BaseModel):
    label: str
    passed: bool


class Metrics(BaseModel):
    set_precision: float | None = None  # 聚焦度（答案对齐真值的纯度，含"真值外真实实体"扣分）
    set_recall: float | None = None
    set_f1: float | None = None
    hallucinated: int | None = None     # 幻觉实体数（Bangumi 搜不到=编造）；真值外但真实的不计入
    path_valid: bool | None = None


class CaseResult(BaseModel):
    id: str
    kind: CaseKind
    passed: bool
    checks: list[Check]
    answer: str
    tools_called: list[str]
    metrics: Metrics = Field(default_factory=Metrics)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    infra_errors: int = 0  # runner 收到的 ErrorEvent 数（LLM/网络故障，区别于断言失败）
    infra_error_note: str = ""  # 首条错误文本（判断可否重试：余额不足等重试无意义）


# --------------------------------------------------------------------------- #
# 子串/工具级（同步，向后兼容）
# --------------------------------------------------------------------------- #


def _legacy_checks(case: GoldenCase, answer: str, tools_called: list[str]) -> list[Check]:
    a = answer.lower()
    called = set(tools_called)
    checks: list[Check] = []
    for s in case.expect_contains:
        checks.append(Check(label=f"含「{s}」", passed=s.lower() in a))
    if case.expect_any:
        checks.append(Check(label=f"含任一{case.expect_any}", passed=any(s.lower() in a for s in case.expect_any)))
    for s in case.expect_absent:
        checks.append(Check(label=f"不含「{s}」", passed=s.lower() not in a))
    for t in case.expect_tools:
        checks.append(Check(label=f"调用过 {t}", passed=t in called))
    for t in case.forbid_tools:
        checks.append(Check(label=f"未调用 {t}", passed=t not in called))
    if case.min_tools:
        checks.append(Check(label=f"至少调用 {case.min_tools} 次工具", passed=len(tools_called) >= case.min_tools))
    if not answer.strip():
        checks.append(Check(label="非空回答", passed=False))
    if any(m in a for m in _LEAK_MARKERS):
        checks.append(Check(label="无工具标记泄漏", passed=False))
    return checks


# --------------------------------------------------------------------------- #
# 图谱级 ①：set-F1（开放抽取 + canonical 锚定）
# --------------------------------------------------------------------------- #

_EXTRACT_PROMPT = (
    "从下面这段回答里，抽取所有作为答案明确列出的{etype}名称。"
    '只输出 JSON 字符串数组（如 ["名称1","名称2"]），没有就输出 []，不要任何多余文字。\n\n回答：\n{answer}'
)


async def _extract_names(answer: str, etype: str, llm: AsyncOpenAI, model: str) -> list[str]:
    """LLM 从答案**开放抽取**某类型实体名（set-F1 的 precision 靠它抓出幻觉/多答）。"""
    try:
        resp = await llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(etype=_ETYPE_CN[etype], answer=answer)}],
        )
        txt = resp.choices[0].message.content or ""
        i, j = txt.find("["), txt.rfind("]")
        if 0 <= i < j:
            return [str(x).strip() for x in json.loads(txt[i : j + 1]) if str(x).strip()]
    except Exception:  # noqa: BLE001
        pass
    return []


async def _resolve_id(name: str, etype: str, client: BangumiClient) -> int | None:
    """实体名 → Bangumi canonical id（解析不到 → 视为幻觉）。"""
    try:
        if etype == "subject":
            data = (await client.search_subjects(name, limit=1)).get("data") or []
        elif etype == "person":
            data = (await client.search_persons(name, limit=1)).get("data") or []
        else:
            data = (await client.search_characters(name, limit=1)).get("data") or []
        return data[0].get("id") if data else None
    except Exception:  # noqa: BLE001
        return None


def _alias_hits(answer: str, truth: list[EntityRef]) -> set[int]:
    """真值别名在答案里直接命中的（recall 稳健兜底，防 LLM 抽取遗漏）。"""
    a = answer.lower()
    return {
        e.id for e in truth
        if any(al and al.lower() in a for al in (e.aliases or [e.name]))
    }


async def _set_f1(answer: str, truth: list[EntityRef], llm: AsyncOpenAI, model: str, client: BangumiClient) -> Metrics:
    etype = truth[0].type  # 真值通常单一类型
    truth_t = [e for e in truth if e.type == etype]
    truth_ids = {e.id for e in truth_t}

    # 答案侧：开放抽取 → 锚定 id；解析不到 = 幻觉（计入分母，拉低 precision）
    answer_ids: set[int] = set()
    hallucinated = 0
    for n in await _extract_names(answer, etype, llm, model):
        rid = await _resolve_id(n, etype, client)
        if rid:
            answer_ids.add(rid)
        else:
            hallucinated += 1
    answer_ids |= _alias_hits(answer, truth_t)  # 别名直接命中并入，兜底 recall

    tp = len(answer_ids & truth_ids)
    denom_p = len(answer_ids) + hallucinated
    p = tp / denom_p if denom_p else 0.0
    r = tp / len(truth_ids) if truth_ids else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return Metrics(
        set_precision=round(p, 3), set_recall=round(r, 3), set_f1=round(f1, 3),
        hallucinated=hallucinated,
    )


# --------------------------------------------------------------------------- #
# 图谱级 ②：路径有效性
# --------------------------------------------------------------------------- #


def _path_valid(trace: list[ToolStep], truth_path: list[tuple[str, int]]) -> bool:
    """agent 是否走过真值路径的每个节点（出现在某步的返回实体，或作为该类型 id 入参）。"""
    visited: set[tuple[str, int]] = set()
    for step in trace:
        for e in step.entities:
            visited.add((e.type, e.id))
        for k, v in step.args.items():  # id 入参也算"经过"（如 get_subject(subject_id=…)）
            if isinstance(v, int) and k in _ARG_TYPE:
                visited.add((_ARG_TYPE[k], v))
    return all((t, i) in visited for t, i in truth_path)


# --------------------------------------------------------------------------- #
# 统一入口（async：图谱级需 LLM 抽取 + Bangumi 锚定）
# --------------------------------------------------------------------------- #


async def verify(
    case: GoldenCase,
    answer: str,
    trace: list[ToolStep],
    llm: AsyncOpenAI | None = None,
    model: str = "",
    client: BangumiClient | None = None,
) -> CaseResult:
    tools_called = [s.name for s in trace]
    checks = _legacy_checks(case, answer, tools_called)
    if case.expect_panels:  # Phase 1：断言这些工具产出了结构化面板 data（observation.data 非空）
        panels = {s.name for s in trace if s.has_data}
        for t in case.expect_panels:
            checks.append(Check(label=f"{t} 产出结构化面板", passed=t in panels))
    metrics = Metrics()

    if case.truth_entities and llm and client:  # 图谱级：多答真实信息不算错 → 答全真值(recall) + 不编造(零幻觉)
        metrics = await _set_f1(answer, case.truth_entities, llm, model, client)
        ok = (metrics.set_recall or 0) >= 0.5 and (metrics.hallucinated or 0) == 0
        checks.append(Check(
            label=f"recall≥0.5 且无幻觉（R={metrics.set_recall} 幻觉={metrics.hallucinated}）", passed=ok,
        ))
    if case.truth_path and trace:  # 图谱级路径有效性
        metrics.path_valid = _path_valid(trace, case.truth_path)
        checks.append(Check(label="路径有效（覆盖真值图谱节点）", passed=metrics.path_valid))

    passed = all(c.passed for c in checks) and len(checks) > 0
    return CaseResult(
        id=case.id, kind=case.kind, passed=passed, checks=checks,
        answer=answer, tools_called=tools_called, metrics=metrics,
    )
