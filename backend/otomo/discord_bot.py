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

import logging
import re

from .agent.contracts import AgentState, ErrorEvent, FinalEvent
from .auth import AuthStore
from .config import settings
from .factory import build_runner
from .memory import LongTermMemory
from .tools.bangumi.client import BangumiClient
from .tools.moegirl.client import MoegirlClient

log = logging.getLogger("otomo.discord")
_PANEL_RE = re.compile(r"\[\[panel:[^\]]*\]\]")
_DISCORD_LIMIT = 1900
_MAX_HISTORY = 24
_MAX_USER_RUNNERS = 32   # 绑定用户各一个带 token 的 runner,LRU 上限防膨胀


def _clean(answer: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _PANEL_RE.sub("", answer)).strip()


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
    moegirl = MoegirlClient()
    _sessions: dict[int, AgentState] = {}
    _guest_client = BangumiClient(token=settings.bangumi_token, user_agent=settings.bangumi_user_agent)
    _guest_runner = build_runner(_guest_client, moegirl, "adaptive", ltm)
    _user_runners: dict[str, object] = {}   # username -> runner(带该用户 token)

    def _runner_for(discord_user_id: int):
        """绑定用户 → 用其 Bangumi token 的个人化 runner;否则 guest。"""
        username = auth.username_for_discord(str(discord_user_id))
        if not username:
            return _guest_runner, None
        tok = auth.token_for_username(username)
        if not tok or tok.status != "active":
            return _guest_runner, username  # 绑过但 token 失效:退回 guest,回答里可提示重绑
        if username not in _user_runners:
            if len(_user_runners) >= _MAX_USER_RUNNERS:
                _user_runners.pop(next(iter(_user_runners)))
            client = BangumiClient(token=tok.access_token, user_agent=settings.bangumi_user_agent)
            _user_runners[username] = build_runner(client, moegirl, "adaptive", ltm)
        return _user_runners[username], username

    async def _answer(discord_user_id: int, question: str) -> str:
        runner, _username = _runner_for(discord_user_id)
        state = _sessions.get(discord_user_id) or AgentState()
        result = ""
        try:
            async for ev in runner.stream(question, state):
                if isinstance(ev, FinalEvent):
                    result = ev.answer
                elif isinstance(ev, ErrorEvent):
                    result = result or f"⚠️ 出错了:{ev.message[:200]}"
        except Exception as e:  # noqa: BLE001
            log.exception("discord answer failed")
            return f"抱歉,处理时出错了({type(e).__name__}),换个问法再试试?"
        if len(state.messages) > _MAX_HISTORY:
            state.messages = state.messages[:1] + state.messages[-(_MAX_HISTORY - 1):]
        _sessions[discord_user_id] = state
        return _clean(result) or "(这次没能整理出回答,换个问法试试?)"

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
            await message.channel.send("在的~ 直接问我番剧推荐 / 评价 / 在哪看 / 梗出处都行,或用 `/绑定` 关联你的 Bangumi 账号。")
            return
        async with message.channel.typing():
            reply = await _answer(message.author.id, content)
        for chunk in _split(reply):
            await message.channel.send(chunk)

    async def _slash_answer(interaction: "discord.Interaction", question: str) -> None:
        await interaction.response.defer(thinking=True)
        reply = await _answer(interaction.user.id, question)
        parts = _split(reply)
        await interaction.followup.send(parts[0])
        for chunk in parts[1:]:
            await interaction.followup.send(chunk)

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
        _user_runners.clear()
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
        tok = auth.token_for_username(username)
        status = tok.status if tok else "找不到 token"
        await interaction.response.send_message(
            f"Discord ID `{uid}`\n绑定账号:**{username}**\nToken 状态:`{status}`"
            + ("\n✅ 个人化已生效" if tok and tok.status == "active" else "\n⚠️ token 异常,`/解绑` 后重新 `/绑定`"),
            ephemeral=True,
        )

    client.run(token, log_handler=None)


if __name__ == "__main__":
    run()
