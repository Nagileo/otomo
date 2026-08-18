"""Offline tests for guidance, review fusion, spoiler state, and user analysis."""
from __future__ import annotations

import asyncio

import pytest

from otomo.agent._common import summarize
from otomo.agent.contracts import AgentState, ToolResult
from otomo.memory.models import UserMemory
from otomo.tools.comments.tool import EpisodeCommentsArgs, GetEpisodeCommentsTool
from otomo.tools.recommend.tool import RecEvidence, _egs_mapping_confidence, _quality_badges, _review_bonus
from otomo.tools.review.tool import (
    CommentEvidence,
    RatingEvidence,
    ReviewFusionResult,
    _bangumi_signal,
    _build_aspect_summary,
    _consensus,
    _extract_aspect_opinions,
    _format_aspect_summary,
    _pick_aspects,
    _score_signal,
)
from otomo.tools.season.tool import GuideCommentDigest, SeasonGuideBriefResult, _fit_item
from otomo.tools.spoiler.tool import assess_spoiler_policy
from otomo.tools.user_analysis.tool import _build_affinity, _parse_friend_list, _sentiment
from otomo.tools.videos import tool as videos_tool
from otomo.tools.videos.tool import (
    BiliDanmakuItem,
    BiliVideoCommentsResult,
    BiliVideoContentArgs,
    BiliVideoDanmakuResult,
    BiliVideoSubtitleResult,
    BiliSubtitleSegment,
    SummarizeBiliVideoContentTool,
    _clean_bili_title,
    _guide_links,
    _parse_bili_video_ref,
    _summarize_aspect_opinions,
)


def test_find_guide_video_links_prefers_whitelist():
    links = _guide_links("2026年7月新番导视", "season", 3)
    assert len(links) == 3
    assert all("bilibili.com" in x.url for x in links)


def test_spoiler_policy_requires_followup_for_ending_question():
    policy = assess_spoiler_policy("这部最后结局怎么样？")
    assert policy.level == "none"
    assert policy.needs_followup
    assert "结局" in policy.risk_keywords


def test_spoiler_policy_extracts_progress_without_escalating():
    policy = assess_spoiler_policy("我看到第 5 集了，后面别剧透")
    assert policy.level == "none"
    assert policy.progress_episode == 5


def test_runtime_state_is_updated_from_natural_language():
    pytest.importorskip("openai")
    from otomo.agent._common import inject_runtime_state, update_spoiler_state_from_input

    state = AgentState()
    update_spoiler_state_from_input(state, "我看到第 5 集了，后面别剧透")
    messages: list[dict] = []
    inject_runtime_state(messages, state)
    assert state.short_term["spoiler"]["mode"] == "none"
    assert state.short_term["spoiler"]["progress_episode"] == 5
    assert "progress_episode=5" in messages[0]["content"]


def test_memory_spoiler_default_does_not_auto_escalate_turn():
    from otomo.memory.runtime import attach_memory_state

    class FakeClient:
        async def get_me(self):
            return {"username": "spoiler-user"}

    class FakeLtm:
        def load_user(self, username: str):
            return UserMemory(username=username, spoiler_default="full")

    state = AgentState()
    asyncio.run(attach_memory_state(state, FakeClient(), FakeLtm()))
    assert state.short_term["spoiler"]["mode"] == "none"
    assert state.short_term["spoiler"]["memory_default"] == "full"


def test_memory_full_softly_allows_explicit_spoiler_intent_with_warning():
    from otomo.agent._common import update_spoiler_state_from_input

    state = AgentState(short_term={"spoiler": {"mode": "none", "memory_default": "full"}})
    update_spoiler_state_from_input(state, "这部最后结局怎么样？")
    spoiler = state.short_term["spoiler"]
    assert spoiler["mode"] == "full"
    assert spoiler["soft_warning"] is True
    assert spoiler["pending_followup"] is False


def test_explicit_no_spoiler_overrides_memory_full():
    from otomo.agent._common import update_spoiler_state_from_input

    state = AgentState(short_term={"spoiler": {"mode": "none", "memory_default": "full"}})
    update_spoiler_state_from_input(state, "我看到第 5 集了，后面别剧透")
    spoiler = state.short_term["spoiler"]
    assert spoiler["mode"] == "none"
    assert spoiler["progress_episode"] == 5
    assert not spoiler.get("soft_warning")


def test_review_rating_signals_and_consensus():
    assert _bangumi_signal(8.2, 1000, None) == "strong"
    assert _score_signal(82, 1200, 100) == "strong"
    assert _consensus([
        RatingEvidence(source="Bangumi", score=8.1, scale=10, signal="strong"),
        RatingEvidence(source="ErogameScape", score=81, scale=100, signal="strong"),
    ])


