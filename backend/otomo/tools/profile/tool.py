"""口味画像工具（A4）：读用户 Bangumi 看过的动画 → 聚合口味 → 写入长期记忆。

- 不传 username 时用 token 经 /v0/me 解析当前用户（你自己的号）。
- 传 username 则读其公开收藏（多用户、零授权）。
- 结果写入长期记忆；下次默认读缓存（refresh=true 可强制重算）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...profile import TasteProfile, compute_taste_profile
from ..bangumi.client import BangumiClient

_MAX_ITEMS = 200  # 首版上限，控制调用量


class TasteArgs(BaseModel):
    username: str | None = Field(
        None, description="Bangumi 用户名；不传则用当前登录账号（需 token）"
    )
    refresh: bool = Field(False, description="是否忽略长期记忆缓存、强制重新计算")


class TasteProfileTool(Tool):
    name = "get_taste_profile"
    description = (
        "分析某用户的二次元口味画像（看过的动画的标签偏好、评分分布、年代、最爱作品）。"
        "用于『分析我的口味 / 我是什么二次元人格 / 据此推荐』。不传 username 用当前账号。"
    )
    args_model = TasteArgs
    result_model = TasteProfile

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: TasteArgs) -> ToolResult[TasteProfile]:
        username = args.username
        if not username:
            me = await self.client.get_me()
            username = me.get("username") or str(me.get("id"))

        if not args.refresh:
            cached = self.ltm.get("taste", username)
            if cached:
                return ToolResult(
                    ok=True,
                    data=TasteProfile.model_validate(cached),
                    sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
                )

        items: list[dict] = []
        offset = 0
        while len(items) < _MAX_ITEMS:
            page = await self.client.get_user_collections(
                username, subject_type=2, collection_type=2, limit=50, offset=offset
            )
            batch = page.get("data") or []
            items.extend(batch)
            if len(batch) < 50:
                break
            offset += 50

        profile = compute_taste_profile(username, items)
        self.ltm.set("taste", username, profile.model_dump())
        return ToolResult(
            ok=True,
            data=profile,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [TasteProfileTool(client, ltm)]
