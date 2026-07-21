"""渐进式工具披露（progressive disclosure）。

96 个工具全量塞给模型时：单轮 ~26k token 光是工具 schema，且工具数越多模型选错/
反复空转越严重（业界共识：function-calling 准确率随工具目录增长而下降）。

方案 = 核心常驻 + 按查询词法初选相关工具组 + `load_tool_group` 逃生舱：
  - CORE：任何查询都可能用到的图谱/检索/推荐/评价/记忆/剧透/兜底，始终暴露。
  - GROUPS：按域分组，词法命中查询关键词才注入。
  - 逃生舱：词法漏选时，模型自己调 load_tool_group 把某组拉进上下文（对标
    Anthropic Tool Search / RAG-MCP 的按需加载），保证没有硬失败路径。

对标：progressive disclosure（渐进披露）是 2025-2026 的统一原则；此处用"按域粗到细 +
逃生舱"落地，贴合 Otomo 工具本就按域组织、adaptive 已有 router 的现状，零向量库依赖。
"""
from __future__ import annotations

import re
from typing import Any

from ..config import settings
from .registry import ToolRegistry

META_TOOL = "load_tool_group"

# 任何查询都可能需要：实体图谱解析 + 顶层意图入口 + 记忆读 + 剧透护栏 + 知识兜底。
CORE_TOOLS: set[str] = {
    # 实体图谱（几乎所有问题都要先把名字解析成 id，再沿关系边走）
    "search_subjects", "get_subject", "search_characters", "search_persons",
    "get_subject_characters", "get_subject_persons", "get_subject_relations",
    "get_subject_episodes", "get_character_persons", "get_person_subjects", "check_subjects",
    # 顶层高频意图入口
    "recommend_subjects", "review_subject", "season_guide_brief",
    "where_to_watch", "get_broadcast_calendar",
    # 记忆读 + 反馈（便宜且频繁相关）
    "get_user_memory", "remember_user_preference", "record_recommendation_feedback",
    # 剧透护栏（触碰剧情前应随时可用）
    "assess_spoiler_policy",
    # 知识兜底
    "lore_search", "web_search",
    # 逃生舱
    META_TOOL,
}