def test_season_fit_item_matches_focus_tags():
    fit, matches, reason, fit_score = _fit_item(["百合", "日常"], 7.2, ["百合", "治愈"])
    assert fit == "strong"
    assert matches == ["百合"]
    assert fit_score > 3
    assert reason


def test_recommend_review_bonus_and_badges():
    evidence = [
        RecEvidence(source="Bangumi", score=8.1, scale=10, signal="strong"),
        RecEvidence(source="VNDB", score=79, scale=100, signal="positive"),
    ]
    assert _review_bonus(evidence) > 0
    assert _quality_badges(evidence) == ["Bangumi 8.1/10", "VNDB 79/100"]


def test_strict_egs_to_bangumi_mapping_rejects_neighbor_titles():
    assert _egs_mapping_confidence("ランス10", {"name": "ランス9", "name_cn": "兰斯9"})[0] == 0.0
    assert _egs_mapping_confidence("サクラノ刻", {"name": "サクラノ詩", "name_cn": "樱之诗"})[0] == 0.0
    assert _egs_mapping_confidence("サクラノ刻", {"name": "サクラノ刻", "name_cn": "樱之刻"})[0] == 1.0


def test_review_aspect_extraction():
    praise, criticism = _pick_aspects([
        CommentEvidence(source="Bangumi", samples=["节奏很舒服，治愈好看", "后半有点拖，比较失望"])
    ])
    assert praise and "治愈" in praise[0].points[0]
    assert criticism and any("失望" in p for p in criticism[0].points)


def test_review_detailed_aspect_opinions():
    opinions = _extract_aspect_opinions([
        CommentEvidence(source="Bangumi 短评", samples=["剧情节奏很舒服，角色塑造也治愈好看", "作画后半有点崩，比较失望"])
    ])
    assert any(o.aspect == "story" and o.sentiment in {"positive", "mixed"} for o in opinions)
    assert any(o.aspect == "visual" and o.sentiment in {"negative", "mixed"} for o in opinions)
    summary = _summarize_aspect_opinions(opinions)
    assert summary
    assert any("剧情" in x or "画面" in x for x in summary)


def test_review_aspect_summary_groups_sentiment_and_spoiler_risk():
    opinions = _extract_aspect_opinions([
        CommentEvidence(
            source="Bangumi 短评",
            samples=[
                "剧情展开很精彩，角色塑造也很舒服",
                "剧情后半反转太雷，比较失望",
                "音乐很神，配乐稳定",
            ],
        )
    ])
    summary = _build_aspect_summary(opinions)
    story = next(x for x in summary if x.aspect == "story")
    assert story.total >= 2
    assert story.dominant_sentiment == "mixed"
    assert story.spoiler_risk == "high"
    formatted = _format_aspect_summary(summary)
    assert any("剧情" in x for x in formatted)


def test_trace_summary_prefers_aspect_summary():
    opinions = _extract_aspect_opinions([
        CommentEvidence(source="B站评论", samples=["画面作画很稳，音乐也可以"])
    ])
    res = ToolResult(
        ok=True,
        data=ReviewFusionResult(
            subject_id=1,
            title="占位",
            subject_type="anime",
            spoiler_level="none",
            aspect_opinions=opinions,
            aspect_summary=_build_aspect_summary(opinions),
        ),
    )
    assert summarize(res).startswith("方面摘要：")


def test_season_guide_comment_digest_summary():
    res = ToolResult(
        ok=True,
        data=SeasonGuideBriefResult(
            season="2026 年 7 月（夏）番",
            count=5,
            guide_comment_digests=[
                GuideCommentDigest(
                    video_title="2026年7月新番导视",
                    author="名作之壁吧",
                    url="https://www.bilibili.com/video/BVtest",
                    aid=1,
                    count=20,
                    opinion_summary=["整体观感：正向 × 3", "剧情：分歧 × 1"],
                )
            ],
        ),
    )
    assert "导视评论 1 个视频" in summarize(res)
    assert "整体观感" in summarize(res)


def test_peer_affinity_detects_sync_and_disagreement():
    def row(sid: int, name: str, rate: int) -> dict:
        return {"rate": rate, "subject": {"id": sid, "name": name, "images": {}}}

    own = [row(1, "A", 9), row(2, "B", 8), row(3, "C", 3), row(4, "D", 2)]
    peer = [row(1, "A", 10), row(2, "B", 8), row(3, "C", 2), row(4, "D", 9)]
    affinity = _build_affinity("peer", own, peer)
    assert affinity.common_rated == 4
    assert affinity.rating_similarity > 0
    assert affinity.collection_similarity > 0
    assert affinity.user_space_similarity > 0
    assert affinity.extreme_similarity > 0
    assert affinity.liked_together[0].name == "A"
    assert affinity.biggest_disagreements[0].name == "D"


