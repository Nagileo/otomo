"""两个 runner（ReAct / Plan-Execute）共享的底层：

- 工具标记泄漏处理（DeepSeek 偶把 tool call 写成 DSML 文本）
- 观察摘要
- step_tools：执行一轮工具调用并产出结构化事件（side effect 落到 state/sources）
- stream_answer：无工具的流式最终答案（带泄漏护栏）
- trim_messages：滑动窗口（控制送入 LLM 的上下文长度，配对安全）
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from .contracts import (
    AnswerDeltaEvent,
    Citation,
    EntityRef,
    ObservationEvent,
    StateEvent,
    ToolCallEvent,
    ToolResult,
)
from .registry import ToolRegistry

# DeepSeek 等模型偶尔把工具调用写成 DSML 文本塞进 content，而非结构化 tool_calls 字段
LEAK_MARKERS = ("｜DSML｜", "DSML", "tool_calls", "invoke name", "<｜")
CORRECT_FC = (
    "请使用函数调用（tool calls）来调用工具，不要在回复正文里输出 invoke / DSML / tool_calls 等标记。"
    "若信息已足够，请直接给出最终自然语言答案。"
)


def _legacy_runtime_state_prompt(state: Any | None) -> str:
    """Serialize short-term runtime controls that should affect this turn."""
    if state is None:
        return ""
    st = getattr(state, "short_term", {}) or {}
    spoiler = st.get("spoiler") or {}
    if not spoiler:
        return ""
    mode = spoiler.get("mode") or "none"
    progress = spoiler.get("progress_episode")
    parts = [
        "运行时用户偏好：",
        f"- spoiler_mode={mode}（none=无剧透，mild=轻微剧透，full=允许完整剧透）。",
    ]
    if progress is not None:
        parts.append(f"- progress_episode={progress}；分集讨论/剧情回答不得越过该集。")
    if spoiler.get("pending_followup"):
        parts.append("- 本轮问题可能要求后续剧情/结局，但用户未授权剧透；先追问无剧透/轻微/完整剧透，不要直接回答剧透内容。")
    parts.append("- 调 get_episode_comments 时，如果涉及分集进度，必须传 max_episode_sort/progress 对应参数。")
    return "\n".join(parts)


def update_spoiler_state_from_input(state: Any | None, user_input: str) -> None:
    """Update conversation-level spoiler controls from explicit natural-language signals."""
    if state is None:
        return
    from ..tools.spoiler.tool import assess_spoiler_policy

    st = getattr(state, "short_term", None)
    if st is None:
        return
    spoiler = dict(st.get("spoiler") or {"mode": "none"})
    default = spoiler.get("mode") or "none"
    policy = assess_spoiler_policy(user_input, default)
    if policy.progress_episode is not None:
        spoiler["progress_episode"] = policy.progress_episode
    if policy.level in {"none", "mild", "full"} and not policy.needs_followup:
        spoiler["mode"] = policy.level
    spoiler["pending_followup"] = bool(policy.needs_followup and policy.level != "full")
    if policy.needs_followup and policy.followup_question:
        spoiler["followup_question"] = policy.followup_question
    else:
        spoiler.pop("followup_question", None)
    st["spoiler"] = spoiler


def runtime_state_prompt(state: Any | None) -> str:
    """Serialize short-term runtime controls that should affect this turn."""
    if state is None:
        return ""
    st = getattr(state, "short_term", {}) or {}
    spoiler = st.get("spoiler") or {}
    if not spoiler:
        return ""
    mode = spoiler.get("mode") or "none"
    progress = spoiler.get("progress_episode")
    parts = [
        "运行时用户偏好：",
        f"- spoiler_mode={mode}（none=无剧透，mild=轻微剧透，full=允许完整剧透）。",
    ]
    if progress is not None:
        parts.append(f"- progress_episode={progress}；分集讨论和剧情回答不得越过该集。")
    if spoiler.get("pending_followup"):
        parts.append("- 本轮问题可能要求后续剧情/结局，但用户未授权剧透；先追问无剧透/轻微/完整剧透，不要直接回答剧透内容。")
    parts.append("- 调 get_episode_comments 时，如果涉及分集进度，必须传 max_episode_sort/progress 对应参数。")
    return "\n".join(parts)


def inject_runtime_state(messages: list[dict], state: Any | None) -> None:
    prompt = runtime_state_prompt(state)
    if prompt:
        messages.append({"role": "system", "content": prompt})


def runtime_state_events(state: Any | None) -> list[StateEvent]:
    """Expose runtime state to the UI before the model starts a turn."""
    if state is None:
        return []
    st = getattr(state, "short_term", {}) or {}
    events: list[StateEvent] = []
    spoiler = st.get("spoiler") or {}
    if spoiler:
        events.append(
            StateEvent(
                scope="spoiler",
                snapshot={
                    "mode": spoiler.get("mode") or "none",
                    "progress_episode": spoiler.get("progress_episode"),
                    "pending_followup": bool(spoiler.get("pending_followup")),
                    "followup_question": spoiler.get("followup_question"),
                },
            )
        )
    return events


def has_leak(text: str | None) -> bool:
    return bool(text) and any(m in text for m in LEAK_MARKERS)


def strip_leak(text: str) -> str:
    idx = len(text)
    for m in LEAK_MARKERS:
        j = text.find(m)
        if j != -1:
            idx = min(idx, j)
    return text[:idx].strip()


_BAD_FINAL_ANSWERS = {"<", "<|", "<｜", "｜", "|"}


def should_fallback_answer(answer: str, leaked: list[bool] | None = None) -> bool:
    """判断最终答案是否只是 DSML / tool-call 泄漏残片，需强制纯文本重写。"""
    s = answer.strip()
    if not s or s in _BAD_FINAL_ANSWERS:
        return True
    if len(s) < 3 and any(ch in s for ch in "<｜|"):
        return True
    return bool(leaked) and len(s) < 20


def safe_json(s: str | None) -> dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                pass
    return {}


def summarize(result: ToolResult) -> str:
    if not result.ok:
        return f"失败：{result.error}"
    if result.data is None:
        return "ok（无数据）"
    d = result.data.model_dump(exclude_none=True)
    if d.get("guide_comment_digests"):
        digests = d["guide_comment_digests"]
        parts = []
        for x in digests[:2]:
            summary = "；".join(str(s) for s in (x.get("opinion_summary") or [])[:2])
            label = x.get("author") or x.get("video_title") or "B站导视"
            parts.append(f"{label}：{summary}" if summary else str(label))
        return f"{d.get('count', 0)} 部；导视评论 {len(digests)} 个视频；" + " / ".join(parts)
    if d.get("opinion_summary"):
        joined = "；".join(str(x) for x in d["opinion_summary"][:3])
        return f"{d.get('count', 0)} 条；{joined}"
    if d.get("aspect_summary"):
        parts = []
        for x in d["aspect_summary"][:3]:
            label = x.get("label") or x.get("aspect")
            sentiment = x.get("dominant_sentiment")
            total = x.get("total")
            parts.append(f"{label}:{sentiment}({total})")
        return "方面摘要：" + "；".join(parts)
    if d.get("aspect_opinions"):
        return f"{len(d['aspect_opinions'])} 条方面观点"
    for key in ("subjects", "characters", "persons"):
        if key in d and isinstance(d[key], list):
            names = [it.get("name_cn") or it.get("name") for it in d[key][:5]]
            names = [n for n in names if n]
            return f"{d.get('count', len(d[key]))} 条：{', '.join(names)}" if names else f"{d.get('count', 0)} 条"
    return d.get("name_cn") or d.get("name") or "ok"


_PANEL_TOOLS = {"review_subject", "compare_user_taste", "season_guide_brief", "recommend_subjects"}


def _trim_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _trim_strings(values: Any, *, limit: int = 6, text_limit: int = 220) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_trim_text(v, text_limit) for v in values[:limit] if str(v or "").strip()]


def _trim_dicts(values: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    return [v for v in (values or [])[:limit] if isinstance(v, dict)]


def _safe_review_payload(data: dict[str, Any]) -> dict[str, Any]:
    comments = []
    for item in _trim_dicts(data.get("comments"), limit=4):
        comments.append({
            **item,
            "samples": _trim_strings(item.get("samples"), limit=3, text_limit=180),
        })
    return {
        "subject_id": data.get("subject_id"),
        "title": data.get("title"),
        "subject_type": data.get("subject_type"),
        "spoiler_level": data.get("spoiler_level"),
        "ratings": _trim_dicts(data.get("ratings"), limit=8),
        "comments": comments,
        "praise": _trim_dicts(data.get("praise"), limit=4),
        "criticism": _trim_dicts(data.get("criticism"), limit=4),
        "aspect_summary": _trim_dicts(data.get("aspect_summary"), limit=8),
        "consensus": data.get("consensus"),
        "confidence": data.get("confidence"),
        "caveats": _trim_strings(data.get("caveats"), limit=8),
        "source_matrix": _trim_dicts(data.get("source_matrix"), limit=10),
        "suggested_summary_points": _trim_strings(data.get("suggested_summary_points"), limit=8),
    }


def _safe_taste_payload(data: dict[str, Any]) -> dict[str, Any]:
    affinity = dict(data.get("affinity") or {})
    for key in ("liked_together", "disliked_together", "biggest_disagreements"):
        affinity[key] = _trim_dicts(affinity.get(key), limit=6)
    affinity["confidence_reasons"] = _trim_strings(affinity.get("confidence_reasons"), limit=6)
    return {
        "username": data.get("username"),
        "peer_username": data.get("peer_username"),
        "subject_type": data.get("subject_type"),
        "affinity": affinity,
        "caveats": _trim_strings(data.get("caveats"), limit=6),
    }


def _safe_season_payload(data: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _trim_dicts(data.get("items"), limit=20):
        copied = dict(item)
        copied["tags"] = _trim_strings(copied.get("tags"), limit=10, text_limit=40)
        copied["match_tags"] = _trim_strings(copied.get("match_tags"), limit=6, text_limit=40)
        copied["evidence"] = _trim_strings(copied.get("evidence"), limit=6, text_limit=160)
        copied["guide_videos"] = _trim_dicts(copied.get("guide_videos"), limit=3)
        items.append(copied)
    digests = []
    for item in _trim_dicts(data.get("guide_comment_digests"), limit=3):
        digests.append({
            **item,
            "opinion_summary": _trim_strings(item.get("opinion_summary"), limit=6, text_limit=160),
            "caveats": _trim_strings(item.get("caveats"), limit=4, text_limit=160),
        })
    return {
        "season": data.get("season"),
        "count": data.get("count"),
        "personalized": data.get("personalized"),
        "profile_tags": _trim_strings(data.get("profile_tags"), limit=12, text_limit=40),
        "focus_tags": _trim_strings(data.get("focus_tags"), limit=8, text_limit=40),
        "items": items,
        "guide_videos": _trim_dicts(data.get("guide_videos"), limit=8),
        "guide_comment_digests": digests,
        "notes": _trim_strings(data.get("notes"), limit=6, text_limit=220),
    }


def _safe_recommend_payload(data: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _trim_dicts(data.get("items"), limit=20):
        copied = dict(item)
        copied["reasons"] = _trim_strings(copied.get("reasons"), limit=8, text_limit=160)
        copied["explicit_tag_matches"] = _trim_strings(copied.get("explicit_tag_matches"), limit=8, text_limit=40)
        copied["evidence"] = _trim_dicts(copied.get("evidence"), limit=8)
        copied["external_mappings"] = _trim_dicts(copied.get("external_mappings"), limit=6)
        copied["quality_badges"] = _trim_strings(copied.get("quality_badges"), limit=5, text_limit=120)
        items.append(copied)
    return {
        "subject_type": data.get("subject_type"),
        "based_on_tags": _trim_strings(data.get("based_on_tags"), limit=12, text_limit=40),
        "mode": data.get("mode"),
        "items": items,
        "notes": _trim_strings(data.get("notes"), limit=6, text_limit=220),
    }


def panel_data_from_payload(name: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return UI-safe structured payload for tools that have dedicated evidence panels."""
    if name not in _PANEL_TOOLS or not isinstance(payload, dict):
        return None
    data = payload.get("data") if "data" in payload else payload
    if not isinstance(data, dict):
        return None
    if name == "review_subject":
        return _safe_review_payload(data)
    if name == "compare_user_taste":
        return _safe_taste_payload(data)
    if name == "season_guide_brief":
        return _safe_season_payload(data)
    if name == "recommend_subjects":
        return _safe_recommend_payload(data)
    return None


