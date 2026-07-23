"""Discord bot 入口:把 Otomo agent 接到 Discord(python -m otomo.discord_bot)。

Otomo 本质是个 ACGN agent,Discord 是它最自然的形态之一——在服务器里 @机器人、
私信、或用斜杠命令,就能问番/推荐/评价/查资源/识梗,复用全部工具。

当前能力:
- 触发:私信直接答;服务器里被 @ 才答;常用工作流提供原生斜杠命令。
- 多轮:按 Discord 用户+频道隔离持久会话，并与共享长期记忆分层。
- **账号绑定**:/绑定 → 一条链接 → 用自己的 Bangumi 登录 → 之后个人化(用你的收藏
  画像推荐、查你的追番进度)。未绑定=guest 模式(公开知识问答照常)。
- 输出:去 [[panel:x]] 锚点、按 2000 字上限分段，并把高频产品面板转成 embed/按钮。

依赖:pip install -e ".[discord]"(discord.py)。需 DISCORD_BOT_TOKEN,且在开发者后台
开启 MESSAGE CONTENT INTENT。绑定复用 AUTH_ENCRYPTION_KEY(bot 与 backend 共享)。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from fastapi import HTTPException

from .agent.compaction import compact_agent_state
from .agent.contracts import (
    AgentState,
    ErrorEvent,
    FinalEvent,
    FollowupEvent,
    ObservationEvent,
    ProgressEvent,
    StateEvent,
)
from .auth import AuthStore, refreshed_token_for_username
from .factory import build_registry
from .uploads import upload_store
from .config import settings
from .factory import build_runner
from .memory import LongTermMemory
from .memory.runtime import attach_memory_state
from .obs import traced_stream
from .quota import RateLimiter, TokenQuotaStore, begin_usage_ledger, collected_usage, estimate_tokens
from .security_context import tenant_scope
from .session_store import SessionStore
from .subscriptions import (
    CreateSubscriptionRuleRequest,
    SubscriptionSchedule,
    SubscriptionStore,
    UpdateSubscriptionRuleRequest,
    default_subscription_title,
)
from . import trajectory
from .tools.bangumi.client import BangumiClient
from .tools.moegirl.client import MoegirlClient

log = logging.getLogger("otomo.discord")
_PANEL_RE = re.compile(r"\[\[panel:[^\]]*\]\]")
_DISCORD_LIMIT = 1900


def _clean(answer: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _PANEL_RE.sub("", answer)).strip()


# ── Discord embed 卡片(复用证据面板同一份结构化 data)────────────────────
# 卡片构建器接收 discord 模块作参数(保持模块可在无 discord.py 环境导入)。
_EMBED_COLOR = 0x7AA2F7


def _first(v: object) -> str:
    return (v[0] if isinstance(v, list) and v else "") or ""


def _cover(item: dict) -> str | None:
    img = item.get("image") or item.get("cover")
    if isinstance(img, dict):
        img = img.get("large") or img.get("common") or img.get("grid")
    return img if isinstance(img, str) and img.startswith("http") else None


def _rec_embeds(discord, data: dict) -> list:
    out = []
    for it in (data.get("items") or [])[:5]:
        e = discord.Embed(
            title=str(it.get("name") or "?")[:256],
            url=f"https://bgm.tv/subject/{it.get('id')}" if it.get("id") else None,
            description=str(_first(it.get("fit_points")) or it.get("review_consensus") or "")[:400],
            color=_EMBED_COLOR,
        )
        if cover := _cover(it):
            e.set_thumbnail(url=cover)
        if it.get("bangumi_score"):
            e.add_field(name="Bangumi", value=str(it["bangumi_score"]), inline=True)
        if it.get("rank"):
            e.add_field(name="全站排名", value=f"#{it['rank']}", inline=True)
        if recall := _first(it.get("why_recalled")):
            e.add_field(name="为什么给你", value=recall[:200], inline=False)
        if risk := (_first(it.get("risks")) or _first(it.get("aspect_warnings"))):
            e.add_field(name="⚠️ 注意", value=risk[:200], inline=False)
        out.append(e)
    return out


def _review_embeds(discord, data: dict) -> list:
    e = discord.Embed(
        title=f"口碑速览 · {data.get('title') or '?'}"[:256],
        url=f"https://bgm.tv/subject/{data.get('subject_id')}" if data.get("subject_id") else None,
        description=str(data.get("consensus") or "")[:1000],
        color=_EMBED_COLOR,
    )
    for r in (data.get("ratings") or [])[:4]:
        if (score := r.get("score")) is not None:
            e.add_field(name=str(r.get("source") or "评分"), value=str(score), inline=True)
    conf = {"high": "样本充足", "medium": "样本一般", "low": "样本偏少，仅供参考"}.get(str(data.get("confidence")), "")
    if conf:
        e.set_footer(text=conf)
    return [e]


def _omikuji_embeds(discord, data: dict) -> list:
    advice = "\n".join(f"· {a}" for a in (data.get("advice") or [])[:3])
    e = discord.Embed(
        title=f"🎴 今日番签 · {data.get('fortune') or '?'}"[:256],
        description=f"今日之番:**{data.get('subject_name') or '?'}**\n{advice}"[:1000],
        color=_EMBED_COLOR,
    )
    if cover := _cover(data):
        e.set_thumbnail(url=cover)
    if data.get("lucky_tag"):
        e.add_field(name="幸运标签", value=str(data["lucky_tag"]), inline=True)
    return [e]


def _watch_embeds(discord, data: dict) -> list:
    lines = []
    for s in (data.get("official_sources") or [])[:6]:
        label, url = str(s.get("label") or "?"), str(s.get("url") or "")
        lines.append(f"[{label}]({url})" if url.startswith("http") else label)
    e = discord.Embed(
        title=f"在哪看 · {data.get('title') or '?'}"[:256],
        description=("\n".join(lines) or "暂无已验证的正版渠道")[:1000],
        color=_EMBED_COLOR,
    )
    return [e]


_HOT_BADGE = {"surge": "🔥🔥 爆热", "hot": "🔥 热播", "warm": "升温中", "none": ""}


def _season_embeds(discord, data: dict) -> list:
    items = (data.get("items") or [])[:6]
    if not items:
        return []
    e = discord.Embed(title="季番导视", color=_EMBED_COLOR)
    first_cover = None
    for it in items:
        hot = _HOT_BADGE.get(str(it.get("hotness_level") or "none"), "")
        bits = []
        if it.get("bangumi_score"):
            bits.append(f"⭐ {it['bangumi_score']}")
        if hot:
            bits.append(hot)
        if it.get("broadcast"):
            bits.append(str(it["broadcast"]))
        reason = str(it.get("reason") or "")[:120]
        sid = it.get("subject_id")
        title_link = f"[{it.get('title') or '?'}](https://bgm.tv/subject/{sid})" if sid else str(it.get("title") or "?")
        e.add_field(
            name=(" · ".join(bits) or "·")[:256],
            value=f"{title_link}\n{reason}"[:1024],
            inline=False,
        )
        first_cover = first_cover or _cover(it)
    if first_cover:
        e.set_thumbnail(url=first_cover)
    return [e]


def _movers_embeds(discord, data: dict) -> list:
    boards = [("📉 口碑下跌(崩)", data.get("down")), ("📈 口碑上涨", data.get("up")), ("🏁 近期完结", data.get("done"))]
    lines_all = []
    for label, board in boards:
        rows = (board or [])[:6]
        if not rows:
            continue
        lines = []
        for m in rows:
            delta = float(m.get("delta_score") or 0)
            sign = "+" if delta > 0 else ""
            lines.append(
                f"[{m.get('title') or '?'}](https://bgm.tv/subject/{m.get('subject_id')}) "
                f"`{sign}{delta}` (现 {m.get('current_score') or '?'})"
            )
        lines_all.append((label, "\n".join(lines)))
    if not lines_all:
        return []
    e = discord.Embed(title="口碑异动 · 近 30 天", color=_EMBED_COLOR)
    for label, value in lines_all:
        e.add_field(name=label, value=value[:1024], inline=False)
    e.set_footer(text="数据来自 netaba.re 快照(第三方)")
    return [e]


def _trend_embeds(discord, data: dict) -> list:
    e = discord.Embed(
        title=f"口碑走势 · {data.get('title') or '?'}"[:256],
        url=str(data.get("netabare_url") or "") or None,
        description=str(data.get("summary") or "")[:600],
        color=_EMBED_COLOR,
    )
    if data.get("current_score") is not None:
        e.add_field(name="当前均分", value=str(data["current_score"]), inline=True)
    for key, label in (("score_change_30d", "30 天"), ("score_change_90d", "90 天")):
        if data.get(key) is not None:
            v = float(data[key])
            e.add_field(name=label, value=f"{'+' if v > 0 else ''}{v}", inline=True)
    if data.get("controversy"):
        e.add_field(name="争议度", value=str(data["controversy"]), inline=True)
    e.set_footer(text="走势为 netaba.re 每日快照(第三方)")
    return [e]


def _buzz_embeds(discord, data: dict) -> list:
    hits = (data.get("hits") or [])[:8]
    e = discord.Embed(
        title="分集爆点雷达",
        description=f"扫描 {data.get('checked_subjects') or 0} 部在看番" + ("" if hits else " · 最近没有讨论量突增的集"),
        color=_EMBED_COLOR,
    )
    for h in hits:
        ratio = f" · {h['ratio']}× 平常" if h.get("ratio") else " · 开播即热"
        e.add_field(
            name=f"🔥 {h.get('subject_name') or '?'} 第 {h.get('sort')} 集"[:256],
            value=f"[{h.get('comments')} 条讨论{ratio}]({h.get('url') or 'https://bgm.tv'})"[:1024],
            inline=False,
        )
    return [e]


def _quiz_embeds(discord, data: dict) -> list:
    """答案用 Discord 剧透语法 ||…|| 藏起来,点击揭晓——聊天框里最自然的判分方式。"""
    qs = (data.get("questions") or [])[:8]
    if not qs:
        return []
    e = discord.Embed(title="🎯 ACGN 小测验", description="想好了再点开 ||答案|| 揭晓", color=_EMBED_COLOR)
    letters = "ABCD"
    for i, q in enumerate(qs, 1):
        opts = q.get("options") or []
        lines = [f"{letters[j]}. {opt}" for j, opt in enumerate(opts[:4])]
        ans_idx = int(q.get("answer_index") or 0)
        ans = f"||{letters[ans_idx]}. {opts[ans_idx] if ans_idx < len(opts) else '?'}"
        if q.get("explain"):
            ans += f" — {q['explain']}"
        ans += "||"
        e.add_field(name=f"{i}. {q.get('q') or '?'}"[:256], value=("\n".join(lines) + f"\n{ans}")[:1024], inline=False)
    return [e]


def _calendar_embeds(discord, data: dict) -> list:
    items = list((data.get("items") or [])[:12])
    if not items:
        for day in (data.get("days") or [])[:7]:
            for item in (day.get("items") or [])[:8]:
                copied = dict(item)
                copied.setdefault("day_label", day.get("label") or day.get("date") or "")
                items.append(copied)
                if len(items) >= 20:
                    break
            if len(items) >= 20:
                break
    if not items:
        return []
    e = discord.Embed(title=f"放送日历 · {data.get('today') or ''}"[:256], color=_EMBED_COLOR)
    lines = []
    for it in items:
        mark = "📌 " if it.get("mine") else ""
        broadcast = f" · {it['broadcast']}" if it.get("broadcast") else ""
        day = f"{it.get('day_label')} · " if it.get("day_label") else ""
        sid = it.get("subject_id") or it.get("id")
        name = it.get("name") or it.get("title") or "?"
        linked = f"[{name}](https://bgm.tv/subject/{sid})" if sid else str(name)
        lines.append(f"{mark}{day}{linked}{broadcast}")
    e.description = "\n".join(lines)[:3500]
    return [e]


def _ep_progress_embeds(discord, data: dict) -> list:
    eps = data.get("episodes") or []
    total = int(data.get("total_main") or len(eps) or 0)
    watched = int(data.get("watched") or 0)
    cells = "".join("🟩" if e.get("status") == "看过" else "🟥" if e.get("status") == "抛弃" else "⬜" for e in eps[:40])
    e = discord.Embed(
        title=f"追番进度 · {data.get('subject_name') or '?'}"[:256],
        description=f"看到第 **{data.get('watched_up_to') or 0}** 集 · {watched}/{total}\n{cells}",
        color=_EMBED_COLOR,
    )
    if data.get("next_episode") is not None:
        e.add_field(name="下一集", value=f"第 {data['next_episode']:g} 集", inline=True)
    return [e]


def _compare_embeds(discord, data: dict) -> list:
    cols = (data.get("columns") or data.get("subjects") or [])[:4]
    if not cols:
        return []
    e = discord.Embed(title="作品对比", color=_EMBED_COLOR)
    for c in cols:
        bits = []
        if c.get("score"):
            bits.append(f"⭐ {c['score']}")
        if c.get("rank"):
            bits.append(f"#{c['rank']}")
        if c.get("drop_rate") is not None:
            bits.append(f"弃番率 {c['drop_rate']}%")
        e.add_field(name=str(c.get("name_cn") or c.get("name") or "?")[:256], value=(" · ".join(bits) or "-")[:1024], inline=True)
    for h in (data.get("highlights") or [])[:3]:
        e.add_field(name="💡", value=str(h)[:1024], inline=False)
    return [e]


def _birthday_embeds(discord, data: dict) -> list:
    chars = (data.get("characters") or [])[:6]
    if not chars:
        return []
    e = discord.Embed(title=f"🎂 今日生日 · {data.get('date') or ''}"[:256], color=_EMBED_COLOR)
    for c in chars:
        e.add_field(
            name=str(c.get("name_native") or c.get("name") or "?")[:256],
            value=f"{c.get('from_media') or ''} · ♥ {c.get('favourites') or 0}"[:1024],
            inline=True,
        )
    if cover := _cover(chars[0]):
        e.set_thumbnail(url=cover)
    return [e]


def _pilgrimage_embeds(discord, data: dict) -> list:
    points = (data.get("points") or [])[:8]
    e = discord.Embed(
        title=f"⛩️ 圣地巡礼 · {data.get('title') or '?'}"[:256],
        description=f"{data.get('city') or '多地'} · 共 {data.get('count') or len(points)} 个取景点",
        color=_EMBED_COLOR,
        url=str(data.get("map_url") or "") or None,
    )
    for pt in points:
        ep = f"ep{pt['episode']} · " if pt.get("episode") is not None else ""
        e.add_field(
            name=str(pt.get("name") or "?")[:256],
            value=f"[{ep}地图]({pt.get('google_maps_url') or data.get('map_url') or 'https://anitabi.cn'})"[:1024],
            inline=True,
        )
    return [e]


def _taste_embeds(discord, data: dict) -> list:
    # friends_pulse 三榜优先;否则 pair 同步率
    pulse = data.get("pulse") or {}
    if pulse.get("watching_hot") or pulse.get("wishlist_hot"):
        e = discord.Embed(title=f"好友圈动态 · @{data.get('username') or ''}"[:256], color=_EMBED_COLOR)
        for key, label in (("watching_hot", "🔥 都在追"), ("wishlist_hot", "🌟 都想看"), ("top_rated", "🏆 圈内高分")):
            rows = (pulse.get(key) or [])[:5]
            if not rows:
                continue
            lines = [
                f"[{r.get('name') or '?'}](https://bgm.tv/subject/{r.get('subject_id')}) · {r.get('count')} 人"
                + (f" · 均分 {r.get('avg_rate')}" if r.get("avg_rate") else "")
                for r in rows
            ]
            e.add_field(name=label, value="\n".join(lines)[:1024], inline=False)
        return [e]
    aff = data.get("affinity") or {}
    if not aff and data.get("sync_score") is None:
        return []
    sync = data.get("sync_score") or aff.get("sync_score")
    level = data.get("sync_level") or aff.get("level")
    e = discord.Embed(
        title=f"口味同步率 · {data.get('username') or ''} × {data.get('peer_username') or ''}"[:256],
        description=(f"**{sync}** 分" + (f" · Lv{level}" if level else "")) if sync is not None else "",
        color=_EMBED_COLOR,
    )
    return [e]


def _watch_order_embeds(discord, data: dict) -> list:
    main = (data.get("watch_order") or [])[:12]
    side = (data.get("side_stories") or [])[:6]
    if not main and not side:
        return []
    e = discord.Embed(title=f"补番顺序 · {data.get('ip') or '系列'}"[:256], color=_EMBED_COLOR)
    if main:
        lines = []
        for index, item in enumerate(main, 1):
            sid = item.get("subject_id") or item.get("id")
            name = str(item.get("name") or item.get("title") or "?")
            label = f"[{name}](https://bgm.tv/subject/{sid})" if sid else name
            hint = item.get("duration_hint") or item.get("skip_advice") or ""
            lines.append(f"**{index}.** {label}" + (f" · {str(hint)[:80]}" if hint else ""))
        e.add_field(name="主线", value="\n".join(lines)[:1024], inline=False)
    if side:
        lines = []
        for item in side:
            name = item.get("name") or item.get("title") or "?"
            advice = item.get("skip_advice") or item.get("necessity") or "按兴趣补"
            lines.append(f"• **{name}** · {str(advice)[:90]}")
        e.add_field(name="旁支 / OVA / 番外", value="\n".join(lines)[:1024], inline=False)
    return [e]


def _airing_progress_embeds(discord, data: dict) -> list:
    items = (data.get("items") or [])[:12]
    if not items:
        return []
    e = discord.Embed(
        title=f"追番进度 · 落后 {data.get('behind_count') or 0} 部",
        color=_EMBED_COLOR,
    )
    lines = []
    for item in items:
        name = item.get("name") or item.get("title") or "?"
        current = item.get("watched") or item.get("ep_status") or item.get("progress") or 0
        total = item.get("total") or item.get("eps") or "?"
        action = item.get("action") or ""
        lines.append(f"• **{name}** · {current}/{total}" + (f" · {str(action)[:70]}" if action else ""))
    e.description = "\n".join(lines)[:3500]
    return [e]


def _copilot_embeds(discord, data: dict) -> list:
    groups = [
        ("今晚队列", data.get("queue")),
        ("继续追", data.get("continue_watching")),
        ("从想看开坑", data.get("start_from_wishlist")),
        ("捞回搁置", data.get("revive_on_hold")),
    ]
    e = discord.Embed(title=f"追番副驾 · @{data.get('username') or ''}"[:256], color=_EMBED_COLOR)
    for label, rows in groups:
        values = []
        for item in (rows or [])[:5]:
            sid = item.get("subject_id") or item.get("id")
            name = str(item.get("name") or item.get("title") or "?")
            linked = f"[{name}](https://bgm.tv/subject/{sid})" if sid else name
            why = _first(item.get("why")) or item.get("action") or ""
            values.append(f"• {linked}" + (f" · {str(why)[:70]}" if why else ""))
        if values:
            e.add_field(name=label, value="\n".join(values)[:1024], inline=False)
    return [e] if e.fields else []


def _memory_embeds(discord, data: dict) -> list:
    memory = data.get("memory") if isinstance(data.get("memory"), dict) else data
    likes = memory.get("likes") or []
    dislikes = memory.get("dislikes") or []
    progress = memory.get("progress") or {}
    e = discord.Embed(
        title=f"长期记忆 · @{memory.get('username') or ''}"[:256],
        description=f"默认剧透：`{memory.get('spoiler_default') or 'none'}`",
        color=_EMBED_COLOR,
    )
    for label, rows in (("喜欢", likes), ("避雷", dislikes)):
        values = [
            str(item.get("value") or item.get("label") or item) if isinstance(item, dict) else str(item)
            for item in rows[:10]
        ]
        if values:
            e.add_field(name=label, value="、".join(values)[:1024], inline=False)
    if isinstance(progress, dict) and progress:
        values = []
        for name, item in list(progress.items())[:8]:
            episode = item.get("episode") if isinstance(item, dict) else item
            values.append(f"{name}: 第 {episode} 集")
        e.add_field(name="观看进度", value="\n".join(values)[:1024], inline=False)
    return [e]


def _music_embeds(discord, data: dict) -> list:
    subject = data.get("subject") or {}
    title = subject.get("name") if isinstance(subject, dict) else subject
    rows = data.get("fused") or data.get("entries") or data.get("bangumi_music") or []
    if not rows:
        return []
    e = discord.Embed(title=f"OP / ED / 音乐 · {title or data.get('query') or '?'}"[:256], color=_EMBED_COLOR)
    for item in rows[:10]:
        kind = item.get("type") or item.get("kind") or item.get("group") or "music"
        name = item.get("title") or item.get("name") or "?"
        url = item.get("url") or item.get("video_url") or ""
        value = f"[{name}]({url})" if str(url).startswith("http") else str(name)
        e.add_field(name=str(kind)[:256], value=value[:1024], inline=False)
    return [e]


def _release_embeds(discord, data: dict) -> list:
    groups = data.get("groups") or []
    links = []
    for group in groups[:8]:
        for item in (group.get("latest_items") or [])[:2]:
            title = item.get("title") or item.get("name") or group.get("group_name") or "RSS"
            url = item.get("url") or item.get("link") or ""
            links.append(f"[{title}]({url})" if str(url).startswith("http") else str(title))
    if not links:
        for item in (data.get("search_links") or [])[:8]:
            title, url = item.get("label") or item.get("title") or "入口", item.get("url") or ""
            links.append(f"[{title}]({url})" if str(url).startswith("http") else str(title))
    if not links:
        return []
    return [
        discord.Embed(
            title=f"Release / RSS · {data.get('title') or '?'}"[:256],
            description="\n".join(links)[:3500],
            color=_EMBED_COLOR,
        )
    ]


def _visual_route_embeds(discord, data: dict) -> list:
    candidates = (data.get("candidates") or [])[:6]
    if not candidates:
        return []
    e = discord.Embed(
        title="图片识别候选",
        description=(
            f"路由：{data.get('decision') or '?'} · 置信度 {data.get('confidence') or '?'}"
            + (" · 需要你确认" if data.get("needs_user_confirmation") else "")
        ),
        color=_EMBED_COLOR,
    )
    for item in candidates:
        title = item.get("bangumi_name") or item.get("title") or "?"
        sid = item.get("bangumi_id") or item.get("subject_id")
        label = f"[{title}](https://bgm.tv/subject/{sid})" if sid else str(title)
        score = item.get("confidence") or item.get("similarity") or item.get("match")
        reason = item.get("match_note") or item.get("reason") or item.get("source") or ""
        e.add_field(
            name=(f"{label} · {score}" if score is not None else label)[:256],
            value=(str(reason) or "候选来源待回锚")[:1024],
            inline=False,
        )
    if cover := _cover(candidates[0]):
        e.set_thumbnail(url=cover)
    return [e]


def _video_content_embeds(discord, data: dict) -> list:
    rows = data.get("content_summary") or data.get("subtitle_summary") or data.get("metadata_summary") or []
    audience = data.get("audience_summary") or data.get("comment_summary") or []
    e = discord.Embed(
        title=f"B站内容摘要 · {data.get('title') or data.get('bvid') or '?'}"[:256],
        url=str(data.get("source_url") or "") or None,
        description="\n".join(f"• {row}" for row in rows[:6])[:2500] or "没有读到可用正文层。",
        color=_EMBED_COLOR,
    )
    if audience:
        e.add_field(name="观众讨论", value="\n".join(f"• {row}" for row in audience[:5])[:1024], inline=False)
    e.set_footer(text=f"读取层：{', '.join(data.get('read_layers') or []) or data.get('access_level') or 'metadata'}")
    return [e]


def _sections_embeds(discord, data: dict) -> list:
    """驾驶舱/档案/IP图谱/报告 这类分区型交付物 → 摘要卡(细节看网页)。"""
    sections = (data.get("sections") or [])[:6]
    if not sections:
        return []
    title = str(data.get("title") or data.get("subject", {}).get("name") or "报告")
    e = discord.Embed(title=title[:256], color=_EMBED_COLOR)
    for s in sections:
        rows = (s.get("items") or [])[:4]
        lines = []
        for r in rows:
            nm = r.get("name") or r.get("title") or r.get("subject_name") or "?"
            note = r.get("summary") or r.get("reason") or r.get("note") or ""
            lines.append(f"**{nm}**" + (f" — {str(note)[:60]}" if note else ""))
        e.add_field(name=str(s.get("title") or "·")[:256], value=("\n".join(lines) or "-")[:1024], inline=False)
    return [e]


def build_embeds(discord, name: str, data: dict | None) -> list:
    """按工具名把结构化结果做成 Discord embed;不认识/出错→[](走纯文本兜底)。"""
    if not data:
        return []
    try:
        return {
            "recommend_subjects": _rec_embeds,
            "review_subject": _review_embeds,
            "anime_omikuji": _omikuji_embeds,
            "where_to_watch": _watch_embeds,
            "season_guide_brief": _season_embeds,
            "get_rating_movers": _movers_embeds,
            "get_subject_trend": _trend_embeds,
            "scan_my_episode_buzz": _buzz_embeds,
            "generate_acgn_quiz": _quiz_embeds,
            "get_broadcast_calendar": _calendar_embeds,
            "get_my_episode_progress": _ep_progress_embeds,
            "compare_subjects": _compare_embeds,
            "get_character_birthdays": _birthday_embeds,
            "get_pilgrimage_map": _pilgrimage_embeds,
            "compare_user_taste": _taste_embeds,
            "get_airing_progress": _airing_progress_embeds,
            "plan_watch_order": _watch_order_embeds,
            "plan_watch_copilot": _copilot_embeds,
            "get_user_memory": _memory_embeds,
            "remember_user_preference": _memory_embeds,
            "forget_user_memory": _memory_embeds,
            "anime_music_themes": _music_embeds,
            "search_anime_themes": _music_embeds,
            "get_anime_release_feeds": _release_embeds,
            "route_image_source": _visual_route_embeds,
            "summarize_bilibili_video_content": _video_content_embeds,
            "watch_cockpit": _sections_embeds,
            "subject_dossier": _sections_embeds,
            "franchise_map": _sections_embeds,
            "monthly_watch_report": _sections_embeds,
            "build_weekly_digest": _sections_embeds,
            "build_taste_report": _sections_embeds,
            "build_collection_dashboard": _sections_embeds,
        }.get(name, lambda *_: [])(discord, data)
    except Exception:  # noqa: BLE001 - 卡片失败绝不能拖垮回复
        return []


def _source_embed(discord, sources: list[dict]) -> object | None:
    rows = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "")
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        title = str(source.get("title") or source.get("source") or "来源").replace("[", "").replace("]", "")
        rows.append(f"[{title[:80]}]({url}) · {source.get('source') or 'web'}")
        if len(rows) >= 8:
            break
    if not rows:
        return None
    return discord.Embed(title="来源", description="\n".join(rows)[:3500], color=0x565F89)


def _split(text: str, limit: int = _DISCORD_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks


def run() -> None:
    import discord
    from discord import app_commands
    # Slash callback annotations are postponed by ``from __future__ import
    # annotations`` and discord.py resolves them against module globals.
    globals()["discord"] = discord
    globals()["app_commands"] = app_commands

    token = settings.discord_bot_token
    if not token:
        raise SystemExit("需要 DISCORD_BOT_TOKEN 才能启动 Discord bot")

    auth = AuthStore()
    ltm = LongTermMemory()
    session_store = SessionStore()
    subscription_store = SubscriptionStore()
    rate_limiter = RateLimiter()
    quota_store = TokenQuotaStore()
    moegirl = MoegirlClient()
    _locks: dict[str, asyncio.Lock] = {}
    _identity_locks: dict[int, asyncio.Lock] = {}
    # guest 客户端**绝不**带部署者的 BANGUMI_TOKEN(否则未绑定用户的推荐会意外基于
    # 部署者自己的收藏画像——朋友指出的坑,Web 路径已避免,这里对齐:纯匿名 token="")。
    _guest_client = BangumiClient(token="", user_agent=settings.bangumi_user_agent)
    _guest_runner = build_runner(_guest_client, moegirl, "adaptive", ltm)
    async def _runner_for(discord_user_id: int):
        """绑定用户 → 用其 Bangumi token 的个人化 runner;否则 guest。"""
        username = auth.username_for_discord(str(discord_user_id))
        if not username:
            return _guest_runner, None, None
        tok = await refreshed_token_for_username(auth, username)
        if not tok or tok.status != "active":
            return _guest_runner, username, None
        user_client = BangumiClient(token=tok.access_token, user_agent=settings.bangumi_user_agent)
        return build_runner(user_client, moegirl, "adaptive", ltm), username, user_client

    def _conversation(discord_user_id: int, channel_id: int, guild_id: int | None) -> tuple[str, str]:
        raw = f"{guild_id or 'dm'}:{channel_id}:{discord_user_id}"
        session_id = "discord_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
        return session_id, f"discord:{discord_user_id}"

    async def _answer_locked(
        discord_user_id: int,
        channel_id: int,
        guild_id: int | None,
        question: str,
        attachments: list[dict] | None = None,
        progress_cb=None,
        spoiler_mode: str | None = None,
    ) -> tuple[str, list, list[dict], dict]:
        """Return text, embeds, pending writes, and Discord interaction state."""
        runner, username, owned_client = await _runner_for(discord_user_id)
        active_username = username if owned_client is not None else None
        quota_key = f"user:{active_username}" if active_username else f"discord:{discord_user_id}"
        try:
            rate_limiter.check(
                f"discord:minute:{discord_user_id}",
                limit=settings.rate_limit_chat_per_minute,
                window_seconds=60,
            )
            rate_limiter.check(
                f"discord:hour:{discord_user_id}",
                limit=settings.rate_limit_chat_per_hour,
                window_seconds=3600,
            )
            quota_store.check(quota_key)
        except HTTPException as exc:
            if owned_client is not None:
                await owned_client.aclose()
            return str(exc.detail), [], [], {}
        session_id, owner = _conversation(discord_user_id, channel_id, guild_id)
        lock = _locks.setdefault(session_id, asyncio.Lock())
        result = ""
        observations: list[tuple[str, dict]] = []
        tools_called: list[str] = []
        pending_actions: list[dict] = []
        followups: list[str] = []
        spoiler_snapshot: dict = {}
        final_sources: list[dict] = []
        state = AgentState()
        turn_id = ""
        begin_usage_ledger()
        try:
            async with lock:
                session_store.ensure_session(session_id, owner, title="Discord 对话")
                state = session_store.load_state(session_id, owner) or AgentState()
                identity_marker = active_username or "__guest__"
                previous_identity = state.short_term.get("discord_identity")
                if previous_identity is not None and previous_identity != identity_marker:
                    # A Discord account can be unlinked/rebound. Never carry the
                    # previous Bangumi identity's transcript into the new one.
                    state = AgentState()
                state.short_term["discord_identity"] = identity_marker
                if spoiler_mode in {"none", "mild", "full"}:
                    spoiler = dict(state.short_term.get("spoiler") or {})
                    spoiler["mode"] = spoiler_mode
                    spoiler["pending_followup"] = False
                    state.short_term["spoiler"] = spoiler
                if active_username:
                    with tenant_scope(active_username, authenticated=True):
                        await attach_memory_state(
                            state,
                            owned_client,
                            ltm,
                            username=active_username,
                        )
                else:
                    state.short_term.pop("memory", None)
                if attachments:  # 与 Web /chat 同构:识图工具从 short_term 读 upload:// 附件
                    state.short_term["attachments"] = attachments[:4]
                turn_id = hashlib.sha256(
                    f"{session_id}:{question}:{len(state.messages)}".encode()
                ).hexdigest()[:32]
                try:
                    with tenant_scope(active_username, authenticated=bool(active_username)):
                        async for ev in traced_stream(
                            runner,
                            question,
                            state,
                            {
                                "session_id": session_id,
                                "runner": "adaptive",
                                "turn_id": turn_id,
                                "surface": "discord",
                            },
                        ):
                            if isinstance(ev, FinalEvent):
                                result = ev.answer
                                final_sources = [
                                    source.model_dump(mode="json", exclude_none=True)
                                    for source in ev.sources
                                ]
                            elif isinstance(ev, FollowupEvent):
                                followups = [str(x) for x in ev.questions[:3]]
                            elif isinstance(ev, StateEvent):
                                if ev.scope == "memory":
                                    state.short_term["memory"] = dict(ev.snapshot or {})
                                elif ev.scope == "spoiler":
                                    spoiler_snapshot = dict(ev.snapshot or {})
                            elif isinstance(ev, ProgressEvent) and progress_cb:
                                try:
                                    await progress_cb(ev.summary)
                                except Exception:  # noqa: BLE001 - 进度提示失败不影响回答
                                    pass
                            elif isinstance(ev, ObservationEvent):
                                tools_called.append(ev.name)
                                if ev.data:
                                    observations.append((ev.name, ev.data))
                                    if ev.name == "prepare_bangumi_write_action" and ev.ok:
                                        action = (ev.data or {}).get("action") or {}
                                        if action.get("id") and active_username:
                                            pending_actions.append({
                                                "id": str(action["id"]),
                                                "summary": str(action.get("summary") or "写回动作"),
                                                "username": active_username,
                                            })
                            elif isinstance(ev, ErrorEvent):
                                result = result or f"⚠️ 出错了:{ev.message[:200]}"
                finally:
                    state.short_term.pop("attachments", None)
                    if result:
                        try:
                            await compact_agent_state(
                                state,
                                getattr(runner, "llm", None),
                                getattr(runner, "model", None),
                            )
                        except Exception:  # noqa: BLE001 - keep the completed turn
                            log.exception("discord conversation compaction failed")
                    session_store.save_state(session_id, owner, state)
        except Exception as e:  # noqa: BLE001
            log.exception("discord answer failed")
            return f"抱歉,处理时出错了({type(e).__name__}),换个问法再试试?", [], [], {}
        finally:
            usage = collected_usage() or estimate_tokens(question, result)
            try:
                quota_store.record(quota_key, usage)
            except Exception:  # noqa: BLE001 - accounting must not hide the reply
                pass
            trajectory.log_turn(
                turn_id=turn_id,
                session_id=session_id,
                owner=owner,
                runner="adaptive",
                user_message=question,
                final_answer=result,
                messages=state.messages,
                tools_called=tools_called,
                usage_tokens=usage,
            )
            if owned_client is not None:
                await owned_client.aclose()
        embeds: list = []
        for nm, dat in observations:
            embeds.extend(build_embeds(discord, nm, dat))
            if len(embeds) >= 9:  # reserve one slot for the source card
                break
        if source_card := _source_embed(discord, final_sources):
            embeds = embeds[:9] + [source_card]
        critique: list[str] = []
        for name, data in observations:
            if name == "recommend_subjects":
                critique.extend(str(x) for x in (data.get("critique_chips") or [])[:3])
        interaction_state = {
            "followups": list(dict.fromkeys([*critique, *followups]))[:3],
            "spoiler": spoiler_snapshot,
            "question": question,
        }
        return (
            _clean(result) or "(这次没能整理出回答,换个问法试试?)",
            embeds[:10],
            pending_actions,
            interaction_state,
        )

    async def _answer(
        discord_user_id: int,
        channel_id: int,
        guild_id: int | None,
        question: str,
        attachments: list[dict] | None = None,
        progress_cb=None,
        spoiler_mode: str | None = None,
    ) -> tuple[str, list, list[dict], dict]:
        # Identity changes (especially unlink/rebind) are serialized with every
        # conversation write so an old account cannot recreate deleted state.
        lock = _identity_locks.setdefault(discord_user_id, asyncio.Lock())
        async with lock:
            return await _answer_locked(
                discord_user_id,
                channel_id,
                guild_id,
                question,
                attachments,
                progress_cb,
                spoiler_mode,
            )

    async def _execute_write(username: str, action_id: str, kind: str) -> str:
        """按钮回调:确认/取消写回。按 username 取 token 建 registry,tenant_scope 内执行。"""
        tok = await refreshed_token_for_username(auth, username)
        if not tok or tok.status != "active":
            return "⚠️ Bangumi 授权已失效,请 `/解绑` 后重新 `/绑定`。"
        client_ = BangumiClient(token=tok.access_token, user_agent=settings.bangumi_user_agent)
        try:
            registry = build_registry(client_, moegirl, ltm)
            tool = "execute_bangumi_write_action" if kind == "confirm" else "cancel_bangumi_write_action"
            payload = {"action_id": action_id, "confirmed": True} if kind == "confirm" else {"action_id": action_id}
            with tenant_scope(username, authenticated=True):
                result = await registry.dispatch(tool, json.dumps(payload, ensure_ascii=False), allow_write=True)
            if result.ok:
                return "✅ 已写回 Bangumi(可说'撤销'回滚)。" if kind == "confirm" else "已取消,未写入。"
            return f"⚠️ 执行失败:{(result.error or '未知错误')[:180]}"
        except Exception as e:  # noqa: BLE001
            log.exception("discord write action failed")
            return f"⚠️ 执行出错:{type(e).__name__}"
        finally:
            await client_.aclose()

    async def _fetch_discord_images(message) -> list[dict]:
        """下载 Discord 图片附件 → UploadStore(与 Web 上传同构,识图工具零改动)。"""
        out: list[dict] = []
        for att in (message.attachments or [])[:4]:
            ctype = str(att.content_type or "")
            if not ctype.startswith("image/") or att.size > settings.upload_max_image_bytes:
                continue
            try:
                data = await att.read()
                import base64 as _b64
                data_url = f"data:{ctype.split(';')[0]};base64,{_b64.b64encode(data).decode('ascii')}"
                saved = upload_store.save_data_url(data_url, filename=att.filename or "")
                out.append({
                    "uri": saved.uri,
                    "filename": saved.filename,
                    "mime_type": saved.mime_type,
                    "size": saved.size,
                })
            except Exception:  # noqa: BLE001 - 单张失败跳过
                log.exception("discord attachment save failed")
        return out

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def _link_url(discord_user_id: int) -> str:
        # 短码方案:URL 只带 8 位 hex 码,无特殊字符,经得起 Discord/浏览器/Caddy 任何折腾
        code = auth.create_discord_link_code(str(discord_user_id))
        base = settings.frontend_base_url.rstrip("/")
        return f"{base}/auth/bangumi/start?discord_code={code}"

    @client.event
    async def on_ready() -> None:
        try:
            # 按服务器同步=命令即时生效(全局同步要等最长 1 小时才在客户端出现)
            for guild in client.guilds:
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
            await tree.sync()  # 也做全局(私信里的斜杠命令用,传播较慢)
            log.info("slash 命令已同步到 %d 个服务器", len(client.guilds))
            print(f"slash 命令已同步到 {len(client.guilds)} 个服务器")
        except Exception:  # noqa: BLE001
            log.exception("slash command sync failed")
        log.info("Otomo Discord bot 上线:%s", client.user)
        print(f"Otomo Discord bot 已上线:{client.user}")

    class WriteConfirmView(discord.ui.View):
        """写回确认按钮(比口头确认更 Discord 原生)。只有发起者能点,3 分钟超时。"""

        def __init__(self, requester_id: int, username: str, action_id: str) -> None:
            super().__init__(timeout=180)
            self.requester_id = requester_id
            self.username = username
            self.action_id = action_id

        async def interaction_check(self, interaction: "discord.Interaction") -> bool:
            if interaction.user.id != self.requester_id:
                await interaction.response.send_message("这个确认按钮只有提问的人能点哦。", ephemeral=True)
                return False
            return True

        async def _finish(self, interaction: "discord.Interaction", note: str) -> None:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=(interaction.message.content or "") + f"\n{note}", view=self,
            )
            self.stop()

        @discord.ui.button(label="✅ 确认写回", style=discord.ButtonStyle.success)
        async def confirm(self, interaction: "discord.Interaction", _button) -> None:
            note = await _execute_write(self.username, self.action_id, "confirm")
            await self._finish(interaction, note)

        @discord.ui.button(label="✖ 取消", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: "discord.Interaction", _button) -> None:
            note = await _execute_write(self.username, self.action_id, "cancel")
            await self._finish(interaction, note)

    class ContinuationButton(discord.ui.Button):
        def __init__(self, label: str, question: str, *, spoiler_mode: str | None = None) -> None:
            style = discord.ButtonStyle.danger if spoiler_mode == "full" else discord.ButtonStyle.secondary
            super().__init__(label=label[:80], style=style)
            self.question = question
            self.spoiler_mode = spoiler_mode

        async def callback(self, interaction: "discord.Interaction") -> None:
            view = self.view
            if not isinstance(view, ContinuationView):
                return
            await interaction.response.defer(thinking=True)
            reply, embeds, pending, interaction_state = await _answer(
                interaction.user.id,
                int(interaction.channel_id or interaction.user.id),
                interaction.guild_id,
                self.question,
                spoiler_mode=self.spoiler_mode,
            )
            await _deliver_interaction(interaction, reply, embeds, pending, interaction_state)

    class ContinuationView(discord.ui.View):
        """Native follow-up/critique/spoiler controls; only the requester may use them."""

        def __init__(self, requester_id: int, state: dict) -> None:
            super().__init__(timeout=300)
            self.requester_id = requester_id
            spoiler = state.get("spoiler") or {}
            question = str(state.get("question") or "")
            if spoiler.get("pending_followup") and question:
                self.add_item(ContinuationButton("无剧透", question, spoiler_mode="none"))
                self.add_item(ContinuationButton("轻微剧透", question, spoiler_mode="mild"))
                self.add_item(ContinuationButton("完整剧透", question, spoiler_mode="full"))
            else:
                for followup in (state.get("followups") or [])[:3]:
                    q = str(followup).strip()
                    if q:
                        self.add_item(ContinuationButton(q, q))

        async def interaction_check(self, interaction: "discord.Interaction") -> bool:
            if interaction.user.id != self.requester_id:
                await interaction.response.send_message("这些操作只属于原提问者。", ephemeral=True)
                return False
            return True

    def _continuation_view(requester_id: int, state: dict) -> ContinuationView | None:
        view = ContinuationView(requester_id, state)
        return view if view.children else None

    async def _deliver_interaction(
        interaction: "discord.Interaction",
        reply: str,
        embeds: list,
        pending: list[dict],
        interaction_state: dict,
    ) -> None:
        parts = _split(reply) or ["(没有生成回答)"]
        view = _continuation_view(interaction.user.id, interaction_state)
        for i, chunk in enumerate(parts):
            last = i == len(parts) - 1
            await interaction.followup.send(
                chunk,
                embeds=embeds if embeds and last else [],
                view=view if last else None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        for action in pending[:3]:
            await interaction.followup.send(
                f"📝 待确认:{action['summary']}",
                view=WriteConfirmView(interaction.user.id, action["username"], action["id"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @client.event
    async def on_message(message: "discord.Message") -> None:
        if message.author.bot:
            return
        is_dm = message.guild is None
        mentioned = client.user in message.mentions
        if not (is_dm or mentioned or settings.discord_reply_all):
            return
        content = message.content
        if mentioned:
            content = re.sub(rf"<@!?{client.user.id}>", "", content).strip()
        attachments = await _fetch_discord_images(message)
        if not content and not attachments:
            await message.channel.send(
                "在的~ 直接问我番剧推荐 / 评价 / 在哪看 / 梗出处都行,发图能识番,或用 `/绑定` 关联你的 Bangumi 账号。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if attachments and not content:
            content = "这张图出自哪部作品?帮我识别一下。"

        # 进度状态消息:发一条"思考中"随 ProgressEvent 节流编辑(网页"看它思考"的 Discord 版)
        status_msg = await message.channel.send("🤔 正在思考…")
        last_edit = 0.0

        async def progress_cb(summary: str) -> None:
            nonlocal last_edit
            now = asyncio.get_running_loop().time()
            if now - last_edit < 2.5:  # 节流:Discord 编辑有速率限制
                return
            last_edit = now
            await status_msg.edit(content=f"🤔 {str(summary)[:150]}")

        async with message.channel.typing():
            reply, embeds, pending, interaction_state = await _answer(
                message.author.id,
                message.channel.id,
                message.guild.id if message.guild else None,
                content,
                attachments=attachments,
                progress_cb=progress_cb,
            )
        try:
            await status_msg.delete()
        except Exception:  # noqa: BLE001
            pass
        parts = _split(reply)
        continuation = _continuation_view(message.author.id, interaction_state)
        for i, chunk in enumerate(parts):
            # embed 附在最后一段文本上(Discord 单条消息可带 content + 最多10个embed)
            if embeds and i == len(parts) - 1:
                await message.channel.send(
                    chunk,
                    embeds=embeds,
                    view=continuation,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.channel.send(
                    chunk,
                    view=continuation if i == len(parts) - 1 else None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        if embeds and not parts:
            await message.channel.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
        for act in pending[:3]:  # 写回确认按钮(每个待确认动作一条)
            await message.channel.send(
                f"📝 待确认:{act['summary']}",
                view=WriteConfirmView(message.author.id, act["username"], act["id"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _slash_answer(interaction: "discord.Interaction", question: str) -> None:
        await interaction.response.defer(thinking=True)
        reply, embeds, pending, interaction_state = await _answer(
            interaction.user.id,
            int(interaction.channel_id or interaction.user.id),
            interaction.guild_id,
            question,
        )
        await _deliver_interaction(interaction, reply, embeds, pending, interaction_state)

    @tree.command(name="新对话", description="清空当前频道的对话上下文,重新开始")
    async def new_chat(interaction: "discord.Interaction") -> None:
        session_id, owner = _conversation(
            interaction.user.id, int(interaction.channel_id or interaction.user.id), interaction.guild_id
        )
        identity_lock = _identity_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with identity_lock:
            session_lock = _locks.setdefault(session_id, asyncio.Lock())
            async with session_lock:
                try:
                    session_store.delete_session(session_id, owner)
                except Exception:  # noqa: BLE001
                    pass
            if not session_lock.locked():
                _locks.pop(session_id, None)
        await interaction.response.send_message("✨ 已开新对话,之前的上下文清空了。", ephemeral=True)

    @tree.command(name="推荐", description="按你的口味推荐番剧(绑定后更懂你)")
    @app_commands.describe(关键词="想要的题材/心情,如 治愈 / 今晚看完 / 类似孤独摇滚")
    async def rec(interaction: "discord.Interaction", 关键词: str = "") -> None:
        await _slash_answer(interaction, f"推荐几部{('：' + 关键词) if 关键词 else '我可能喜欢的番'}")

    @tree.command(name="评价", description="查一部作品的口碑评价")
    @app_commands.describe(作品="作品名")
    async def review(interaction: "discord.Interaction", 作品: str) -> None:
        await _slash_answer(interaction, f"《{作品}》口碑怎么样,值得看吗?")

    @tree.command(name="在哪看", description="查作品的正版观看/购买渠道")
    @app_commands.describe(作品="作品名")
    async def where(interaction: "discord.Interaction", 作品: str) -> None:
        await _slash_answer(interaction, f"《{作品}》在哪能看?给正版渠道")

    @tree.command(name="新番", description="查看指定季度的新番导视")
    @app_commands.describe(年份="四位年份", 月份="季度首月：1/4/7/10", 模式="按口味导视或当前热播")
    @app_commands.choices(
        月份=[
            app_commands.Choice(name="1 月番", value=1),
            app_commands.Choice(name="4 月番", value=4),
            app_commands.Choice(name="7 月番", value=7),
            app_commands.Choice(name="10 月番", value=10),
        ],
        模式=[
            app_commands.Choice(name="按我口味", value="guide"),
            app_commands.Choice(name="当前热播", value="hot"),
        ]
    )
    async def season(
        interaction: "discord.Interaction",
        年份: app_commands.Range[int, 2000, 2100],
        月份: app_commands.Choice[int],
        模式: app_commands.Choice[str],
    ) -> None:
        intent = "按我的口味推荐" if 模式.value == "guide" else "按当前热度和口碑分诊"
        await _slash_answer(interaction, f"给我 {int(年份)} 年 {月份.value} 月新番导视，{intent}，附合适的B站导视入口")

    @tree.command(name="日历", description="查看今天或本周的放送与个人追番进度")
    @app_commands.describe(范围="今天或本周")
    @app_commands.choices(
        范围=[
            app_commands.Choice(name="今天", value="today"),
            app_commands.Choice(name="本周", value="week"),
        ]
    )
    async def calendar(interaction: "discord.Interaction", 范围: app_commands.Choice[str]) -> None:
        await _slash_answer(interaction, "今天我在追的番更新什么？" if 范围.value == "today" else "给我本周追番放送日历")

    @tree.command(name="记忆", description="查看 Otomo 对你的长期偏好、进度和反馈记忆")
    async def memory(interaction: "discord.Interaction") -> None:
        await _slash_answer(interaction, "你现在长期记住了我的哪些偏好、避雷、观看进度和推荐反馈？")

    @tree.command(name="剧透", description="设置当前频道的剧透模式，可选写成跨端默认")
    @app_commands.describe(模式="当前频道立即生效", 记住为默认="同步到网页和其他新会话")
    @app_commands.choices(
        模式=[
            app_commands.Choice(name="无剧透", value="none"),
            app_commands.Choice(name="轻微剧透", value="mild"),
            app_commands.Choice(name="完整剧透", value="full"),
        ]
    )
    async def spoiler(
        interaction: "discord.Interaction",
        模式: app_commands.Choice[str],
        记住为默认: bool = False,
    ) -> None:
        session_id, owner = _conversation(
            interaction.user.id,
            int(interaction.channel_id or interaction.user.id),
            interaction.guild_id,
        )
        identity_lock = _identity_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with identity_lock:
            lock = _locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                session_store.ensure_session(session_id, owner, title="Discord 对话")
                state = session_store.load_state(session_id, owner) or AgentState()
                current = dict(state.short_term.get("spoiler") or {})
                current.update({"mode": 模式.value, "pending_followup": False})
                state.short_term["spoiler"] = current
                session_store.save_state(session_id, owner, state)
        note = "当前频道会话已生效"
        if 记住为默认:
            username = auth.username_for_discord(str(interaction.user.id))
            token_state = await refreshed_token_for_username(auth, username) if username else None
            if not username or not token_state or token_state.status != "active":
                await interaction.response.send_message(
                    f"{note}；但跨端默认需要先 `/绑定` Bangumi。",
                    ephemeral=True,
                )
                return
            with tenant_scope(username, authenticated=True):
                mem = ltm.load_user(username)
                mem.spoiler_default = 模式.value
                ltm.save_user(mem)
            note += "，并已写入跨端长期默认"
        labels = {"none": "无剧透", "mild": "轻微剧透", "full": "完整剧透"}
        await interaction.response.send_message(f"{note}：**{labels[模式.value]}**。", ephemeral=True)

    _SUBSCRIPTION_KIND_LABELS = {
        "daily_airing": "每日追番",
        "weekly_digest": "每周周报",
        "monthly_report": "每月报告",
        "birthday": "角色生日",
        "episode_buzz": "分集爆点",
        "friends_activity": "好友动态",
    }

    @tree.command(name="订阅", description="直接管理发送到 Discord 私信的 Otomo 主动订阅")
    @app_commands.describe(操作="查看、开启或停用", 类型="订阅内容")
    @app_commands.choices(
        操作=[
            app_commands.Choice(name="查看", value="list"),
            app_commands.Choice(name="开启", value="enable"),
            app_commands.Choice(name="停用", value="disable"),
        ],
        类型=[
            app_commands.Choice(name=label, value=kind)
            for kind, label in _SUBSCRIPTION_KIND_LABELS.items()
        ],
    )
    async def subscriptions(
        interaction: "discord.Interaction",
        操作: app_commands.Choice[str],
        类型: app_commands.Choice[str] | None = None,
    ) -> None:
        username = auth.username_for_discord(str(interaction.user.id))
        token_state = await refreshed_token_for_username(auth, username) if username else None
        if not username or not token_state or token_state.status != "active":
            await interaction.response.send_message("主动订阅需要先用 `/绑定` 关联 Bangumi。", ephemeral=True)
            return
        owner = f"user:{username}"
        rules = subscription_store.list_rules(owner)
        if 操作.value == "list":
            if not rules:
                await interaction.response.send_message("目前没有订阅规则。用 `/订阅 开启` 创建 Discord 私信提醒。", ephemeral=True)
                return
            lines = [
                f"{'✅' if rule.enabled else '⏸️'} **{rule.title}** · {rule.schedule.hour:02d}:{rule.schedule.minute:02d}"
                f" · {','.join(rule.channels)}"
                for rule in rules[:20]
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return
        if 类型 is None:
            await interaction.response.send_message("开启或停用时请选择订阅类型。", ephemeral=True)
            return
        kind = 类型.value
        matched = [rule for rule in rules if rule.kind == kind]
        if 操作.value == "disable":
            for rule in matched:
                subscription_store.update(rule.id, owner, UpdateSubscriptionRuleRequest(enabled=False))
            await interaction.response.send_message(
                f"已停用 {_SUBSCRIPTION_KIND_LABELS[kind]}（{len(matched)} 条）。",
                ephemeral=True,
            )
            return
        if matched:
            rule = matched[0]
            channels = list(dict.fromkeys([*rule.channels, "discord_dm"]))
            subscription_store.update(
                rule.id,
                owner,
                UpdateSubscriptionRuleRequest(enabled=True, channels=channels),
            )
            action = "已重新启用"
        else:
            schedule = SubscriptionSchedule(hour=9, minute=0)
            if kind == "weekly_digest":
                schedule.weekday = 0
            elif kind == "monthly_report":
                schedule.day_of_month = 1
            subscription_store.create(
                CreateSubscriptionRuleRequest(
                    kind=kind,
                    title=default_subscription_title(kind),
                    schedule=schedule,
                    channels=["discord_dm"],
                ),
                owner_key=owner,
                username=username,
            )
            action = "已创建"
        await interaction.response.send_message(
            f"{action} **{_SUBSCRIPTION_KIND_LABELS[kind]}** Discord 私信订阅。"
            "主动推送要求服务器上的 subscription worker 常驻运行；完整时间和筛选条件可在网页订阅中心调整。",
            ephemeral=True,
        )

    @tree.command(name="绑定", description="关联你的 Bangumi 账号,解锁个人化推荐")
    async def link(interaction: "discord.Interaction") -> None:
        current = auth.username_for_discord(str(interaction.user.id))
        if current:
            await interaction.response.send_message(
                f"你已绑定 Bangumi 账号 **{current}**。要换号先 `/解绑`。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"点这里用你的 Bangumi 账号完成绑定(仅你可见):\n{_link_url(interaction.user.id)}\n"
            "绑定后我推荐/查进度就会用你自己的收藏画像。链接 15 分钟内有效。",
            ephemeral=True,
        )

    @tree.command(name="解绑", description="解除 Bangumi 账号关联")
    async def unlink(interaction: "discord.Interaction") -> None:
        identity_lock = _identity_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with identity_lock:
            auth.unlink_discord(str(interaction.user.id))
            cleared = session_store.delete_owner_sessions(f"discord:{interaction.user.id}")
        await interaction.response.send_message(
            f"已解绑并清理 {cleared} 个 Discord 短期会话。长期记忆仍保留在原 Bangumi 账号下；"
            "之后回到 guest 模式，`/绑定` 可重新关联。",
            ephemeral=True,
        )

    _HELP = (
        "**Otomo · 番组搭子** —— ACGN 知识图谱 agent 🎴\n\n"
        "**怎么用:**\n"
        "• 在频道里 **@我** 提问,或**私信我**(私信不用 @)\n"
        "• **发图给我**能识番(截图/CG/封面都行)\n"
        "• 斜杠命令:`/推荐` `/评价` `/在哪看` `/新番` `/日历` `/记忆` `/剧透` `/订阅` `/新对话`\n\n"
        "**能问什么(举例):**\n"
        "• 推荐:`推荐几部治愈番` / `类似孤独摇滚的` / `今晚能看完的短番`\n"
        "• 评价:`药屋少女的呢喃口碑怎么样` / `最近什么番崩了`\n"
        "• 追番:`这季什么番最火` / `药屋在哪能看` / `我看完孤独摇滚第8集了`(带确认按钮写回)\n"
        "• 考据:`白色相簿2 冬马的声优还配过谁` / `这是什么梗`\n"
        "• 玩:`抽个今日番签` / `考考我`(答案点开剧透条揭晓)\n\n"
        "**个人化:** `/绑定` 关联你的 Bangumi 账号后,推荐用你自己的收藏画像,进度打卡/写回也解锁。\n"
        "`/订阅` 可把追番/周报等主动推到 Discord 私信；`/我是谁` 看绑定状态，"
        "`/解绑` 解除并清理 Discord 短期会话，`/新对话` 只清空当前频道上下文。"
    )

    @tree.command(name="帮助", description="Otomo 用法说明")
    async def help_zh(interaction: "discord.Interaction") -> None:
        await interaction.response.send_message(_HELP, ephemeral=True)

    @tree.command(name="help", description="How to use Otomo")
    async def help_en(interaction: "discord.Interaction") -> None:
        await interaction.response.send_message(_HELP, ephemeral=True)

    @tree.command(name="我是谁", description="查看你的 Bangumi 绑定状态(排障用)")
    async def whoami(interaction: "discord.Interaction") -> None:
        uid = str(interaction.user.id)
        username = auth.username_for_discord(uid)
        if not username:
            await interaction.response.send_message(
                f"Discord ID `{uid}`：**未绑定**。用 `/绑定` 关联 Bangumi 账号。", ephemeral=True)
            return
        tok = await refreshed_token_for_username(auth, username)
        status = tok.status if tok else "找不到 token"
        await interaction.response.send_message(
            f"Discord ID `{uid}`\n绑定账号:**{username}**\nToken 状态:`{status}`"
            + ("\n✅ 个人化已生效" if tok and tok.status == "active" else "\n⚠️ token 异常,`/解绑` 后重新 `/绑定`"),
            ephemeral=True,
        )

    client.run(token, log_handler=None)


if __name__ == "__main__":
    run()