def test_peer_affinity_uses_unrated_collection_space():
    def row(sid: int, name: str, rate: int = 0) -> dict:
        return {"rate": rate, "subject": {"id": sid, "name": name, "images": {}}}

    own = [row(1, "A", 0), row(2, "B", 0), row(3, "C", 9)]
    peer = [row(1, "A", 0), row(2, "B", 0), row(4, "D", 9)]
    affinity = _build_affinity("peer", own, peer)
    assert affinity.common_rated == 0
    assert affinity.common_collections == 2
    assert affinity.rating_similarity == 0
    assert affinity.collection_similarity > 0
    assert affinity.peer_weight > 0
    assert affinity.confidence == "low"


def test_peer_affinity_confidence_penalizes_collection_gap():
    def row(sid: int, name: str, rate: int = 0) -> dict:
        return {"rate": rate, "subject": {"id": sid, "name": name, "images": {}}}

    own = [row(i, f"A{i}", 8 if i <= 6 else 0) for i in range(1, 31)]
    peer = [row(i, f"A{i}", 8 if i <= 6 else 0) for i in range(1, 7)]
    affinity = _build_affinity("peer", own, peer)
    assert affinity.common_rated == 6
    assert affinity.collection_size_ratio < 0.5
    assert any("收藏量" in x for x in affinity.confidence_reasons)


def test_episode_comments_blocks_future_episode():
    tool = GetEpisodeCommentsTool()
    res = asyncio.run(tool.run(EpisodeCommentsArgs(ep_id=1, episode_sort=8, max_episode_sort=5)))
    assert res.ok
    assert res.data and res.data.blocked_by_spoiler
    assert res.data.comments == []


def test_bili_title_cleaner_friend_parser_and_sentiment():
    assert _clean_bili_title('<em class="keyword">新番</em>导视') == "新番导视"
    assert _parse_bili_video_ref("https://www.bilibili.com/video/BV1abcDEF23x/")[1] == "BV1abcDEF23x"
    assert _parse_bili_video_ref("https://www.bilibili.com/video/av123456")[0] == 123456
    assert _sentiment("节奏太拖，比较失望") < 0
    friends = _parse_friend_list(
        '<ul id="memberUserList"><li><a href="/user/alice" class="avatar">'
        '<span style="background-image:url(\'//lain.bgm.tv/pic/user/l/1.jpg\')"></span>Alice</a></li></ul>',
        10,
    )
    assert friends[0].username == "alice"
    assert friends[0].avatar_url == "https://lain.bgm.tv/pic/user/l/1.jpg"


def test_bili_relevance_rejects_whitelist_wrong_title_and_accepts_exact_non_whitelist():
    from otomo.tools.videos.tool import _hit_relevance

    wrong_score, wrong_reason = _hit_relevance(
        {"title": "十年前的经典动画闲聊", "author": "泛式"},
        up_name="泛式", aliases=["孤独摇滚"], tags=[], season_query="孤独摇滚",
    )
    exact_score, exact_reason = _hit_relevance(
        {"title": "《孤独摇滚》完整漫评：为什么它打动人", "author": "认真漫评人"},
        up_name="", aliases=["孤独摇滚"], tags=[], season_query="孤独摇滚",
    )

    assert wrong_score < 0.58
    assert "不能证明" in wrong_reason
    assert exact_score >= 0.58
    assert "标题明确命中" in exact_reason

    short_title_score, _ = _hit_relevance(
        {"title": "迷宫饭 深度漫评", "author": "认真漫评人"},
        up_name="", aliases=["迷宫饭"], tags=[], season_query="迷宫饭",
    )
    assert short_title_score >= 0.58


def test_bili_relevance_downranks_stale_season_and_rejects_sequel_mismatch():
    from datetime import datetime, timezone
    from otomo.tools.videos.tool import _hit_relevance

    fresh = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    stale = int(datetime(2022, 6, 1, tzinfo=timezone.utc).timestamp())
    base = {"title": "2026年7月新番导视", "author": "普通UP"}
    fresh_score, _ = _hit_relevance(
        {**base, "pubdate": fresh}, up_name="", aliases=["2026年7月新番导视"],
        tags=[], season_query="2026年7月新番导视",
    )
    stale_score, stale_reason = _hit_relevance(
        {**base, "pubdate": stale}, up_name="", aliases=["2026年7月新番导视"],
        tags=[], season_query="2026年7月新番导视",
    )
    mismatch_score, mismatch_reason = _hit_relevance(
        {"title": "测试作品 第二季 漫评", "author": "泛式"}, up_name="泛式",
        aliases=["测试作品 第三季"], tags=[], season_query="测试作品 第三季",
    )

    assert fresh_score > stale_score
    assert "发布时间偏旧" in stale_reason
    assert mismatch_score < 0.58
    assert "季度/续作编号冲突" in mismatch_reason


