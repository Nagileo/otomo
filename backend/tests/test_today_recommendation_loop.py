from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from otomo.memory import LongTermMemory
from otomo.recommendation_events import (
    RecommendationEventStore,
    RecommendationFeedbackRequest,
    record_recommendation_feedback,
)
from otomo.recsys_registry import CFModelRegistry
from otomo.today import TodayCockpitService, TodayPreferenceStore
from otomo.agent.contracts import ToolResult
from otomo.factory import build_registry
from otomo.tools.bangumi.client import BangumiClient
from otomo.tools.calendar.tool import (
    AiringProgressItem,
    AiringProgressResult,
    BroadcastCalendarDay,
    BroadcastCalendarItem,
    BroadcastCalendarResult,
)
from otomo.tools.recommend.tool import _mmr_rerank


def test_today_preferences_are_separate_and_reversible(tmp_path):
    store = TodayPreferenceStore(str(tmp_path / "today.sqlite3"))
    hidden = store.update("alice", 42, hidden_this_season=True, pinned=True)
    assert hidden.hidden_this_season is True
    assert hidden.hidden_season
    assert store.list("alice")[42].pinned is True
    assert store.list("bob") == {}

    restored = store.update("alice", 42, hidden_this_season=False)
    assert restored.hidden_this_season is False
    assert restored.hidden_season == ""
    assert restored.pinned is True


def test_recommendation_events_enforce_owner_and_dedupe_impressions(tmp_path):
    store = RecommendationEventStore(str(tmp_path / "events.sqlite3"))
    set_id = store.create_set(
        "alice", "anime", "general", {"limit": 2},
        [{"id": 1, "name": "A", "score": 1.0}, {"id": 2, "name": "B", "score": 0.5}],
    )
    request = RecommendationFeedbackRequest(
        recommendation_set_id=set_id, subject_id=1, event="impression",
    )
    assert store.record("alice", request)["recorded"] is True
    assert store.record("alice", request)["deduplicated"] is True
    preference = RecommendationFeedbackRequest(
        recommendation_set_id=set_id, subject_id=1, event="more",
    )
    assert store.record("alice", preference)["recorded"] is True
    assert store.record("alice", preference)["deduplicated"] is True
    negative = RecommendationFeedbackRequest(
        recommendation_set_id=set_id, subject_id=1, event="less",
    )
    assert store.record("alice", negative)["recorded"] is True
    assert store.recent_excluded_ids("alice") == {1}
    assert store.record("alice", preference)["recorded"] is True
    assert store.recent_excluded_ids("alice") == set()
    with pytest.raises(PermissionError):
        store.record("bob", request)
    assert store.get_set(set_id, "alice")["items"][0]["name"] == "A"
    assert store.get_set(set_id, "bob") is None