def panel_data(name: str, result: ToolResult) -> dict[str, Any] | None:
    if name not in _PANEL_TOOLS or not result.ok or result.data is None:
        return None
    return panel_data_from_payload(name, result.data.model_dump(mode="json", exclude_none=True))


# 工具返回里承载实体的容器键 → 实体类型（items=recommend 结果，按 subject 计）
_ENTITY_CONTAINERS = {
    "subjects": "subject", "items": "subject", "relations": "subject",
    "persons": "person", "characters": "character",
}


def _ref(it: dict, etype: str) -> EntityRef | None:
    if not it.get("id"):
        return None
    name = it.get("name_cn") or it.get("name") or ""
    aliases = [x for x in dict.fromkeys([it.get("name"), it.get("name_cn")]) if x]
    return EntityRef(type=etype, id=int(it["id"]), name=name, aliases=aliases)


def extract_entities(result: ToolResult) -> list[EntityRef]:
    """从 typed ToolResult.data 提取 canonical 实体（图谱级校验 / 路径重建用，零额外 API）。"""
    if not result.ok or result.data is None:
        return []
    d = result.data.model_dump(exclude_none=True)
    out: list[EntityRef] = []
    if d.get("id") and (d.get("name") or d.get("name_cn")):  # 单体详情（SubjectDetail 等）
        if (r := _ref(d, "subject")):
            out.append(r)
    for key, etype in _ENTITY_CONTAINERS.items():            # 列表容器
        for it in d.get(key) or []:
            if isinstance(it, dict) and (r := _ref(it, etype)):
                out.append(r)
    return out


