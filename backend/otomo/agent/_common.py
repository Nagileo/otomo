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
    ProgressEvent,
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
_RUNTIME_STATE_MARKER = "[[OTOMO_RUNTIME_STATE]]"


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
    # 用户已显式授权(mild/full，含点 followup chips 后 api 设的 mode)就不再追问，避免"讲结局"被反复判 followup
    spoiler["pending_followup"] = bool(policy.needs_followup and spoiler.get("mode") not in {"mild", "full"})
    if policy.needs_followup and policy.followup_question:
        spoiler["followup_question"] = policy.followup_question
    else:
        spoiler.pop("followup_question", None)
    st["spoiler"] = spoiler


def _memory_prompt_lines(memory: dict[str, Any]) -> list[str]:
    likes = [x.get("value") for x in (memory.get("likes") or []) if isinstance(x, dict) and x.get("value")]
    dislikes = [x.get("value") for x in (memory.get("dislikes") or []) if isinstance(x, dict) and x.get("value")]
    recent = []
    for item in (memory.get("recent_feedback") or [])[:6]:
        if not isinstance(item, dict):
            continue
        label = item.get("name") or item.get("subject_id") or "候选"
        signal = item.get("signal") or "feedback"
        note = item.get("note") or ""
        recent.append(f"{label}:{signal}" + (f"({note})" if note else ""))

    lines = [
        f"- memory_user={memory.get('username') or 'unknown'}；spoiler_default={memory.get('spoiler_default') or 'none'}。",
    ]
    if likes:
        lines.append("- 长期喜欢/正偏好：" + "、".join(str(x) for x in likes[:12]) + "。")
    if dislikes:
        lines.append("- 长期避雷/负偏好：" + "、".join(str(x) for x in dislikes[:12]) + "；推荐时应降权或解释避雷命中。")
    progress = memory.get("progress") or {}
    if isinstance(progress, dict) and progress:
        parts = []
        for subject, item in list(progress.items())[:8]:
            if isinstance(item, dict) and item.get("episode") is not None:
                parts.append(f"{subject}=第{item.get('episode')}集")
        if parts:
            lines.append("- 已知观看进度：" + "、".join(parts) + "；涉及这些作品时按进度防剧透。")
    if recent:
        lines.append("- 近期推荐反馈：" + "；".join(recent) + "。")
    visual_recent = []
    for item in (memory.get("recent_visual_feedback") or [])[:6]:
        if not isinstance(item, dict):
            continue
        title = item.get("predicted_subject_name") or item.get("predicted_title") or "视觉候选"
        signal = item.get("signal") or "feedback"
        visual_recent.append(f"{title}:{signal}")
    if visual_recent:
        lines.append("- 近期视觉识别反馈：" + "；".join(visual_recent) + "；视觉候选需优先回锚并让用户确认。")
    watch_plan = memory.get("watch_plan") or []
    if isinstance(watch_plan, list) and watch_plan:
        plan_bits = []
        for item in watch_plan[:8]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("subject_id")
                status = item.get("status") or "plan"
                if name:
                    plan_bits.append(f"{name}:{status}")
        if plan_bits:
            lines.append("- 本地计划板：" + "；".join(plan_bits) + "。")
    pending_write_actions = memory.get("pending_write_actions") or []
    if isinstance(pending_write_actions, list) and pending_write_actions:
        lines.append("- 有待用户确认的 Bangumi 写回动作；不要声称已执行，等待前端确认。")
    profiles = memory.get("profile_snapshot") or {}
    if isinstance(profiles, dict) and profiles:
        chunks = []
        for subject_type, snap in list(profiles.items())[:3]:
            if not isinstance(snap, dict):
                continue
            tags = []
            for item in (snap.get("top_tags") or [])[:6]:
                if isinstance(item, dict) and item.get("tag"):
                    tags.append(str(item["tag"]))
            favs = [str(x) for x in (snap.get("favorites") or [])[:3]]
            parts = []
            if tags:
                parts.append("tags=" + "/".join(tags))
            if favs:
                parts.append("fav=" + "/".join(favs))
            if parts:
                chunks.append(f"{subject_type}(" + "；".join(parts) + ")")
        if chunks:
            lines.append("- 最近画像摘要：" + "、".join(chunks) + "。")
    aspect_profiles = memory.get("aspect_profiles") or {}
    if isinstance(aspect_profiles, dict) and aspect_profiles:
        chunks = []
        for subject_type, profile in list(aspect_profiles.items())[:3]:
            if not isinstance(profile, dict):
                continue
            likes = [
                f"{x.get('label') or x.get('aspect')}({float(x.get('weight') or 0):.2f})"
                for x in (profile.get("likes") or [])[:4]
                if isinstance(x, dict)
            ]
            dislikes = [
                f"{x.get('label') or x.get('aspect')}({float(x.get('weight') or 0):.2f})"
                for x in (profile.get("dislikes") or [])[:4]
                if isinstance(x, dict)
            ]
            parts = []
            if likes:
                parts.append("好球=" + "/".join(likes))
            if dislikes:
                parts.append("雷区=" + "/".join(dislikes))
            if parts:
                chunks.append(f"{subject_type}(" + "；".join(parts) + ")")
        if chunks:
            lines.append("- aspect 情感画像：" + "、".join(chunks) + "。")
    lines.append("- 长期记忆仅作为个性化弱证据；用户本轮明确要求优先于历史记忆。")
    return lines


