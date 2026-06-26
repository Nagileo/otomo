"""Offline tests for guidance, review fusion, spoiler state, and user analysis."""
from __future__ import annotations

import asyncio

import pytest

from otomo.agent._common import summarize
from otomo.agent.contracts import AgentState, ToolResult
from otomo.tools.comments.tool import EpisodeCommentsArgs, GetEpisodeCommentsTool
from otomo.tools.recommend.tool import RecEvidence, _egs_mapping_confidence, _quality_badges, _review_bonus
from otomo.tools.review.tool import (
    CommentEvidence,
    RatingEvidence,
    _bangumi_signal,
    _consensus,
    _extract_aspect_opinions,
    _pick_aspects,
    _score_signal,
)
from otomo.tools.season.tool import GuideCommentDigest, SeasonGuideBriefResult, _fit_item
from otomo.tools.spoiler.tool import assess_spoiler_policy
from otomo.tools.user_analysis.tool import _build_affinity, _parse_friend_list, _sentiment
from otomo.tools.videos.tool import _clean_bili_title, _guide_links, _summarize_aspect_opinions


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
    assert affinity.liked_together[0].name == "A"
    assert affinity.biggest_disagreements[0].name == "D"


def test_episode_comments_blocks_future_episode():
    tool = GetEpisodeCommentsTool()
    res = asyncio.run(tool.run(EpisodeCommentsArgs(ep_id=1, episode_sort=8, max_episode_sort=5)))
    assert res.ok
    assert res.data and res.data.blocked_by_spoiler
    assert res.data.comments == []


def test_bili_title_cleaner_friend_parser_and_sentiment():
    assert _clean_bili_title('<em class="keyword">新番</em>导视') == "新番导视"
    assert _sentiment("节奏太拖，比较失望") < 0
    friends = _parse_friend_list(
        '<ul id="memberUserList"><li><a href="/user/alice" class="avatar">Alice</a></li></ul>',
        10,
    )
    assert friends[0].username == "alice"