# 每组：tools（工具名集合）+ keywords（命中即初选该组）+ desc（给逃生舱元工具的说明）
TOOL_GROUPS: dict[str, dict[str, Any]] = {
    "vision": {
        "desc": "图片/截图识别、以图搜源、OCR、画风推荐、pixiv 插画、视频抽帧",
        "tools": {
            "route_image_source", "extract_visual_text", "recommend_by_visual_style",
            "search_image_source", "analyze_video_frames",
            "get_pixiv_ranking", "search_pixiv_illusts", "get_pixiv_artist_portfolio",
        },
        "keywords": ["图", "截图", "这是什么", "出自", "谁画", "pixiv", "插画", "cosplay", "画风", "ocr", "表情包", "封面", "壁纸"],
    },
    "video_bili": {
        "desc": "B站导视/漫评视频检索、弹幕/字幕/评论读取、视频内容摘要",
        "tools": {
            "find_related_videos", "find_guide_videos", "search_bilibili_guide_videos",
            "get_bilibili_video_comments", "get_bilibili_video_danmaku",
            "get_bilibili_video_subtitles", "summarize_bilibili_video_content",
        },
        "keywords": ["b站", "bilibili", "视频", "导视", "漫评", "up主", "up 主", "弹幕", "字幕", "bv", "解说"],
    },
    "music": {
        "desc": "OP/ED/主题曲、动画音乐元数据（AnimeThemes / MusicBrainz）",
        "tools": {"anime_music_themes", "search_anime_themes", "search_musicbrainz"},
        "keywords": ["op", "ed", "主题曲", "片头", "片尾", "歌", "音乐", "ost", "作曲", "谁唱", "声优歌"],
    },
    "pilgrimage": {
        "desc": "圣地巡礼取景地地图、按目的地城市/区域规划巡礼行程",
        "tools": {"get_pilgrimage_map", "plan_pilgrimage_trip"},
        "keywords": ["圣地", "巡礼", "取景", "打卡", "旅游", "旅行", "去.{0,6}玩", "景点"],
    },
    "watch_resource": {
        "desc": "在哪看/在哪买正版渠道、离线资源 RSS/种子/BD（蜜柑/dmhy/acgnx/VCB）、推送下载器",
        "tools": {"where_to_watch", "get_anime_release_feeds", "get_vertical_links", "prepare_downloader_push"},
        "keywords": ["在哪看", "在哪买", "资源", "下载", "rss", "种子", "磁力", "蜜柑", "mikan", "dmhy", "acgnx", "vcb", "bd", "字幕组", "正版", "购买", "steam", "dlsite", "订阅.{0,4}字幕"],
    },
    "season_hot": {
        "desc": "季度新番表、放送日历、追番进度、全站热门/热播榜",
        "tools": {"list_season_anime", "list_year_anime", "list_yuc_season", "get_airing_progress", "get_trending_subjects", "get_rating_movers", "scan_my_episode_buzz"},
        "keywords": ["新番", "这季", "本季", "几月", "放送", "追什么", "热播", "热门", "最热", "最火", "火爆", "热度", "排行", "榜", "时间表", "追番", "在追", "更新", "开播", "落后", "现在.{0,4}看", "哪集", "炸了", "爆点", "名场面"],
    },
    "recommend_extra": {
        "desc": "萌点标签检索、评分预测、好友同步召回、推荐清单保存、口味/aspect 画像",
        "tools": {"search_by_traits", "predict_my_rating", "sync_user_recommendations", "save_recommendation_list", "get_taste_profile", "build_aspect_profile"},
        "keywords": ["萌点", "标签", "预测.{0,4}分", "同步", "画像", "好球区", "雷区", "换一批", "补番", "冷门"],
    },
    "review_extra": {
        "desc": "galgame 圈层评分（批判空间/EGS/VNDB）、英文圈/音乐元数据评分、分集口碑、私评情感",
        "tools": {"get_subject_comments", "get_episode_comments", "episode_buzz_radar", "rank_erogamescape", "search_erogamescape", "search_visual_novels", "search_anilist", "analyze_user_opinions", "get_subject_trend", "get_rating_movers"},
        "keywords": ["评价", "口碑", "怎么样", "好不好", "值不值", "争议", "打分", "评分", "批判空间", "egs", "vndb", "galgame", "gal", "哪几集", "名场面", "高能", "走势", "崩", "涨", "跌", "变化", "期待"],
    },
    "user_analysis": {
        "desc": "好友列表、口味同步率对比、弃坑/搁置分析",
        "tools": {"compare_user_taste", "list_bangumi_friends", "analyze_abandoned_subjects", "export_my_collections_csv"},
        # friends_pulse/matrix 都走 compare_user_taste
        "keywords": ["好友", "同步率", "口味.{0,4}像", "和.{1,8}像", "弃坑", "搁置", "抛弃", "圈子", "都在看", "都在追", "都想看", "导出", "备份", "csv", "表格"],
    },
    "memory_plan": {
        "desc": "长期记忆写入/遗忘、决策日志、追番计划板、Bangumi 收藏写回（加想看/打分/进度打卡）",
        "tools": {"forget_user_memory", "record_decision_log", "list_watch_plan", "upsert_watch_plan_item", "prepare_bangumi_write_action", "cancel_bangumi_write_action", "execute_bangumi_write_action", "undo_bangumi_write_action", "get_my_episode_progress"},
        "keywords": ["记住", "别推", "别再", "想看", "在看", "计划", "加入", "标记", "打.{0,2}分", "看过", "追番计划", "忘掉", "别记", "确认", "写回", "同步", "撤销", "看到第", "看完第", "进度", "打卡", "下一集"],
    },
    "product_page": {
        "desc": "追番驾驶舱、作品档案页、IP 跨媒介图谱、月度报告、口味报告、收藏仪表盘、作品对比、补番顺序",
        "tools": {"watch_cockpit", "subject_dossier", "franchise_map", "monthly_watch_report", "build_taste_report", "build_collection_dashboard", "compare_subjects", "plan_watch_order", "plan_watch_copilot"},
        "keywords": ["档案", "驾驶舱", "系列", "ip", "月报", "月度", "年度", "年终", "wrapped", "我的20", "报告", "仪表盘", "对比", "哪个好", "补番顺序", "观看顺序", "全貌", "总结"],
    },
    "discovery_extra": {
        "desc": "今日角色生日、Bangumi 精选目录/清单",
        "tools": {"get_character_birthdays", "get_bangumi_index", "explore_voice_network", "anime_omikuji", "generate_acgn_quiz"},
        "keywords": ["生日", "目录", "清单", "声优网络", "配过", "同台", "谁配", "抽签", "番签", "运势", "签", "考考", "出题", "quiz", "答题", "猜"],
    },
    "digest": {
        "desc": "周报生成/配置、收件箱查看",
        "tools": {"build_weekly_digest", "configure_weekly_digest", "generate_weekly_digest_now", "list_weekly_digest_inbox"},
        "keywords": ["周报", "订阅", "提醒", "收件箱", "inbox", "推送"],
    },
    "lore_extra": {
        "desc": "维基检索、ACGN 梗解释、指定 URL 页面摘要",
        "tools": {"wiki_search", "explain_acgn_meme", "fetch_url_summary", "browser_fetch_summary"},
        "keywords": ["梗", "设定", "世界观", "出处", "台词", "什么意思", "考据", "维基", "百科", "http", "这个链接", "这篇"],
    },
    "source_router": {
        "desc": "跨媒介信息源路由：解释某作品该用哪些源（canonical/口碑/话语/导航分层）",
        "tools": {"route_subject_sources"},
        "keywords": ["源", "该信谁", "哪个源", "来源可信", "怎么选源"],
    },
}