def _last_recommend_prompt_lines(last: dict[str, Any]) -> list[str]:
    items = [
        f"{x.get('id')}:{x.get('name')}"
        for x in (last.get("items") or [])[:12]
        if isinstance(x, dict) and x.get("id")
    ]
    if not items:
        return []
    parts = [
        "- 上轮推荐候选：" + "、".join(items) + "。",
        "- 如果用户说“换一批/这些不要/短一点/更冷门/别这个题材”，这是 critiquing；调用 recommend_subjects 时把上轮 id 放进 exclude_ids，并按语义设置 niche/max_episodes/prefer_tags/avoid_tags。只有明确“以后别推X/不要这种”才写长期 memory。",
    ]
    args = last.get("args")
    if isinstance(args, dict):
        parts.append(f"- 上轮推荐参数：{json.dumps(args, ensure_ascii=False)[:360]}")
    return parts


def runtime_state_prompt(state: Any | None) -> str:
    """Serialize short-term runtime controls that should affect this turn."""
    if state is None:
        return ""
    st = getattr(state, "short_term", {}) or {}
    spoiler = st.get("spoiler") or {}
    memory = st.get("memory") or {}
    last_recommend = st.get("last_recommend") or {}
    attachments = st.get("attachments") or []
    if not spoiler and not memory and not last_recommend and not attachments:
        return ""
    parts = ["运行时用户偏好："]
    if spoiler:
        mode = spoiler.get("mode") or "none"
        progress = spoiler.get("progress_episode")
        parts.append(f"- spoiler_mode={mode}（none=无剧透，mild=轻微剧透，full=允许完整剧透）。")
        if progress is not None:
            parts.append(f"- progress_episode={progress}；分集讨论和剧情回答不得越过该集。")
        if spoiler.get("pending_followup"):
            parts.append("- 本轮问题可能要求后续剧情/结局，但用户未授权剧透；先追问无剧透/轻微/完整剧透，不要直接回答剧透内容。")
        parts.append("- 调 get_episode_comments 时，如果涉及分集进度，必须传 max_episode_sort/progress 对应参数。")
    if isinstance(memory, dict) and memory:
        parts.extend(_memory_prompt_lines(memory))
    if isinstance(last_recommend, dict) and last_recommend:
        parts.extend(_last_recommend_prompt_lines(last_recommend))
    if isinstance(attachments, list) and attachments:
        image_bits = []
        for item in attachments[:4]:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri") or item.get("image_url")
            filename = item.get("filename") or "image"
            mime_type = item.get("mime_type") or "image"
            if uri:
                image_bits.append(f"{filename}({mime_type})={uri}")
        if image_bits:
            parts.append("- 本轮用户上传图片：" + "；".join(image_bits) + "。")
            parts.append("- 若用户要求识别截图/角色/作品/画面线索，调用 identify_acgn_screenshot；单图可传 image_url，多图传 image_urls=[upload://...]；不要把 upload:// 当普通网页链接。")
    return "\n".join(parts)