def trim_messages(messages: list[dict], max_messages: int = 40) -> list[dict]:
    """滑动窗口：超过阈值时丢弃最旧的轮次，但保留所有 system + 第一条 user，
    且避免让窗口以孤立的 tool 消息开头（否则 OpenAI/DeepSeek 会报错）。"""
    if len(messages) <= max_messages:
        return messages
    head: list[dict] = []
    body: list[dict] = []
    seen_user = False
    for m in messages:
        if m.get("role") == "system" or (m.get("role") == "user" and not seen_user):
            head.append(m)
            if m.get("role") == "user":
                seen_user = True
        else:
            body.append(m)
    keep = max(max_messages - len(head), 0)
    tail = body[-keep:] if keep else []
    while tail and tail[0].get("role") == "tool":  # 别用孤立 tool 消息开头
        tail = tail[1:]
    return head + tail


def assistant_toolcalls_msg(msg: Any) -> dict:
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ],
    }


async def step_tools(
    registry: ToolRegistry,
    msg: Any,
    messages: list[dict],
    sources: list[Citation],
    seen_urls: set[str],
) -> AsyncIterator[Any]:
    """执行一轮（一个 assistant 回合里的全部 tool_calls），产出 ToolCall/Observation 事件。
    side effect：把 assistant 与 tool 结果消息追加进 messages、新来源并入 sources。"""
    messages.append(assistant_toolcalls_msg(msg))
    for tc in msg.tool_calls:
        yield ToolCallEvent(name=tc.function.name, args=safe_json(tc.function.arguments))
        result = await registry.dispatch(tc.function.name, tc.function.arguments)
        for c in result.sources:
            if c.url not in seen_urls:
                seen_urls.add(c.url)
                sources.append(c)
        yield ObservationEvent(
            name=tc.function.name, ok=result.ok, summary=summarize(result),
            sources=result.sources, entities=extract_entities(result),
            data=panel_data(tc.function.name, result),
        )
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.to_observation()})


