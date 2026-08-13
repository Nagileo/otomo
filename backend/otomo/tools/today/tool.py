from __future__ import annotations

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...security_context import can_access_private_user
from ...today import TodayCockpitResult, TodayCockpitService, TodayPreferenceStore
from ..bangumi.client import BangumiClient


class TodayCockpitArgs(BaseModel):
    username: str | None = Field(None, description="不传则使用当前绑定的 Bangumi 用户")
    include_wishlist: bool = True
    include_hidden: bool = True
    limit: int = Field(80, ge=1, le=80)


class TodayCockpitTool(Tool):
    name = "today_cockpit"
    description = (
        "读取当前用户统一的今日追番驾驶舱：今天/昨天/本周放送、落后进度、置顶和本季隐藏。"
        "问『今天更新什么』『我今天追什么』『今天点哪些格子』时优先使用；结果与固定 /today 页面和每日提醒一致。"
    )
    args_model = TodayCockpitArgs
    result_model = TodayCockpitResult

    def __init__(self, client: BangumiClient, store: TodayPreferenceStore | None = None) -> None:
        self.client = client
        self.store = store or TodayPreferenceStore()

    async def run(self, args: TodayCockpitArgs) -> ToolResult[TodayCockpitResult]:
        username = (args.username or "").strip()
        if not username:
            try:
                me = await self.client.get_me()
                username = str(me.get("username") or me.get("id") or "").strip()
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, error=f"今日追番需要先绑定 Bangumi 账号：{exc}")
        if not can_access_private_user(username):
            return ToolResult(ok=False, error="今日追番驾驶舱只允许读取当前登录用户的私有偏好与进度")
        data = await TodayCockpitService(self.client, self.store).build(
            username,
            include_wishlist=args.include_wishlist,
            include_hidden=args.include_hidden,
            limit=args.limit,
        )
        items = data.today or data.backlog
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=item.name_cn or item.name, url=item.url, source="bangumi", image=item.image)
                for item in items[:8]
            ],
        )


def build_today_tools(client: BangumiClient) -> list[Tool]:
    return [TodayCockpitTool(client)]