def inject_runtime_state(messages: list[dict], state: Any | None) -> None:
    prompt = runtime_state_prompt(state)
    messages[:] = [
        m for m in messages
        if not (m.get("role") == "system" and str(m.get("content") or "").startswith(_RUNTIME_STATE_MARKER))
    ]
    if prompt:
        messages.append({"role": "system", "content": f"{_RUNTIME_STATE_MARKER}\n{prompt}"})


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
    memory = st.get("memory") or {}
    if memory:
        events.append(StateEvent(scope="memory", snapshot=memory))
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
    if d.get("profile") and isinstance(d["profile"], dict) and "likes" in d["profile"]:
        profile = d["profile"]
        return (
            f"aspect画像：好球 {len(profile.get('likes') or [])} 项，"
            f"雷区 {len(profile.get('dislikes') or [])} 项，样本 {profile.get('sample_count') or d.get('samples_seen') or 0}"
        )
    if d.get("queue"):
        names = [x.get("name") for x in d["queue"][:4] if isinstance(x, dict) and x.get("name")]
        return f"追番副驾：{len(d['queue'])} 个本周候选" + (f"：{', '.join(names)}" if names else "")
    if d.get("week") and d.get("sections"):
        return f"周报：{d.get('week')} · {len(d.get('sections') or [])} 个分区"
    if d.get("sections"):
        types = [x.get("subject_type") for x in d["sections"] if isinstance(x, dict)]
        return f"口味报告：{len(d['sections'])} 个媒介分区（{', '.join(str(x) for x in types[:5])}）"
    if d.get("memory"):
        mem = d["memory"]
        likes = len(mem.get("likes") or [])
        dislikes = len(mem.get("dislikes") or [])
        feedback = len(mem.get("recent_feedback") or [])
        return f"记忆：喜欢 {likes} 项，避雷 {dislikes} 项，近期反馈 {feedback} 条"
    if d.get("guide_comment_digests"):
        digests = d["guide_comment_digests"]
        parts = []
        for x in digests[:2]:
            summary = "；".join(str(s) for s in (x.get("opinion_summary") or [])[:2])
            label = x.get("author") or x.get("video_title") or "B站导视"
            parts.append(f"{label}：{summary}" if summary else str(label))
        return f"{d.get('count', 0)} 部；导视评论 {len(digests)} 个视频；" + " / ".join(parts)
    if d.get("candidates") and d.get("raw_vlm_answer") is not None:
        names = [x.get("bangumi_name") or x.get("title") for x in d.get("candidates", [])[:4] if isinstance(x, dict)]
        return f"截图识别候选 {len(d.get('candidates') or [])} 个" + (f"：{', '.join(str(x) for x in names if x)}" if names else "")
    if d.get("access_level") and any(k in d for k in ("subtitle_summary", "danmaku_summary", "comment_summary", "metadata_summary")):
        pieces = []
        for key in ("subtitle_summary", "danmaku_summary", "comment_summary", "metadata_summary"):
            vals = d.get(key) or []
            if vals:
                pieces.append(str(vals[0])[:80])
        return f"B站视频内容层级={d.get('access_level')}；" + " / ".join(pieces[:2])
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
    if d.get("read_layers") and (d.get("content_summary") or d.get("audience_summary")):
        layers = "/".join(d.get("read_layers") or [])
        content = (d.get("content_summary") or d.get("audience_summary") or [""])[0]
        return f"B站视频分析：{layers} · {content[:80]}"
    if d.get("aspect_opinions"):
        return f"{len(d['aspect_opinions'])} 条方面观点"
    for key in ("subjects", "characters", "persons"):
        if key in d and isinstance(d[key], list):
            names = [it.get("name_cn") or it.get("name") for it in d[key][:5]]
            names = [n for n in names if n]
            return f"{d.get('count', len(d[key]))} 条：{', '.join(names)}" if names else f"{d.get('count', 0)} 条"
    return d.get("name_cn") or d.get("name") or "ok"


