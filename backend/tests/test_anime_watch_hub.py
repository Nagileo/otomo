from __future__ import annotations

import pytest

from otomo.agent.contracts import ToolResult
from otomo.tools.product_loop.tool import AnimeWatchHubArgs, AnimeWatchHubTool, _duration_minutes
from otomo.tools.release.tool import AnimeReleaseFeedsResult
from otomo.tools.videos.tool import (
    BiliSubjectVideoMeta,
    BiliSubjectVideosResult,
    BiliSubjectVideosArgs,
    SearchBiliSubjectVideosTool,
    _episode_coverage,
    _subject_version_compatibility,
    classify_subject_video,
)
from otomo.tools.watch.tool import WatchSource, WhereToWatchResult


def test_long_public_episode_is_watchable_without_staff_name_match() -> None:
    role, uploader, watch_candidate, identity, content, caution = classify_subject_video(
        "《测试动画》第1话 正片",
        "完全不同名字的UP",
        "本作品第一话限时公开",
        staff_names=["测试动画制作委员会"],
        duration_seconds=24 * 60,
        copyright_code=2,
        match_confidence=0.91,
    )
    assert role == "public_full_episode"
    assert uploader == "unknown"
    assert watch_candidate is True
    assert identity == []
    assert any("24 分钟" in row for row in content)
    assert "不是番剧库正版入口" in caution


def test_self_claimed_full_episode_is_not_promoted_to_reliable_watch() -> None:
    role, uploader, watch_candidate, _identity, _content, caution = classify_subject_video(
        "《测试动画》第1话 完整版",
        "某搬运官方号",
        "",
        staff_names=["真实制作公司"],
    )
    assert role == "episode_candidate"
    assert uploader == "self_claimed_official"
    assert watch_candidate is False
    assert "证据不足" in caution


def test_missing_uploader_never_matches_staff_identity() -> None:
    role, uploader, watch_candidate, _identity, _content, _caution = classify_subject_video(
        "《测试动画》第1话 正片",
        "",
        "",
        staff_names=["测试动画制作委员会"],
    )
    assert role == "episode_candidate"
    assert uploader == "unknown"
    assert watch_candidate is False


def test_short_clip_stays_uncertain_even_if_description_claims_full_episode() -> None:
    role, _uploader, watch_candidate, _identity, content, _caution = classify_subject_video(
        "K-ON轻音少女【台配国语中字】",
        "普通动画收藏UP",
        "完整版正片",
        duration_seconds=9 * 60,
        match_confidence=0.91,
        expected_duration_seconds=24 * 60,
    )
    assert role == "episode_candidate"
    assert watch_candidate is False
    assert any("9 分钟" in row for row in content)


def test_partial_length_upload_stays_uncertain_when_runtime_is_known() -> None:
    role, _uploader, watch_candidate, _identity, _content, _caution = classify_subject_video(
        "《测试动画》国语中字 1080P",
        "普通UP",
        "正片",
        duration_seconds=15 * 60,
        match_confidence=0.93,
        expected_duration_seconds=24 * 60,
    )
    assert role == "episode_candidate"
    assert watch_candidate is False


def test_short_format_can_be_full_when_subject_runtime_is_short() -> None:
    role, _uploader, watch_candidate, _identity, content, _caution = classify_subject_video(
        "《短篇测试动画》第3话",
        "普通UP",
        "中字",
        duration_seconds=5 * 60,
        match_confidence=0.9,
        expected_duration_seconds=5 * 60,
    )
    assert role == "public_full_episode"
    assert watch_candidate is True
    assert any("参考时长约 5 分钟" in row for row in content)


def test_duration_parser_handles_minutes_hours_and_clock() -> None:
    assert _duration_minutes("24分钟") == 24
    assert _duration_minutes("1小时30分") == 90
    assert _duration_minutes("00:05:30") == 5.5


def test_review_is_not_mixed_with_full_episode() -> None:
    role, uploader, watch_candidate, _identity, _content, _caution = classify_subject_video(
        "《测试动画》首集漫评：作画很好但节奏很怪",
        "普通漫评UP",
        staff_names=[],
    )
    assert role == "review"
    assert uploader == "creator"
    assert watch_candidate is False


def test_condensed_story_is_never_promoted_by_full_episode_words_or_duration() -> None:
    role, uploader, watch_candidate, _identity, _content, caution = classify_subject_video(
        "一口气看完《罗小黑战记》剧场版+动画+漫画番外！完整版",
        "剧情解说UP",
        "全剧情故事梳理",
        duration_seconds=53 * 60,
        match_confidence=0.98,
        expected_duration_seconds=5 * 60,
    )
    assert role == "retrospective"
    assert uploader == "creator"
    assert watch_candidate is False
    assert "观点内容" in caution