def test_temporary_dismissal_expires_before_durable_dismissal(tmp_path):
    store = RecommendationEventStore(str(tmp_path / "events.sqlite3"))
    set_id = store.create_set(
        "alice", "anime", "general", {},
        [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
    )
    store.record("alice", RecommendationFeedbackRequest(
        recommendation_set_id=set_id, subject_id=1, event="dismiss", reason="temporary",
    ))
    store.record("alice", RecommendationFeedbackRequest(
        recommendation_set_id=set_id, subject_id=2, event="dismiss", reason="genre",
    ))
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with store._connect() as conn:
        conn.execute("UPDATE recommendation_events SET created_at=? WHERE subject_id=1", (old,))
    assert store.recent_excluded_ids("alice") == {2}


def test_recommendation_feedback_service_shares_memory_across_clients(tmp_path):
    store = RecommendationEventStore(str(tmp_path / "events.sqlite3"))
    ltm = LongTermMemory(tmp_path / "memory")
    set_id = store.create_set("alice", "anime", "general", {}, [{"id": 7, "name": "ARIA"}])

    result = record_recommendation_feedback(
        store,
        ltm,
        "alice",
        RecommendationFeedbackRequest(recommendation_set_id=set_id, subject_id=7, event="watched"),
        channel="discord",
    )

    assert result["recorded"] is True
    memory = ltm.load_user("alice")
    assert memory.feedback[-1].subject_id == 7
    assert memory.feedback[-1].signal == "like"
    assert memory.feedback[-1].note == "recommendation_card:discord:watched"


def test_scoped_feedback_can_be_undone_without_generalizing_all_tags(tmp_path):
    store = RecommendationEventStore(str(tmp_path / "events.sqlite3"))
    ltm = LongTermMemory(tmp_path / "memory")
    set_id = store.create_set("alice", "anime", "general", {}, [{"id": 9, "name": "测试番"}])

    record_recommendation_feedback(
        store,
        ltm,
        "alice",
        RecommendationFeedbackRequest(
            recommendation_set_id=set_id,
            subject_id=9,
            event="less",
            aspect="genre",
        ),
    )
    memory = ltm.load_user("alice")
    assert memory.feedback[-1].scope == "genre"
    assert store.recent_excluded_ids("alice") == {9}

    record_recommendation_feedback(
        store,
        ltm,
        "alice",
        RecommendationFeedbackRequest(
            recommendation_set_id=set_id,
            subject_id=9,
            event="undo",
        ),
    )
    assert ltm.load_user("alice").feedback == []
    assert store.recent_excluded_ids("alice") == set()


def test_metrics_count_only_recorded_visible_impressions(tmp_path):
    store = RecommendationEventStore(str(tmp_path / "events.sqlite3"))
    set_id = store.create_set(
        "alice",
        "anime",
        "tonight",
        {"_model_metadata": {"version": "v1"}},
        [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
    )
    visible = RecommendationFeedbackRequest(
        recommendation_set_id=set_id,
        subject_id=1,
        event="impression",
        note="visible_1200ms",
    )
    store.record("alice", visible)
    store.record("alice", visible)
    metrics = store.metrics("alice")

    assert metrics["visible_impressions"] == 1
    assert metrics["unique_visible_sets"] == 1
    assert metrics["unique_visible_items"] == 1
    assert metrics["segments"] == [{
        "subject_type": "anime", "scenario": "tonight", "impressions": 1,
    }]
    assert metrics["model_versions"] == {"v1": 1}


def test_cf_registry_reports_stale_and_missing_models(tmp_path):
    built = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    (tmp_path / "i2i_anime.json").write_text(json.dumps({
        "meta": {
            "model": "bm25",
            "built_at": built,
            "n_users": 10,
            "n_items": 1,
            "weighting_version": "bangumi-weighted-v2",
            "quality_gate_passed": True,
            "baseline_ndcg_at_10": 0.12,
            "selected_ndcg_at_10": 0.16,
            "relative_lift": 0.333333,
        },
        "items": {"1": [[2, 0.5]]},
    }), encoding="utf-8")
    registry = CFModelRegistry(str(tmp_path), max_age_days=45)
    payload, status = registry.load("anime")
    assert payload["items"]["1"][0][0] == 2
    assert status.available is True
    assert status.stale is True
    assert status.weighting_version == "bangumi-weighted-v2"
    assert status.quality_gate_passed is True
    assert status.baseline_ndcg_at_10 == 0.12
    assert status.selected_ndcg_at_10 == 0.16
    assert status.relative_lift == 0.333333
    assert status.warnings
    assert registry.load("music")[1].available is False


def test_mmr_promotes_a_distinct_candidate():
    candidates = [
        (1, {"name": "A", "tags": {"日常", "治愈"}, "graph": set(), "rank_score": 1.0}),
        (2, {"name": "B", "tags": {"日常", "治愈"}, "graph": set(), "rank_score": 0.99}),
        (3, {"name": "C", "tags": {"悬疑", "科幻"}, "graph": set(), "rank_score": 0.95}),
    ]
    ranked = _mmr_rerank(candidates, lambda _sid, item: item["rank_score"], 0.35)
    assert [sid for sid, _item in ranked[:2]] == [1, 3]


def test_today_cockpit_is_registered_as_the_shared_chat_entry():
    registry = build_registry(BangumiClient(token=""))
    assert registry.get("today_cockpit") is not None


@pytest.mark.asyncio
async def test_today_cockpit_runs_and_fails_closed_to_user_progress(tmp_path, monkeypatch):
    weekday = datetime.now(ZoneInfo("Asia/Shanghai")).date().weekday() + 1
    calendar = BroadcastCalendarResult(
        scope="week",
        today=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        only_mine=False,
        days=[BroadcastCalendarDay(
            weekday_id=weekday,
            weekday_cn="今天",
            is_today=True,
            items=[
                BroadcastCalendarItem(id=1, name="Mine", name_cn="我的番", url="https://bgm.tv/subject/1"),
                BroadcastCalendarItem(id=2, name="Public", name_cn="全量番", url="https://bgm.tv/subject/2"),
            ],
        )],
    )
    progress = AiringProgressResult(
        username="alice",
        today=calendar.today,
        items=[AiringProgressItem(
            id=1, name="我的番", status="watching", status_label="在看",
            my_ep=2, aired_ep=3, behind=1, action="继续看第 3 集",
            url="https://bgm.tv/subject/1",
        )],
    )

    async def calendar_run(_self, _args):
        return ToolResult(ok=True, data=calendar)

    async def progress_run(_self, _args):
        return ToolResult(ok=True, data=progress)

    monkeypatch.setattr("otomo.today.BroadcastCalendarTool.run", calendar_run)
    monkeypatch.setattr("otomo.today.AiringProgressTool.run", progress_run)
    result = await TodayCockpitService(
        BangumiClient(token=""), TodayPreferenceStore(str(tmp_path / "today.sqlite3")),
    ).build("alice")

    assert [item.id for item in result.today] == [1]
    assert result.today[0].behind == 1
    assert [item.id for item in result.backlog] == [1]
    assert result.week[0].weekday_cn == "今天"


@pytest.mark.asyncio
async def test_today_cockpit_keeps_personal_calendar_when_progress_is_partial(tmp_path, monkeypatch):
    weekday = datetime.now(ZoneInfo("Asia/Shanghai")).date().weekday() + 1
    calendar = BroadcastCalendarResult(
        scope="week",
        today=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        only_mine=True,
        username="alice",
        days=[BroadcastCalendarDay(
            weekday_id=weekday,
            weekday_cn="今天",
            is_today=True,
            items=[BroadcastCalendarItem(
                id=7, name="Personal", name_cn="个人日历条目",
                my_collection="watching", my_collection_label="在看",
                url="https://bgm.tv/subject/7",
            )],
        )],
    )

    async def calendar_run(_self, _args):
        return ToolResult(ok=True, data=calendar)

    async def progress_run(_self, _args):
        return ToolResult(ok=False, error="temporary upstream failure")

    monkeypatch.setattr("otomo.today.BroadcastCalendarTool.run", calendar_run)
    monkeypatch.setattr("otomo.today.AiringProgressTool.run", progress_run)
    result = await TodayCockpitService(
        BangumiClient(token=""), TodayPreferenceStore(str(tmp_path / "today.sqlite3")),
    ).build("alice")

    assert [item.id for item in result.today] == [7]
    assert result.today[0].collection_status == "watching"
    assert any("分集进度" in note for note in result.notes)