_PANEL_TOOLS = {
    "review_subject",
    "compare_user_taste",
    "season_guide_brief",
    "recommend_subjects",
    "explore_voice_network",
    "episode_buzz_radar",
    "identify_acgn_screenshot",
    "extract_visual_text",
    "recommend_by_visual_style",
    "search_image_source",
    "analyze_video_frames",
    "summarize_bilibili_video_content",
    "build_aspect_profile",
    "plan_watch_copilot",
    "build_taste_report",
    "build_collection_dashboard",
    "build_weekly_digest",
    "configure_weekly_digest",
    "generate_weekly_digest_now",
    "list_weekly_digest_inbox",
    "get_user_memory",
    "remember_user_preference",
    "forget_user_memory",
    "record_recommendation_feedback",
    "prepare_bangumi_write_action",
    "cancel_bangumi_write_action",
    "upsert_watch_plan_item",
    "list_watch_plan",
    "record_decision_log",
    "save_recommendation_list",
}
_MEMORY_TOOLS = {
    "get_user_memory",
    "remember_user_preference",
    "forget_user_memory",
    "record_recommendation_feedback",
    "prepare_bangumi_write_action",
    "cancel_bangumi_write_action",
    "upsert_watch_plan_item",
    "list_watch_plan",
    "record_decision_log",
    "save_recommendation_list",
    "configure_weekly_digest",
    "generate_weekly_digest_now",
    "list_weekly_digest_inbox",
}
_MEMORY_STATE_TOOLS = _MEMORY_TOOLS | {"build_aspect_profile", "build_taste_report", "build_collection_dashboard"}


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
        "source_groups": _trim_dicts(data.get("source_groups"), limit=8),
        "source_routing_notes": _trim_strings(data.get("source_routing_notes"), limit=8),
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
        copied["aspect_matches"] = _trim_strings(copied.get("aspect_matches"), limit=6, text_limit=120)
        copied["aspect_warnings"] = _trim_strings(copied.get("aspect_warnings"), limit=6, text_limit=120)
        copied["source_routes"] = _trim_strings(copied.get("source_routes"), limit=6, text_limit=160)
        copied["media_subtype"] = copied.get("media_subtype")
        copied["media_notes"] = _trim_strings(copied.get("media_notes"), limit=5, text_limit=120)
        items.append(copied)
    return {
        "subject_type": data.get("subject_type"),
        "based_on_tags": _trim_strings(data.get("based_on_tags"), limit=12, text_limit=40),
        "mode": data.get("mode"),
        "items": items,
        "notes": _trim_strings(data.get("notes"), limit=6, text_limit=220),
        "applied_constraints": _trim_strings(data.get("applied_constraints"), limit=8, text_limit=160),
        "aspect_profile_summary": data.get("aspect_profile_summary") if isinstance(data.get("aspect_profile_summary"), dict) else {},
        "cold_start_questions": _trim_strings(data.get("cold_start_questions"), limit=4, text_limit=80),
        "critique_chips": _trim_strings(data.get("critique_chips"), limit=6, text_limit=80),
        "mapping_warnings": _trim_strings(data.get("mapping_warnings"), limit=8, text_limit=160),
        "media_strategy": data.get("media_strategy") if isinstance(data.get("media_strategy"), dict) else {},
    }


def _safe_aspect_profile_payload(data: dict[str, Any]) -> dict[str, Any]:
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    return {
        "username": data.get("username") or profile.get("username"),
        "subject_type": data.get("subject_type") or profile.get("subject_type"),
        "profile": {
            "username": profile.get("username"),
            "subject_type": profile.get("subject_type"),
            "likes": _trim_dicts(profile.get("likes"), limit=8),
            "dislikes": _trim_dicts(profile.get("dislikes"), limit=8),
            "sample_count": profile.get("sample_count"),
            "extraction_source": profile.get("extraction_source"),
            "updated_at": profile.get("updated_at"),
        },
        "samples_seen": data.get("samples_seen"),
        "extraction_source": data.get("extraction_source"),
        "caveats": _trim_strings(data.get("caveats"), limit=8, text_limit=180),
    }


def _safe_watch_copilot_payload(data: dict[str, Any]) -> dict[str, Any]:
    def items(key: str, limit: int) -> list[dict[str, Any]]:
        out = []
        for item in _trim_dicts(data.get(key), limit=limit):
            copied = dict(item)
            copied["why"] = _trim_strings(copied.get("why"), limit=5, text_limit=140)
            copied["tags"] = _trim_strings(copied.get("tags"), limit=8, text_limit=40)
            out.append(copied)
        return out

    return {
        "username": data.get("username"),
        "profile_tags": _trim_strings(data.get("profile_tags"), limit=12, text_limit=40),
        "queue": items("queue", 12),
        "continue_watching": items("continue_watching", 8),
        "start_from_wishlist": items("start_from_wishlist", 8),
        "revive_on_hold": items("revive_on_hold", 8),
        "notes": _trim_strings(data.get("notes"), limit=8, text_limit=180),
    }