def test_subject_version_check_rejects_other_seasons_and_formats() -> None:
    ok, reason = _subject_version_compatibility(
        ["罗小黑战记"],
        "《罗小黑战记》第二季 4K 全集",
        subject_platform="Web",
    )
    assert ok is False
    assert "第 2 季" in reason

    ok, reason = _subject_version_compatibility(
        ["测试动画 第二季"],
        "测试动画 第一季 第1话",
        subject_platform="TV",
    )
    assert ok is False
    assert "冲突" in reason

    ok, reason = _subject_version_compatibility(
        ["罗小黑战记"],
        "罗小黑战记 剧场版 完整版",
        subject_platform="Web",
    )
    assert ok is False
    assert "剧场版" in reason


def test_multi_page_episode_coverage_is_explained() -> None:
    assert _episode_coverage(
        "测试动画 全集",
        ["第1话 开始", "第2话 相遇", "第3话 尾声"],
    ) == "第 1–3 话"


def test_reaction_and_extras_do_not_fill_default_video_cards() -> None:
    reaction = classify_subject_video(
        "【英配版】轻音少女 第一季【1080P/英文字幕】reaction",
        "百合补番",
        "",
        staff_names=[],
    )
    extras = classify_subject_video(
        "［BDNC1080+/OP/ED/映像特典]轻音少女/剧场版",
        "番剧蓝光特典",
        "二创整理",
        staff_names=[],
    )

    assert reaction[0] == "related"
    assert extras[0] == "related"


def test_concert_is_not_treated_as_full_episode() -> None:
    result = classify_subject_video(
        "K-ON!! Live Event - Come With Me!!",
        "活动录像UP",
        "中日双语字幕 4K",
        duration_seconds=3 * 60 * 60,
        page_titles=["Live Part 1", "Live Part 2"],
        match_confidence=0.99,
        expected_duration_seconds=24 * 60,
    )
    assert result[0] == "related"


@pytest.mark.asyncio
async def test_subject_name_used_as_comparison_does_not_enter_cards(monkeypatch) -> None:
    from otomo.tools.videos import tool as videos_tool

    row = {
        "title": "【BD1080P】《空之音》1-13话 军队版轻音少女",
        "author": "动画收藏UP",
        "bvid": "BVcomparison",
        "aid": 11,
        "pubdate": 100,
        "arcurl": "https://www.bilibili.com/video/BVcomparison",
    }

    async def fake_search(_query: str):
        return {"data": {"result": [row]}}

    monkeypatch.setattr(videos_tool, "_bili_search_async", fake_search)
    result = await SearchBiliSubjectVideosTool().run(BiliSubjectVideosArgs(
        query="轻音少女",
        aliases=["K-ON!"],
        lifecycle="archive",
        limit=5,
    ))
    assert result.ok and result.data is not None
    assert result.data.videos == []


@pytest.mark.asyncio
async def test_subject_video_search_does_not_fill_cards_with_merch(monkeypatch) -> None:
    from otomo.tools.videos import tool as videos_tool

    rows = [
        {
            "title": "轻音少女手办推荐与快闪产品实拍",
            "author": "周边收藏者",
            "bvid": "BVmerch",
            "aid": 1,
            "pubdate": 200,
            "arcurl": "https://www.bilibili.com/video/BVmerch",
        },
        {
            "title": "轻音少女补番回顾：十五年后仍然动人",
            "author": "动画漫评人",
            "bvid": "BVreview",
            "aid": 2,
            "pubdate": 100,
            "arcurl": "https://www.bilibili.com/video/BVreview",
        },
    ]

    async def fake_search(_query: str):
        return {"data": {"result": rows}}

    def fake_view(aid, _bvid):
        raw = rows[0] if aid == 1 else rows[1]
        return {"data": {
            **raw,
            "owner": {"name": raw["author"], "mid": aid},
            "stat": {"view": 1000, "danmaku": 10},
        }}

    monkeypatch.setattr(videos_tool, "_bili_search_async", fake_search)
    monkeypatch.setattr(videos_tool, "_sync_bili_view", fake_view)
    result = await SearchBiliSubjectVideosTool().run(BiliSubjectVideosArgs(
        query="轻音少女",
        aliases=["K-ON!"],
        lifecycle="archive",
        limit=5,
    ))
    assert result.ok and result.data is not None
    assert [video.bvid for video in result.data.videos] == ["BVreview"]
    assert result.data.videos[0].role == "retrospective"