async def run_tool_round(
    llm: AsyncOpenAI,
    model: str,
    registry: ToolRegistry,
    messages: list[dict],
    tools: list[dict],
    max_iters: int,
    sources: list[Citation],
    seen_urls: set[str],
) -> AsyncIterator[Any]:
    """一轮"执行"：反复让模型调工具直到它不再调（含 DSML 文本误写的纠正）。
    ReAct / Plan-Execute / Adaptive 三个 runner 共用。side effect 落到 messages/sources。"""
    corrections = 0
    for _ in range(max_iters):
        resp = await llm.chat.completions.create(
            model=model, messages=trim_messages(messages), tools=tools, tool_choice="auto"
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if has_leak(msg.content) and corrections < 2:
                corrections += 1
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({"role": "system", "content": CORRECT_FC})
                continue
            return
        async for ev in step_tools(registry, msg, messages, sources, seen_urls):
            yield ev


async def stream_answer(
    llm: AsyncOpenAI, model: str, messages: list[dict], tools: list[dict],
    leaked: list[bool] | None = None,
) -> AsyncIterator[AnswerDeltaEvent]:
    """无工具（tool_choice=none）的流式最终答案；一旦冒出工具标记立即停止吐，并把 True 记入 leaked。

    leaked 让调用方知道"流式中途漏了 DSML 标记"——即使已 yield 了残片（如单个"<"），
    也能据此触发纯文本兜底，而不是把残片当最终答案。"""
    stream = await llm.chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="none", stream=True
    )
    acc = ""
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            acc += delta.content
            if has_leak(acc):
                if leaked is not None:
                    leaked.append(True)
                break
            yield AnswerDeltaEvent(text=delta.content)