# 组名 → 该组全部工具（含逃生舱说明用）
_ALL_GROUP_NAMES = list(TOOL_GROUPS.keys())


def _group_tools(groups: set[str]) -> set[str]:
    out: set[str] = set()
    for g in groups:
        spec = TOOL_GROUPS.get(g)
        if spec:
            out |= spec["tools"]
    return out


def initial_groups(user_input: str) -> set[str]:
    """按查询词法命中初选工具组。宁可多选（逃生舱兜底漏选，但多选只是多点 token）。"""
    text = (user_input or "").lower()
    hit: set[str] = set()
    for name, spec in TOOL_GROUPS.items():
        for kw in spec["keywords"]:
            if re.search(kw, text) if any(c in kw for c in ".{[") else (kw in text):
                hit.add(name)
                break
    return hit


def meta_observation(args_json: str) -> str:
    """逃生舱被调用后回给模型的观察文本：列出刚加载的工具。"""
    import json

    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        args = {}
    groups = args.get("groups") or ([args["group"]] if args.get("group") else [])
    loaded: list[str] = []
    for g in groups:
        spec = TOOL_GROUPS.get(str(g))
        if spec:
            loaded.extend(sorted(spec["tools"]))
    if not loaded:
        return "没有匹配的工具组。可用组：" + "、".join(_ALL_GROUP_NAMES)
    return "已加载工具组，以下工具现在可直接调用：" + "、".join(loaded)


def meta_tool_schema() -> dict[str, Any]:
    """逃生舱元工具的 OpenAI schema——描述里枚举所有组，让模型知道能加载什么。"""
    catalog = "；".join(f"{name}（{spec['desc']}）" for name, spec in TOOL_GROUPS.items())
    return {
        "type": "function",
        "function": {
            "name": META_TOOL,
            "description": (
                "当你需要的工具当前不在可用列表里时，用它按需加载对应工具组，加载后这些工具立即可调用。"
                "不要为已经可用的能力调用它。可用工具组："
                + catalog
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "groups": {
                        "type": "array",
                        "items": {"type": "string", "enum": _ALL_GROUP_NAMES},
                        "description": "要加载的工具组名（可多选）",
                    }
                },
                "required": ["groups"],
            },
        },
    }


class ToolSelector:
    """按查询选工具子集；逃生舱调用后可增量激活更多组。runner 每轮从它取 schema。"""

    def __init__(self, registry: ToolRegistry, user_input: str, *, enabled: bool | None = None) -> None:
        self.registry = registry
        self.enabled = settings.tool_progressive_disclosure_enabled if enabled is None else enabled
        self.active_groups: set[str] = initial_groups(user_input) if self.enabled else set(_ALL_GROUP_NAMES)

    def active_names(self) -> set[str]:
        return CORE_TOOLS | _group_tools(self.active_groups)

    def schemas(self) -> list[dict[str, Any]]:
        """当前暴露给模型的工具 schema。始终含逃生舱（除非全量模式）。"""
        if not self.enabled:
            return self.registry.openai_tools(include_write=True)
        names = self.active_names()
        schemas = self.registry.openai_tools_for(names, include_write=True)
        schemas.append(meta_tool_schema())
        return schemas

    def activate(self, group: str) -> bool:
        if group in TOOL_GROUPS and group not in self.active_groups:
            self.active_groups.add(group)
            return True
        return False

    def resolve(self, name: str) -> str | None:
        """给定被调用的工具名，返回它所属的组名（若在某组内）——用于误调未加载工具时自动补活。"""
        for gname, spec in TOOL_GROUPS.items():
            if name in spec["tools"]:
                return gname
        return None

    def note_meta_calls(self, msg: Any) -> list[str]:
        """扫描一条 assistant 消息里的 load_tool_group 调用，激活对应组，返回本次新激活的组名。"""
        activated: list[str] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            if tc.function.name != META_TOOL:
                continue
            import json

            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            groups = args.get("groups") or ([args["group"]] if args.get("group") else [])
            for g in groups:
                if self.activate(str(g)):
                    activated.append(str(g))
        return activated
