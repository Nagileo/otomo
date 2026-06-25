"""口味画像工具（A4）：读用户 Bangumi 看过的动画 → 聚合口味 → 写入长期记忆。

- 不传 username 时用 token 经 /v0/me 解析当前用户（你自己的号）。
- 传 username 则读其公开收藏（多用户、零授权）。
- 当前开发阶段每次按最新收藏重算；上线后再按需要启用缓存。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...profile import TasteProfile, compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_MAX_ITEMS = 1000  # 重度用户常 >300，尽量拉全（分页每页 50）；再大就分批/采样


class TasteArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = Field(
        "anime", description="对哪类作品画像（anime/book(漫画·小说)/music/game/real）；默认动画"
    )
    username: str | None = Field(
        None, description="Bangumi 用户名；不传则用当前登录账号（需 token）"
    )
    refresh: bool = Field(False, description="兼容参数；当前开发阶段始终重新计算")


class TasteProfileTool(Tool):
    name = "get_taste_profile"
    description = (
        "分析某用户的二次元口味画像（看过的动画的标签偏好、评分分布、年代、最爱作品）。"
        "用于『分析我的口味 / 我是什么二次元人格 / 据此推荐』。不传 username 用当前账号。"
    )
    args_model = TasteArgs
    result_model = TasteProfile

    def __init__(self, client: BangumiClient, _ltm: LongTermMemory) -> None:
        self.client = client

    async def run(self, args: TasteArgs) -> ToolResult[TasteProfile]:
        username = args.username
        if not username:
            me = await self.client.get_me()
            username = me.get("username") or str(me.get("id"))

        items = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE[args.subject_type], collection_type=2, max_items=_MAX_ITEMS
        )
        profile = compute_taste_profile(username, items)
        return ToolResult(
            ok=True,
            data=profile,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [TasteProfileTool(client, ltm)]