_FORCE_TEXT = (
    "基于以上已查到的信息，用纯自然语言给出最终回答。"
    "禁止输出任何工具调用 / invoke / DSML / tool_calls 标记，只要给用户看的结论。"
)


async def compose_fallback(llm: AsyncOpenAI, model: str, messages: list[dict]) -> str:
    """流式合成泄漏/为空时的兜底：彻底不带 tools 调一次，强制纯文本。"""
    resp = await llm.chat.completions.create(
        model=model, messages=messages + [{"role": "system", "content": _FORCE_TEXT}]
    )
    return strip_leak(resp.choices[0].message.content or "")


_FOLLOWUP_PROMPT = (
    "基于以上对话，列出用户可能接着想问的 2-3 个简短问题（每个不超过 20 字，二次元相关、"
    "且能用你的工具回答）。只输出 JSON 字符串数组，如 [\"...\",\"...\"]，不要多余文字。"
)


async def gen_followups(llm: AsyncOpenAI, model: str, messages: list[dict]) -> list[str]:
    """生成 2-3 个追问建议（一次轻量调用，失败返回空）。"""
    try:
        resp = await llm.chat.completions.create(
            model=model, messages=messages + [{"role": "system", "content": _FOLLOWUP_PROMPT}]
        )
        txt = strip_leak(resp.choices[0].message.content or "")
        i, j = txt.find("["), txt.rfind("]")
        if 0 <= i < j:
            arr = json.loads(txt[i : j + 1])
            return [str(x).strip() for x in arr if str(x).strip()][:3]
    except Exception:  # noqa: BLE001
        pass
    return []