@pytest.mark.asyncio
async def test_bili_balanced_search_accepts_verified_non_whitelist(monkeypatch):
    from otomo.tools.videos import tool as videos

    async def fake_search(_query):
        return {"data": {"result": [
            {"title": "孤独摇滚 深度漫评", "author": "新锐漫评", "aid": 10,
             "bvid": "BV1abcDEF23x", "arcurl": "https://www.bilibili.com/video/BV1abcDEF23x"},
            {"title": "别的动画盘点", "author": "泛式", "aid": 11,
             "bvid": "BV1abcDEF24y", "arcurl": "https://www.bilibili.com/video/BV1abcDEF24y"},
        ]}}

    def fake_view(aid, bvid):
        title = "孤独摇滚 深度漫评" if aid == 10 else "别的动画盘点"
        author = "新锐漫评" if aid == 10 else "泛式"
        return {"data": {"aid": aid, "bvid": bvid, "title": title,
                         "owner": {"name": author}, "stat": {"view": 1000}}}

    monkeypatch.setattr(videos, "_bili_search_async", fake_search)
    monkeypatch.setattr(videos, "_sync_bili_view", fake_view)
    result = await videos.SearchBiliGuideVideosTool().run(
        videos.BiliGuideSearchArgs(query="孤独摇滚", limit=5),
    )

    assert result.ok and result.data
    assert [item.author for item in result.data.videos] == ["新锐漫评"]
    assert result.data.videos[0].verified is True
    assert any(item.author == "泛式" for item in result.data.rejected)


def test_asr_worker_only_accepts_bilibili_video_urls():
    from fastapi import HTTPException
    from otomo.asr_worker import _validate_bilibili_url

    assert _validate_bilibili_url(
        "https://www.bilibili.com/video/BV1abcDEF23x"
    ).endswith("BV1abcDEF23x")
    with pytest.raises(HTTPException):
        _validate_bilibili_url("https://example.com/video/BV1abcDEF23x")
    with pytest.raises(HTTPException):
        _validate_bilibili_url("https://www.bilibili.com/read/cv1")


def test_bili_video_content_aggregates_public_layers(monkeypatch):
    def fake_view(_aid, _bvid):
        return {
            "data": {
                "aid": 123,
                "bvid": "BV1abcDEF23x",
                "cid": 456,
                "title": "2026年7月新番导视",
                "desc": "本期聊夏季番。",
                "owner": {"name": "名作之壁吧"},
                "stat": {"view": 1000, "danmaku": 30},
            }
        }

    async def fake_subtitles(self, args):
        return ToolResult(
            ok=True,
            data=BiliVideoSubtitleResult(
                aid=123,
                bvid="BV1abcDEF23x",
                cid=456,
                count=2,
                segments=[
                    BiliSubtitleSegment(start=1.0, end=3.0, text="第一部推荐摇曳露营"),
                    BiliSubtitleSegment(start=4.0, end=6.0, text="第二部是百合日常"),
                ],
                rough_summary=["第一部推荐摇曳露营 第二部是百合日常"],
                caveats=["字幕是公开 ASR"],
            ),
        )

    async def fake_danmaku(self, args):
        return ToolResult(
            ok=True,
            data=BiliVideoDanmakuResult(
                aid=123,
                bvid="BV1abcDEF23x",
                cid=456,
                count=2,
                danmaku=[BiliDanmakuItem(time=2.0, text="期待"), BiliDanmakuItem(time=5.0, text="百合好")],
                opinion_summary=["整体观感：正向 × 2"],
                caveats=["弹幕是话语源"],
            ),
        )

    async def fake_comments(self, args):
        return ToolResult(
            ok=True,
            data=BiliVideoCommentsResult(
                aid=123,
                count=1,
                comments=["这季度可以追"],
                opinion_summary=["整体观感：正向 × 1"],
                source_url="https://www.bilibili.com/video/av123",
                caveats=["评论是话语源"],
            ),
        )

    monkeypatch.setattr(videos_tool, "_sync_bili_view", fake_view)
    monkeypatch.setattr(videos_tool.GetBiliVideoSubtitlesTool, "run", fake_subtitles)
    monkeypatch.setattr(videos_tool.GetBiliVideoDanmakuTool, "run", fake_danmaku)
    monkeypatch.setattr(videos_tool.GetBiliVideoCommentsTool, "run", fake_comments)

    res = asyncio.run(SummarizeBiliVideoContentTool().run(BiliVideoContentArgs(url="https://www.bilibili.com/video/BV1abcDEF23x")))
    assert res.ok and res.data
    assert res.data.access_level == "multi"
    assert "subtitle" in res.data.read_layers
    assert "danmaku" in res.data.read_layers
    assert "comments" in res.data.read_layers
    assert "metadata" in res.data.read_layers
    assert "摇曳露营" in res.data.content_summary[0]
    assert res.data.audience_summary