def _safe_weekly_digest_payload(data: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for section in _trim_dicts(data.get("sections"), limit=5):
        copied = dict(section)
        items = []
        for item in _trim_dicts(copied.get("items"), limit=8):
            item_copy = dict(item)
            item_copy["why"] = _trim_strings(item_copy.get("why"), limit=5, text_limit=140)
            item_copy["tags"] = _trim_strings(item_copy.get("tags"), limit=8, text_limit=40)
            items.append(item_copy)
        copied["items"] = items
        copied["notes"] = _trim_strings(copied.get("notes"), limit=4, text_limit=140)
        sections.append(copied)
    return {
        "username": data.get("username"),
        "week": data.get("week"),
        "profile_tags": _trim_strings(data.get("profile_tags"), limit=12, text_limit=40),
        "sections": sections,
        "next_actions": _trim_strings(data.get("next_actions"), limit=8, text_limit=160),
        "caveats": _trim_strings(data.get("caveats"), limit=8, text_limit=180),
    }


def _safe_taste_report_payload(data: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for section in _trim_dicts(data.get("sections"), limit=5):
        copied = dict(section)
        copied["top_tags"] = _trim_dicts(copied.get("top_tags"), limit=10)
        copied["favorites"] = _trim_strings(copied.get("favorites"), limit=6, text_limit=80)
        copied["aspect_likes"] = _trim_dicts(copied.get("aspect_likes"), limit=6)
        copied["aspect_dislikes"] = _trim_dicts(copied.get("aspect_dislikes"), limit=6)
        copied["next_actions"] = _trim_strings(copied.get("next_actions"), limit=5, text_limit=140)
        sections.append(copied)
    return {
        "username": data.get("username"),
        "sections": sections,
        "global_likes": _trim_dicts(data.get("global_likes"), limit=10),
        "global_dislikes": _trim_dicts(data.get("global_dislikes"), limit=10),
        "recent_feedback": _trim_dicts(data.get("recent_feedback"), limit=10),
        "share_summary": _trim_text(data.get("share_summary"), 220),
        "report_tags": _trim_strings(data.get("report_tags"), limit=12, text_limit=40),
        "caveats": _trim_strings(data.get("caveats"), limit=8, text_limit=180),
        "memory": _safe_memory_payload({"memory": data.get("memory")}) if isinstance(data.get("memory"), dict) else None,
    }


def _safe_collection_dashboard_payload(data: dict[str, Any]) -> dict[str, Any]:
    media = []
    for item in _trim_dicts(data.get("media"), limit=8):
        copied = dict(item)
        copied["top_tags"] = _trim_dicts(copied.get("top_tags"), limit=12)
        copied["high_rated"] = _trim_dicts(copied.get("high_rated"), limit=8)
        copied["backlog"] = _trim_dicts(copied.get("backlog"), limit=8)
        copied["on_hold_or_abandoned"] = _trim_dicts(copied.get("on_hold_or_abandoned"), limit=8)
        copied["notes"] = _trim_strings(copied.get("notes"), limit=5, text_limit=180)
        media.append(copied)
    return {
        "username": data.get("username"),
        "generated_at": data.get("generated_at"),
        "totals": data.get("totals") or {},
        "media": media,
        "global_top_tags": _trim_dicts(data.get("global_top_tags"), limit=18),
        "rating_strictness": _trim_text(data.get("rating_strictness"), 240),
        "plan_summary": data.get("plan_summary") or {},
        "weekly_subscription": data.get("weekly_subscription") or {},
        "memory_signals": data.get("memory_signals") or {},
        "recommendations_for_next_step": _trim_strings(data.get("recommendations_for_next_step"), limit=6, text_limit=180),
        "caveats": _trim_strings(data.get("caveats"), limit=6, text_limit=180),
    }


def _safe_memory_payload(data: dict[str, Any]) -> dict[str, Any]:
    memory = data.get("memory") if isinstance(data.get("memory"), dict) else data
    if not isinstance(memory, dict):
        return {}
    progress = memory.get("progress") if isinstance(memory.get("progress"), dict) else {}
    return {
        "username": memory.get("username"),
        "likes": _trim_dicts(memory.get("likes"), limit=12),
        "dislikes": _trim_dicts(memory.get("dislikes"), limit=12),
        "spoiler_default": memory.get("spoiler_default"),
        "progress": dict(list(progress.items())[:20]),
        "recent_feedback": _trim_dicts(memory.get("recent_feedback"), limit=10),
        "recent_visual_feedback": _trim_dicts(memory.get("recent_visual_feedback"), limit=10),
        "profile_snapshot": memory.get("profile_snapshot") if isinstance(memory.get("profile_snapshot"), dict) else {},
        "aspect_profiles": memory.get("aspect_profiles") if isinstance(memory.get("aspect_profiles"), dict) else {},
        "pending_write_actions": _trim_dicts(memory.get("pending_write_actions"), limit=8),
        "recent_decisions": _trim_dicts(memory.get("recent_decisions"), limit=10),
        "watch_plan": _trim_dicts(memory.get("watch_plan"), limit=20),
        "recommendation_lists": _trim_dicts(memory.get("recommendation_lists"), limit=6),
        "weekly_digest_subscription": memory.get("weekly_digest_subscription")
        if isinstance(memory.get("weekly_digest_subscription"), dict) else {},
        "inbox": _trim_dicts(memory.get("inbox"), limit=8),
        "updated_at": memory.get("updated_at"),
    }


def _safe_weekly_inbox_payload(data: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _trim_dicts(data.get("items"), limit=8):
        copied = dict(item)
        payload = copied.get("payload") if isinstance(copied.get("payload"), dict) else {}
        copied["payload"] = _safe_weekly_digest_payload(payload) if payload else {}
        items.append(copied)
    return {
        "username": data.get("username"),
        "items": items,
        "subscription": data.get("subscription") if isinstance(data.get("subscription"), dict) else None,
        "message": data.get("message"),
        "memory": _safe_memory_payload({"memory": data.get("memory")}) if isinstance(data.get("memory"), dict) else None,
    }


def _safe_explorer_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor": data.get("anchor"),
        "anchor_kind": data.get("anchor_kind"),
        "nodes": _trim_dicts(data.get("nodes"), limit=20),
        "notes": _trim_strings(data.get("notes"), limit=4, text_limit=160),
    }


def _safe_episode_radar_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": data.get("subject_id"),
        "total": data.get("total"),
        "curve": _trim_dicts(data.get("curve"), limit=60),
        "peaks": _trim_dicts(data.get("peaks"), limit=10),
        "notes": _trim_strings(data.get("notes"), limit=4, text_limit=160),
    }


def _safe_multimodal_payload(data: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in _trim_dicts(data.get("candidates"), limit=10):
        copied = dict(item)
        copied["reason"] = _trim_text(copied.get("reason"), 180)
        copied["match_note"] = _trim_text(copied.get("match_note"), 160)
        candidates.append(copied)
    characters = []
    for item in _trim_dicts(data.get("character_candidates"), limit=8):
        copied = dict(item)
        copied["reason"] = _trim_text(copied.get("reason"), 160)
        copied["match_note"] = _trim_text(copied.get("match_note"), 140)
        characters.append(copied)
    return {
        "question": data.get("question"),
        "image_refs": _trim_strings(data.get("image_refs"), limit=4, text_limit=500),
        "raw_vlm_answer": _trim_text(data.get("raw_vlm_answer"), 600),
        "candidates": candidates,
        "character_candidates": characters,
        "visual_tags": _trim_strings(data.get("visual_tags"), limit=12, text_limit=40),
        "ocr_text": _trim_text(data.get("ocr_text"), 1000),
        "caveats": _trim_strings(data.get("caveats"), limit=6, text_limit=180),
    }


def _safe_visual_text_payload(data: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _trim_dicts(data.get("structured_items"), limit=12):
        copied = dict(item)
        copied["name"] = _trim_text(copied.get("name"), 120)
        copied["value"] = _trim_text(copied.get("value"), 300)
        copied["note"] = _trim_text(copied.get("note"), 180)
        items.append(copied)
    entities = []
    for item in _trim_dicts(data.get("entities"), limit=10):
        copied = dict(item)
        copied["name"] = _trim_text(copied.get("name"), 100)
        copied["bangumi_name"] = _trim_text(copied.get("bangumi_name"), 100)
        entities.append(copied)
    return {
        "mode": data.get("mode"),
        "image_count": data.get("image_count"),
        "markdown_text": _trim_text(data.get("markdown_text"), 1800),
        "structured_items": items,
        "entities": entities,
        "visual_tags": _trim_strings(data.get("visual_tags"), limit=16, text_limit=40),
        "confidence": data.get("confidence"),
        "raw_vlm_answer": _trim_text(data.get("raw_vlm_answer"), 600),
        "caveats": _trim_strings(data.get("caveats"), limit=6, text_limit=180),
    }


def _safe_visual_style_payload(data: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in _trim_dicts(data.get("candidates"), limit=12):
        copied = dict(item)
        copied["reason"] = _trim_text(copied.get("reason"), 160)
        copied["matched_tags"] = _trim_strings(copied.get("matched_tags"), limit=8, text_limit=40)
        candidates.append(copied)
    return {
        "style_description": _trim_text(data.get("style_description"), 900),
        "visual_tags": _trim_strings(data.get("visual_tags"), limit=16, text_limit=40),
        "bangumi_tags": _trim_strings(data.get("bangumi_tags"), limit=10, text_limit=40),
        "candidates": candidates,
        "confidence": data.get("confidence"),
        "raw_vlm_answer": _trim_text(data.get("raw_vlm_answer"), 600),
        "caveats": _trim_strings(data.get("caveats"), limit=6, text_limit=180),
    }


def _safe_image_source_payload(data: dict[str, Any]) -> dict[str, Any]:
    matches = []
    for item in _trim_dicts(data.get("matches"), limit=12):
        copied = dict(item)
        copied["title"] = _trim_text(copied.get("title"), 120)
        copied["author"] = _trim_text(copied.get("author"), 80)
        copied["note"] = _trim_text(copied.get("note"), 140)
        matches.append(copied)
    return {
        "matches": matches,
        "navigation_links": _trim_dicts(data.get("navigation_links"), limit=8),
        "caveats": _trim_strings(data.get("caveats"), limit=8, text_limit=180),
    }


def _safe_video_frames_payload(data: dict[str, Any]) -> dict[str, Any]:
    frames = []
    for item in _trim_dicts(data.get("frames"), limit=12):
        copied = dict(item)
        copied["ocr_text"] = _trim_text(copied.get("ocr_text"), 700)
        copied["structured_items"] = _trim_dicts(copied.get("structured_items"), limit=6)
        copied["candidates"] = _trim_dicts(copied.get("candidates"), limit=5)
        copied["visual_tags"] = _trim_strings(copied.get("visual_tags"), limit=10, text_limit=40)
        frames.append(copied)
    return {
        "frame_count": data.get("frame_count"),
        "purpose": data.get("purpose"),
        "frames": frames,
        "merged_ocr_text": _trim_text(data.get("merged_ocr_text"), 1800),
        "candidate_subjects": _trim_dicts(data.get("candidate_subjects"), limit=8),
        "caveats": _trim_strings(data.get("caveats"), limit=8, text_limit=180),
    }


def _safe_bili_video_content_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "aid": data.get("aid"),
        "bvid": data.get("bvid"),
        "cid": data.get("cid"),
        "title": _trim_text(data.get("title"), 180),
        "source_url": data.get("source_url"),
        "access_level": data.get("access_level"),
        "read_layers": _trim_strings(data.get("read_layers"), limit=6, text_limit=40),
        "content_summary": _trim_strings(data.get("content_summary"), limit=8, text_limit=220),
        "audience_summary": _trim_strings(data.get("audience_summary"), limit=8, text_limit=180),
        "subtitle_summary": _trim_strings(data.get("subtitle_summary"), limit=6, text_limit=220),
        "danmaku_summary": _trim_strings(data.get("danmaku_summary"), limit=6, text_limit=160),
        "comment_summary": _trim_strings(data.get("comment_summary"), limit=6, text_limit=160),
        "metadata_summary": _trim_strings(data.get("metadata_summary"), limit=6, text_limit=220),
        "subtitle_segments": _trim_dicts(data.get("subtitle_segments"), limit=10),
        "danmaku_samples": _trim_dicts(data.get("danmaku_samples"), limit=12),
        "comment_samples": _trim_strings(data.get("comment_samples"), limit=12, text_limit=180),
        "analysis_plan": _trim_strings(data.get("analysis_plan"), limit=6, text_limit=180),
        "caveats": _trim_strings(data.get("caveats"), limit=10, text_limit=180),
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
    if name == "build_aspect_profile":
        return _safe_aspect_profile_payload(data)
    if name == "plan_watch_copilot":
        return _safe_watch_copilot_payload(data)
    if name == "build_weekly_digest":
        return _safe_weekly_digest_payload(data)
    if name in {"configure_weekly_digest", "generate_weekly_digest_now", "list_weekly_digest_inbox"}:
        return _safe_weekly_inbox_payload(data)
    if name == "build_taste_report":
        return _safe_taste_report_payload(data)
    if name == "build_collection_dashboard":
        return _safe_collection_dashboard_payload(data)
    if name == "explore_voice_network":
        return _safe_explorer_payload(data)
    if name == "episode_buzz_radar":
        return _safe_episode_radar_payload(data)
    if name == "identify_acgn_screenshot":
        return _safe_multimodal_payload(data)
    if name == "extract_visual_text":
        return _safe_visual_text_payload(data)
    if name == "recommend_by_visual_style":
        return _safe_visual_style_payload(data)
    if name == "search_image_source":
        return _safe_image_source_payload(data)
    if name == "analyze_video_frames":
        return _safe_video_frames_payload(data)
    if name == "summarize_bilibili_video_content":
        return _safe_bili_video_content_payload(data)
    if name in _MEMORY_TOOLS:
        return _safe_memory_payload(data)
    return None


def panel_data(name: str, result: ToolResult) -> dict[str, Any] | None:
    if name not in _PANEL_TOOLS or not result.ok or result.data is None:
        return None
    return panel_data_from_payload(name, result.data.model_dump(mode="json", exclude_none=True))


def memory_state_from_result(name: str, result: ToolResult) -> dict[str, Any] | None:
    if name not in _MEMORY_STATE_TOOLS or not result.ok or result.data is None:
        return None
    payload = result.data.model_dump(mode="json", exclude_none=True)
    if name == "build_aspect_profile":
        data = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
        return _safe_memory_payload(data) if data else None
    if name == "build_taste_report":
        data = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
        return _safe_memory_payload(data) if data else None
    data = panel_data_from_payload(name, payload)
    return data if data else None


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


def update_last_recommend_state(
    state: Any | None,
    name: str,
    args: dict[str, Any],
    result: ToolResult,
) -> None:
    if state is None or name != "recommend_subjects" or not result.ok or result.data is None:
        return
    st = getattr(state, "short_term", None)
    if st is None:
        return
    payload = result.data.model_dump(mode="json", exclude_none=True)
    items = [
        {"id": it.get("id"), "name": it.get("name")}
        for it in (payload.get("items") or [])[:20]
        if isinstance(it, dict) and it.get("id")
    ]
    if not items:
        return
    st["last_recommend"] = {
        "subject_type": payload.get("subject_type"),
        "mode": payload.get("mode"),
        "based_on_tags": payload.get("based_on_tags") or [],
        "args": args,
        "items": items,
    }


async def step_tools(
    registry: ToolRegistry,
    msg: Any,
    messages: list[dict],
    sources: list[Citation],
    seen_urls: set[str],
    state: Any | None = None,
) -> AsyncIterator[Any]:
    """执行一轮（一个 assistant 回合里的全部 tool_calls），产出 ToolCall/Observation 事件。
    side effect：把 assistant 与 tool 结果消息追加进 messages、新来源并入 sources。"""
    messages.append(assistant_toolcalls_msg(msg))
    for tc in msg.tool_calls:
        call_args = safe_json(tc.function.arguments)
        yield ToolCallEvent(name=tc.function.name, args=call_args)
        yield ProgressEvent(stage="tool_start", tool=tc.function.name, summary=f"开始执行 {tc.function.name}")
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
        yield ProgressEvent(
            stage="tool_done" if result.ok else "tool_error",
            tool=tc.function.name,
            summary=f"{'完成' if result.ok else '失败'} {tc.function.name}: {summarize(result)}",
        )
        if memory_state := memory_state_from_result(tc.function.name, result):
            yield StateEvent(scope="memory", snapshot=memory_state)
        update_last_recommend_state(state, tc.function.name, call_args, result)
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
    state: Any | None = None,
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
        async for ev in step_tools(registry, msg, messages, sources, seen_urls, state):
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
