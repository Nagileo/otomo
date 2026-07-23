"""Discord bot 入口:把 Otomo agent 接到 Discord(python -m otomo.discord_bot)。

Otomo 本质是个 ACGN agent,Discord 是它最自然的形态之一——在服务器里 @机器人、
私信、或用斜杠命令,就能问番/推荐/评价/查资源/识梗,复用全部工具。

v2:
- 触发:私信直接答;服务器里被 @ 才答;斜杠命令 /推荐 /评价 /在哪看 /绑定 /解绑。
- 多轮:每个 Discord 用户一个持久会话(AgentState,软重置防膨胀)。
- **账号绑定**:/绑定 → 一条链接 → 用自己的 Bangumi 登录 → 之后个人化(用你的收藏
  画像推荐、查你的追番进度)。未绑定=guest 模式(公开知识问答照常)。
- 输出:去 [[panel:x]] 锚点 + 按 2000 字上限分段。

依赖:pip install -e ".[discord]"(discord.py)。需 DISCORD_BOT_TOKEN,且在开发者后台
开启 MESSAGE CONTENT INTENT。绑定复用 AUTH_ENCRYPTION_KEY(bot 与 backend 共享)。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from .agent.contracts import AgentState, ErrorEvent, FinalEvent, ObservationEvent
from .auth import AuthStore, refreshed_token_for_username
from .config import settings
from .factory import build_runner
from .memory import LongTermMemory
from .obs import traced_stream
from .quota import begin_usage_ledger, collected_usage, estimate_tokens
from .security_context import tenant_scope
from .session_store import SessionStore
from . import trajectory
from .tools.bangumi.client import BangumiClient
from .tools.moegirl.client import MoegirlClient

log = logging.getLogger("otomo.discord")
_PANEL_RE = re.compile(r"\[\[panel:[^\]]*\]\]")
_DISCORD_LIMIT = 1900
_MAX_HISTORY = 24


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
        }.get(name, lambda *_: [])(discord, data)
    except Exception:  # noqa: BLE001 - 卡片失败绝不能拖垮回复
        return []


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
    return chunks[:6]


def run() -> None:
    import discord
    from discord import app_commands

    token = settings.discord_bot_token
    if not token:
        raise SystemExit("需要 DISCORD_BOT_TOKEN 才能启动 Discord bot")

    auth = AuthStore()
    ltm = LongTermMemory()
    session_store = SessionStore()
    moegirl = MoegirlClient()
    _locks: dict[str, asyncio.Lock] = {}
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

    async def _answer(
        discord_user_id: int,
        channel_id: int,
        guild_id: int | None,
        question: str,
    ) -> tuple[str, list]:
        """返回 (清洗后的文本回答, embed 卡片列表)。"""
        runner, username, owned_client = await _runner_for(discord_user_id)
        session_id, owner = _conversation(discord_user_id, channel_id, guild_id)
        lock = _locks.setdefault(session_id, asyncio.Lock())
        result = ""
        observations: list[tuple[str, dict]] = []
        tools_called: list[str] = []
        state = AgentState()
        turn_id = ""
        begin_usage_ledger()
        try:
            async with lock:
                session_store.ensure_session(session_id, owner, title="Discord 对话")
                state = session_store.load_state(session_id, owner) or AgentState()
                turn_id = hashlib.sha256(
                    f"{session_id}:{question}:{len(state.messages)}".encode()
                ).hexdigest()[:32]
                try:
                    with tenant_scope(username, authenticated=bool(username and owned_client)):
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
                            elif isinstance(ev, ObservationEvent):
                                tools_called.append(ev.name)
                                if ev.data:
                                    observations.append((ev.name, ev.data))
                            elif isinstance(ev, ErrorEvent):
                                result = result or f"⚠️ 出错了:{ev.message[:200]}"
                finally:
                    if len(state.messages) > _MAX_HISTORY:
                        state.messages = state.messages[:1] + state.messages[-(_MAX_HISTORY - 1):]
                    session_store.save_state(session_id, owner, state)
        except Exception as e:  # noqa: BLE001
            log.exception("discord answer failed")
            return f"抱歉,处理时出错了({type(e).__name__}),换个问法再试试?", []
        finally:
            usage = collected_usage() or estimate_tokens(question, result)
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
            if len(embeds) >= 10:  # Discord 单条消息最多 10 个 embed
                break
        return _clean(result) or "(这次没能整理出回答,换个问法试试?)", embeds[:10]

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
        if not content:
            await message.channel.send(
                "在的~ 直接问我番剧推荐 / 评价 / 在哪看 / 梗出处都行,或用 `/绑定` 关联你的 Bangumi 账号。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        async with message.channel.typing():
            reply, embeds = await _answer(
                message.author.id,
                message.channel.id,
                message.guild.id if message.guild else None,
                content,
            )
        parts = _split(reply)
        for i, chunk in enumerate(parts):
            # embed 附在最后一段文本上(Discord 单条消息可带 content + 最多10个embed)
            if embeds and i == len(parts) - 1:
                await message.channel.send(chunk, embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
            else:
                await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        if embeds and not parts:
            await message.channel.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())

    async def _slash_answer(interaction: "discord.Interaction", question: str) -> None:
        await interaction.response.defer(thinking=True)
        reply, embeds = await _answer(
            interaction.user.id,
            int(interaction.channel_id or interaction.user.id),
            interaction.guild_id,
            question,
        )
        parts = _split(reply) or ["(没有生成回答)"]
        for i, chunk in enumerate(parts):
            if embeds and i == len(parts) - 1:
                await interaction.followup.send(chunk, embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.followup.send(chunk, allowed_mentions=discord.AllowedMentions.none())

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
        auth.unlink_discord(str(interaction.user.id))
        await interaction.response.send_message("已解绑。之后回到 guest 模式,`/绑定` 可重新关联。", ephemeral=True)

    _HELP = (
        "**Otomo · 番组搭子** —— ACGN 知识图谱 agent 🎴\n\n"
        "**怎么用:**\n"
        "• 在频道里 **@我** 提问,或**私信我**(私信不用 @)\n"
        "• 斜杠命令:`/推荐` `/评价` `/在哪看`\n\n"
        "**能问什么(举例):**\n"
        "• 推荐:`推荐几部治愈番` / `类似孤独摇滚的` / `今晚能看完的短番`\n"
        "• 评价:`药屋少女的呢喃口碑怎么样` / `会不会烂尾`\n"
        "• 追番:`这季什么番最火` / `药屋在哪能看`\n"
        "• 考据:`白色相簿2 冬马的声优还配过谁` / `这是什么梗`\n\n"
        "**个人化:** `/绑定` 关联你的 Bangumi 账号后,推荐会用你自己的收藏画像,还能查你的追番进度。\n"
        "`/我是谁` 看绑定状态,`/解绑` 解除关联。"
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
