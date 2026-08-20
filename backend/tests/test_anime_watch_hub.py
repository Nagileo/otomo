from __future__ import annotations

import pytest

from otomo.agent.contracts import ToolResult
from otomo.tools.product_loop.tool import AnimeWatchHubArgs, AnimeWatchHubTool
from otomo.tools.release.tool import AnimeReleaseFeedsResult
from otomo.tools.videos.tool import (
    BiliSubjectVideoMeta,
    BiliSubjectVideosResult,
    BiliSubjectVideosArgs,
    SearchBiliSubjectVideosTool,
    classify_subject_video,
)
from otomo.tools.watch.tool import WatchSource, WhereToWatchResult


def test_staff_upload_is_a_separate_watch_candidate() -> None:
    role, uploader, watch_candidate, evidence, caution = classify_subject_video(
        "《测试动画》第1话 正片",
        "测试动画制作委员会",
        "本作品第一话限时公开",
        staff_names=["测试动画制作委员会"],
    )
    assert role == "staff_uploaded_episode"
    assert uploader == "staff_or_production"
    assert watch_candidate is True
    assert evidence
    assert "不等同" in caution


def test_self_claimed_full_episode_is_not_promoted_to_reliable_watch() -> None:
    role, uploader, watch_candidate, _evidence, caution = classify_subject_video(
        "《测试动画》第1话 完整版",
        "某搬运官方号",
        "",
        staff_names=["真实制作公司"],
    )
    assert role == "episode_candidate"
    assert uploader == "self_claimed_official"
    assert watch_candidate is False
    assert "未确认" in caution


def test_missing_uploader_never_matches_staff_identity() -> None:
    role, uploader, watch_candidate, _evidence, _caution = classify_subject_video(
        "《测试动画》第1话 正片",
        "",
        "",
        staff_names=["测试动画制作委员会"],
    )
    assert role == "episode_candidate"
    assert uploader == "unknown"
    assert watch_candidate is False


def test_review_is_not_mixed_with_full_episode() -> None:
    role, uploader, watch_candidate, _evidence, _caution = classify_subject_video(
        "《测试动画》首集漫评：作画很好但节奏很怪",
        "普通漫评UP",
        staff_names=[],
    )
    assert role == "review"
    assert uploader == "creator"
    assert watch_candidate is False


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
        direct = BiliSubjectVideoMeta(
            title="老番测试 第1话 正片",
            url="https://www.bilibili.com/video/BV1test",
            author="测试动画制作会社",
            role="staff_uploaded_episode",
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
    assert seen["staff_names"] == ["测试动画制作会社"]
    assert result.data.bilibili is not None
    assert result.data.bilibili.watch_candidates[0].watch_candidate is True
    assert "普通视频稿件" in result.data.caveats[0]