@pytest.mark.asyncio
async def test_subject_video_search_promotes_long_public_upload_without_staff_identity(monkeypatch) -> None:
    from otomo.tools.videos import tool as videos_tool

    row = {
        "title": "K-ON轻音少女【台配国语中字】",
        "author": "普通动画收藏UP",
        "bvid": "BVpublic",
        "aid": 9,
        "pubdate": 100,
        "arcurl": "https://www.bilibili.com/video/BVpublic",
    }

    async def fake_search(_query: str):
        return {"data": {"result": [row]}}

    def fake_view(_aid, _bvid):
        return {"data": {
            **row,
            "duration": 24 * 60,
            "copyright": 2,
            "pages": [{"part": "K-ON轻音少女 台配国语中字", "duration": 24 * 60}],
            "owner": {"name": row["author"], "mid": 9},
            "stat": {"view": 1200, "danmaku": 20},
        }}

    monkeypatch.setattr(videos_tool, "_bili_search_async", fake_search)
    monkeypatch.setattr(videos_tool, "_sync_bili_view", fake_view)
    result = await SearchBiliSubjectVideosTool().run(BiliSubjectVideosArgs(
        query="轻音少女",
        aliases=["K-ON!"],
        lifecycle="archive",
        limit=5,
    ))

    assert result.ok and result.data is not None
    assert result.data.watch_candidates[0].role == "public_full_episode"
    assert result.data.watch_candidates[0].uploader_class == "unknown"
    assert result.data.watch_candidates[0].duration_seconds == 24 * 60
    assert result.data.watch_candidates[0].copyright_declaration == "repost"


@pytest.mark.asyncio
async def test_archive_watch_hub_uses_archive_release_strategy(monkeypatch) -> None:
    class FakeClient:
        async def get_subject(self, subject_id: int):
            return {
                "id": subject_id,
                "type": 2,
                "name": "Old Test Anime",
                "name_cn": "老番测试",
                "date": "2006-04-01",
                "platform": "TV",
                "eps": 12,
                "rating": {"score": 8.1, "rank": 100},
                "images": {"common": "https://example.test/cover.jpg"},
                "infobox": [{"key": "播放结束", "value": "2006-06-24"}],
            }

        async def get_subject_persons(self, _subject_id: int):
            return [
                {"name": "测试动画制作会社", "relation": "动画制作"},
                {"name": "无关声优", "relation": "主演"},
            ]

        async def get_episodes(self, _subject_id: int, episode_type=0, limit=12, offset=0):
            return {"data": [
                {"type": episode_type, "duration": "00:05:00", "duration_seconds": 300},
                {"type": episode_type, "duration": "00:05:10", "duration_seconds": 310},
            ][:limit]}

    tool = AnimeWatchHubTool(FakeClient())
    seen: dict[str, object] = {}

    async def watch_run(args):
        return ToolResult(ok=True, data=WhereToWatchResult(
            subject_id=args.subject_id,
            title="老番测试",
            official_sources=[WatchSource(
                label="Bilibili 正版：老番测试",
                url="https://www.bilibili.com/bangumi/play/ss1",
                source="bilibili_verified",
                site="bilibili",
            )],
        ))

    async def release_run(args):
        seen["release_prefer"] = args.prefer
        return ToolResult(ok=True, data=AnimeReleaseFeedsResult(
            subject_id=args.subject_id,
            title="老番测试",
        ))

    async def video_run(args):
        seen["staff_names"] = args.staff_names
        seen["expected_episode_minutes"] = args.expected_episode_minutes
        direct = BiliSubjectVideoMeta(
            title="老番测试 第1话 正片",
            url="https://www.bilibili.com/video/BV1test",
            author="测试动画制作会社",
            role="public_full_episode",
            uploader_class="staff_or_production",
            watch_candidate=True,
        )
        return ToolResult(ok=True, data=BiliSubjectVideosResult(
            query=args.query,
            count=1,
            watch_candidates=[direct],
            videos=[direct],
        ))

    monkeypatch.setattr(tool.watch, "run", watch_run)
    monkeypatch.setattr(tool.release, "run", release_run)
    monkeypatch.setattr(tool.videos, "run", video_run)
    result = await tool.run(AnimeWatchHubArgs(subject_id=42))

    assert result.ok and result.data is not None
    assert result.data.lifecycle.state == "archive"
    assert seen["release_prefer"] == "archive"
    assert seen["staff_names"] == []
    assert seen["expected_episode_minutes"] == 5.08
    assert result.data.staff_signals == ["测试动画制作会社"]
    assert result.data.bilibili is not None
    assert result.data.bilibili.watch_candidates[0].watch_candidate is True
    assert "普通投稿" in result.data.caveats[0]
