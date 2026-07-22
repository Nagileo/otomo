"""Discord bot 入口:把 Otomo agent 接到 Discord(python -m otomo.discord_bot)。

Otomo 本质是个 ACGN agent,Discord 是它最自然的形态之一——在服务器里 @机器人、
或私信它,就能问番/推荐/评价/查资源/识梗,复用全部 103 个工具。

设计(v1):
- 触发:私信直接答;服务器频道里被 @ 才答(避免刷屏)。
- 多轮:每个 Discord 用户一个持久会话(AgentState),对话上下文连续。
- guest 模式:用公开 Bangumi API(无个人 token),知识问答/推荐冷启动/评价/导视/
  资源/识梗都能用;"我的收藏"这类需登录的功能优雅降级(v2 再做 Discord↔Bangumi 绑定)。
- 输出:去掉网页专用的 [[panel:x]] 锚点,按 Discord 2000 字上限分段发送。

依赖:pip install -e ".[discord]"(discord.py)。需 DISCORD_BOT_TOKEN,且在开发者后台
开启 MESSAGE CONTENT INTENT(否则读不到消息文本)。
"""
from __future__ import annotations

import asyncio
import logging
import re

from .agent.contracts import AgentState, ErrorEvent, FinalEvent
from .config import settings
from .factory import build_runner
from .memory import LongTermMemory
from .tools.bangumi.client import BangumiClient
from .tools.moegirl.client import MoegirlClient

log = logging.getLogger("otomo.discord")
_PANEL_RE = re.compile(r"\[\[panel:[^\]]*\]\]")
_DISCORD_LIMIT = 1900  # 官方 2000,留余量
# 每个 Discord 用户一个多轮会话;超过阈值软重置,防止上下文无限膨胀。
_sessions: dict[int, AgentState] = {}
_MAX_HISTORY = 24


def _clean(answer: str) -> str:
    """去掉网页面板锚点 + 多余空行(Discord 没有证据面板)。"""
    answer = _PANEL_RE.sub("", answer)
    return re.sub(r"\n{3,}", "\n\n", answer).strip()


def _split(text: str, limit: int = _DISCORD_LIMIT) -> list[str]:
    """按行切成 <=limit 的段,尽量不从句子中间断开。"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:  # 单行超长:硬切
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
    return chunks[:6]  # 最多 6 段,防刷屏


def run() -> None:
    import discord

    token = settings.discord_bot_token
    if not token:
        raise SystemExit("需要 DISCORD_BOT_TOKEN 才能启动 Discord bot")

    intents = discord.Intents.default()
    intents.message_content = True  # privileged:开发者后台需勾选 MESSAGE CONTENT INTENT
    client = discord.Client(intents=intents)

    # agent 侧一次构建、全用户复用(state 每人一份、按调用传入)。guest 模式:无个人 token。
    bangumi = BangumiClient(token=settings.bangumi_token, user_agent=settings.bangumi_user_agent)
    runner = build_runner(bangumi, MoegirlClient(), "adaptive", LongTermMemory())

    async def answer_for(user_id: int, question: str) -> str:
        state = _sessions.get(user_id) or AgentState()
        result = ""
        try:
            async for ev in runner.stream(question, state):
                if isinstance(ev, FinalEvent):
                    result = ev.answer
                elif isinstance(ev, ErrorEvent):
                    result = result or f"⚠️ 出错了:{ev.message[:200]}"
        except Exception as e:  # noqa: BLE001 - 单条消息失败不能拖垮 bot
            log.exception("discord answer failed")
            return f"抱歉,处理时出错了({type(e).__name__}),换个问法再试试?"
        if len(state.messages) > _MAX_HISTORY:  # 软重置:保留 system + 最近若干轮
            state.messages = state.messages[:1] + state.messages[-(_MAX_HISTORY - 1):]
        _sessions[user_id] = state
        return _clean(result) or "(这次没能整理出回答,换个问法试试?)"

    @client.event
    async def on_ready() -> None:
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
        if mentioned:  # 去掉 @机器人 前缀
            content = re.sub(rf"<@!?{client.user.id}>", "", content).strip()
        if not content:
            await message.channel.send("在的~ 直接问我番剧推荐 / 评价 / 在哪看 / 梗出处都行。")
            return
        async with message.channel.typing():
            reply = await answer_for(message.author.id, content)
        for chunk in _split(reply):
            await message.channel.send(chunk)

    client.run(token, log_handler=None)


if __name__ == "__main__":
    run()
