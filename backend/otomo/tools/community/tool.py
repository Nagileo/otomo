"""Source Router 外链层：按**意图×类型×题材**精选垂直社区/资源站外链（只跳转不抓取）。

不甩一堆站——按 intent（download/guide/discuss/media）挑最相关的几个，URL 用确信的搜索入口 / UP 主页
（站点清单见 docs/10 B.4）。守 docs/04「在哪看只给外链」红线：不下载/不托管/不代理。
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ...agent.contracts import Tool, ToolResult


def _bili(kw: str) -> str:
    return f"https://search.bilibili.com/all?keyword={quote(kw)}"


def _tieba(kw: str) -> str:
    return f"https://tieba.baidu.com/f?kw={quote(kw)}"


# 题材关键词（匹配 tag）→ [(站点, url, 说明)]
_THEME_SITES: list[tuple[tuple[str, ...], list[tuple[str, str, str]]]] = [
    (("百合", "GL"), [
        ("百合会", "https://bbs.yamibo.com/forum.php", "百合作品论坛（动漫/漫画/轻小说区）"),
        ("FlowerMX-花梦", "https://space.bilibili.com/13181306", "百合向导视/推荐 UP"),
        ("峻岸上的喀秋莎", "https://space.bilibili.com/228172909", "百合作品翻译 UP"),
    ]),
    (("芳文", "Kirara", "きらら", "日常系"), [
        ("芳文观星台", "https://space.bilibili.com/1585955812", "芳文社/きらら系盘点 UP"),
        ("大猫猫组", "https://space.bilibili.com/526330959", "芳文向 UP"),
    ]),
    (("萌战", "党争", "人气"), [
        ("萌战吧", _tieba("萌战"), "角色人气/萌战（百度贴吧）"),
    ]),
]


class VerticalLinksArgs(BaseModel):
    name: str = Field(..., description="作品名")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(None, description="作品题材标签（从 get_subject 拿），匹配题材社区")
    intent: Literal["download", "guide", "discuss", "media", "all"] = Field(
        "all", description="意图：download 下载/在哪看 · guide 新番导视 · discuss 圈层讨论 · media 图/音乐 · all 各取主要"
    )


class LinkItem(BaseModel):
    site: str
    url: str
    note: str


class VerticalLinksResult(BaseModel):
    name: str
    intent: str
    links: list[LinkItem] = Field(default_factory=list)


class VerticalLinksTool(Tool):
    name = "get_vertical_links"
    description = (
        "按**意图**精选垂直社区/资源站**外链**（只跳转、不抓取）。"
        "intent：download（哪里下/在哪看）· guide（新番导视）· discuss（圈层讨论/同好）· media（同人图/音乐）。"
        "用于『哪里下载 / 看哪个导视 / 哪个社区聊这部 / 找图找歌』——挑最相关的几个，不甩一堆。引用注明是外部站点。"
    )
    args_model = VerticalLinksArgs
    result_model = VerticalLinksResult

    async def run(self, args: VerticalLinksArgs) -> ToolResult[VerticalLinksResult]:
        name, t, q, intent = args.name, args.subject_type, quote(args.name), args.intent
        links: list[LinkItem] = []

        def add(site: str, url: str, note: str) -> None:
            links.append(LinkItem(site=site, url=url, note=note))

        def want(i: str) -> bool:
            return intent in ("all", i)

        if want("download") and t == "anime":
            add("蜜柑动漫", f"https://mikanani.me/Home/Search?searchstr={q}", "番剧下载/RSS（收录最全，优先）")
            add("VCB-Studio", f"https://vcb-s.com/?s={q}", "BD/高清压制（收藏向）")
            if intent == "download":  # 深挖下载才给备选
                add("动漫花园", f"https://share.dmhy.org/topics/list?keyword={q}", "BT 资源（备选）")
                add("末日动漫", "https://share.acgnx.se/", "BT 资源（备选，站内搜）")
        if want("guide") and t == "anime":
            add("名作之壁吧", "https://space.bilibili.com/2859372", "数据向新番导视（最推）")
            add("新番时间表", "https://yuc.wiki/", "长门有 C 放送时间表/RSS")
            if intent == "guide":
                add("泛式", "https://space.bilibili.com/63231", "评价向漫评/导视 UP")
                add("瓶子君152", "https://space.bilibili.com/730732", "评价向漫评 UP")
                add("台长", "https://space.bilibili.com/213741", "综合漫评/动画杂谈 UP")
        if want("discuss"):
            for keys, sites in _THEME_SITES:  # 题材社区（按 tag）
                if any(any(k in tag for k in keys) for tag in (args.tags or [])):
                    for s, u, n in sites:
                        add(s, u, n)
            if t == "game":  # galgame 圈
                add("VNDB", f"https://vndb.org/v?q={q}", "galgame 权威库（评分/资料，亦有 search_visual_novels 工具）")
                add("批判空间", "https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/", "日系 galgame 打分站（权威评分）")
                add("绯月 Kf", "https://bbs.kfpromax.com/", "galgame 社区/补丁")
                add("月幕", "https://www.ymgal.games/", "galgame 资料/汉化")
                add("galgame 吧", _tieba("galgame"), "galgame 讨论（贴吧）")
            elif t == "book":  # 轻小说圈
                add("轻之国度", "https://www.lightnovel.fun/", "轻小说讨论/资源")
                add("真白萌", "https://masiro.me/", "web 小说/轻小说")
            add("NGA", "https://bbs.nga.cn/", "综合 ACG 讨论区（动漫区）")
            add("S1", "https://stage1st.com/2b/", "综合 ACG 论坛（2 次元区）")
            add("更广讨论", _bili(f"{name} 评价"), "B站漫评等（搜索）")
        if want("media"):
            add("Pixiv", f"https://www.pixiv.net/tags/{q}/artworks", "同人图/插画")
            add("推特搜索", f"https://x.com/search?q={q}", "同人图/情报（X / Twitter）")
            add("网易云音乐", f"https://music.163.com/#/search/m/?s={q}", "OST/角色歌（歌单/乐评区）")
            add("QQ音乐", f"https://y.qq.com/n/ryqq/search?w={q}", "OST/角色歌")

        return ToolResult(ok=True, data=VerticalLinksResult(name=name, intent=intent, links=links))


def build_community_tools() -> list[Tool]:
    return [VerticalLinksTool()]
