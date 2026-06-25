"""垂直社区 / 资源站外链 provider——二次元圈层化，按 type/题材导流到对口站。

**只构造跳转外链、绝不抓取/托管**（守 docs/04「在哪看只给外链」红线）。
URL 一律用确信的搜索入口（蜜柑/VCB 搜索、B站搜索、贴吧、yuc.wiki），具体社区名写进 note 引导，
避免给过时/错误的深链。
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ...agent.contracts import Tool, ToolResult


def _bili(kw: str) -> str:
    return f"https://search.bilibili.com/all?keyword={quote(kw)}"


# 题材关键词（匹配 tag）→ (站点名, url, 说明)
_THEME_SITES: list[tuple[tuple[str, ...], str, str, str]] = [
    (("百合", "GL"), "百合会 / 百合控", _bili("百合 推荐"), "百合作品同好社区（百合会论坛 / B站百合区）"),
    (("芳文", "Kirara", "きらら", "日常系"), "芳文观星台", _bili("芳文观星台"), "芳文社 / きらら系盘点（B站 UP）"),
    (("萌战", "人气投票"), "萌战吧", "https://tieba.baidu.com/f?kw=%E8%90%8C%E6%88%98", "角色人气/萌战讨论（百度贴吧）"),
]


class VerticalLinksArgs(BaseModel):
    name: str = Field(..., description="作品名")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(
        None, description="作品题材标签（从 get_subject 拿），用于匹配题材社区（百合/芳文社…）"
    )


class LinkItem(BaseModel):
    site: str
    url: str
    note: str


class VerticalLinksResult(BaseModel):
    name: str
    links: list[LinkItem] = Field(default_factory=list)


class VerticalLinksTool(Tool):
    name = "get_vertical_links"
    description = (
        "给作品按**类型/题材**导流到对口的垂直社区与资源站**外链**（只跳转不抓取）："
        "番剧资源（蜜柑/VCB）、季番导视（B站名作之壁吧/范式、长门有 C 时间表）、"
        "题材社区（百合会/芳文观星台/galgame 绯月）。"
        "用于『哪里下载/在哪看/哪个社区聊这部/同好在哪/有没有导视』。引用时注明是外部站点。"
    )
    args_model = VerticalLinksArgs
    result_model = VerticalLinksResult

    async def run(self, args: VerticalLinksArgs) -> ToolResult[VerticalLinksResult]:
        name, t, q = args.name, args.subject_type, quote(args.name)
        links: list[LinkItem] = []
        if t == "anime":
            links += [
                LinkItem(site="蜜柑动漫", url=f"https://mikanani.me/Home/Search?searchstr={q}", note="番剧下载 / RSS 订阅"),
                LinkItem(site="VCB-Studio", url=f"https://vcb-s.com/?s={q}", note="高清收藏版压制"),
                LinkItem(site="B站导视", url=_bili(f"{name} 导视"), note="名作之壁吧 / 范式等季番导视、盘点"),
                LinkItem(site="新番时间表", url="https://yuc.wiki/", note="长门有 C 的新番放送时间表"),
            ]
        elif t == "game":
            links.append(LinkItem(site="绯月 Kf", url=_bili(f"{name} galgame"), note="galgame 社区/补丁/资源（绯月 kfpromax 等）"))
        elif t == "book":
            links.append(LinkItem(site="B站搜索", url=_bili(f"{name} 漫画 轻小说"), note="原作漫画/轻小说讨论与资源"))

        seen = {li.site for li in links}
        for tag in args.tags or []:
            for keys, site, url, note in _THEME_SITES:
                if site not in seen and any(k in tag for k in keys):
                    links.append(LinkItem(site=site, url=url, note=note))
                    seen.add(site)
        return ToolResult(ok=True, data=VerticalLinksResult(name=name, links=links))


def build_community_tools() -> list[Tool]:
    return [VerticalLinksTool()]
