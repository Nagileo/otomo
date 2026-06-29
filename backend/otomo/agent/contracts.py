"""核心契约（见 docs/03-agent-contract）。

手搓版与未来 LangGraph 版都满足同一套契约：Tool / ToolResult / AgentState / AgentRunner，
以及流式吐给前端/trace 的 AgentEvent（结构化事件，**不含裸 CoT**）。
"""
from __future__ import annotations

import abc
import json
from typing import Any, AsyncIterator, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 引用 / 工具结果
# --------------------------------------------------------------------------- #


class Citation(BaseModel):
    """来源引用。萌娘/维基内容必填，用于回答挂可见链接（许可证要求）。"""

    title: str
    url: str
    source: str = "bangumi"  # bangumi / moegirl / wikipedia ...
    image: str | None = None  # 封面图 URL（作品来源时有，前端渲染卡片缩略图）


class EntityRef(BaseModel):
    """Bangumi 图谱实体的 canonical 引用。

    图谱级 Verifier 的基石：把"答案文本/工具返回"锚定到 (type,id)，
    用于 set-F1（答案实体集合 vs 真值集合）与路径边验证（重建 agent 走的图谱路径）。
    """

    type: Literal["subject", "person", "character"]
    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)  # 中日双名/别名，文本命中用


T = TypeVar("T", bound=BaseModel)


class ToolResult(BaseModel, Generic[T]):
    """每个工具返回 typed result（data 为该工具自定义 schema，禁止裸 Any）。"""

    ok: bool
    data: T | None = None
    sources: list[Citation] = Field(default_factory=list)
    error: str | None = None

    def to_observation(self) -> str:
        """回填给 LLM 的紧凑文本（工具消息内容）。"""
        if not self.ok:
            return json.dumps({"ok": False, "error": self.error}, ensure_ascii=False)
        payload: dict[str, Any] = {"ok": True}
        if self.data is not None:
            payload["data"] = self.data.model_dump(mode="json", exclude_none=True)
        if self.sources:
            payload["sources"] = [c.model_dump(mode="json") for c in self.sources]
        return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Tool 抽象基类
# --------------------------------------------------------------------------- #


class Tool(abc.ABC):
    """工具契约：typed 入参/出参 + run()。工具/Skills/MCP 全部自建（不接 Bangumi-MCP/bgm-cli）。"""

    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    is_write: bool = False  # 写操作需人工确认（A1 暂只读）

    @abc.abstractmethod
    async def run(self, args: BaseModel) -> ToolResult:
        ...

    def openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI/DeepSeek function-calling 的 tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


# --------------------------------------------------------------------------- #
# Agent 状态
# --------------------------------------------------------------------------- #


class AgentState(BaseModel):
    """一次会话/任务的状态。messages 用 OpenAI 消息 dict 列表（含 tool 消息）。"""

    messages: list[dict[str, Any]] = Field(default_factory=list)
    short_term: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "awaiting_approval", "done", "failed"] = "running"


# --------------------------------------------------------------------------- #
# 流式事件（结构化、typed、可回放；裸 CoT 不在其中）
# --------------------------------------------------------------------------- #


class PlanEvent(BaseModel):
    type: Literal["plan"] = "plan"
    summary: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any]


class ObservationEvent(BaseModel):
    type: Literal["observation"] = "observation"
    name: str
    ok: bool
    summary: str
    sources: list[Citation] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)  # 该步返回的 canonical 实体（路径重建/校验用）
    data: dict[str, Any] | None = None  # 面板白名单工具的结构化 payload，供前端渲染证据卡片


class StateEvent(BaseModel):
    """运行时状态快照。用于把剧透/记忆/画像这类跨轮状态显式暴露给前端。"""

    type: Literal["state"] = "state"
    scope: Literal["spoiler", "memory", "profile"]
    snapshot: dict[str, Any] = Field(default_factory=dict)


class ReflectEvent(BaseModel):
    type: Literal["reflect"] = "reflect"
    complete: bool
    note: str = ""


class AnswerDeltaEvent(BaseModel):
    type: Literal["answer_delta"] = "answer_delta"
    text: str


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    answer: str
    sources: list[Citation] = Field(default_factory=list)
    steps: int = 0


class ClaimCheckEvent(BaseModel):
    type: Literal["claim_check"] = "claim_check"
    support_rate: float = 0.0
    supported_count: int = 0
    unsupported_count: int = 0
    unverifiable_count: int = 0
    claims: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class FollowupEvent(BaseModel):
    type: Literal["followup"] = "followup"
    questions: list[str] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = (
    PlanEvent
    | ToolCallEvent
    | ObservationEvent
    | StateEvent
    | ReflectEvent
    | AnswerDeltaEvent
    | FinalEvent
    | ClaimCheckEvent
    | FollowupEvent
    | ErrorEvent
)


class AgentRunner(abc.ABC):
    """统一签名：手搓版、LangGraph 版都实现它 → 可一键切换 + A/B。"""

    @abc.abstractmethod
    def stream(self, user_input: str, state: AgentState | None = None) -> AsyncIterator[AgentEvent]:
        ...
