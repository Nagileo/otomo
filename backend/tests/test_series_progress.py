from __future__ import annotations

import asyncio

from otomo.tools.recommend.tool import RecommendTool
from otomo.tools.series_progress import (
    SeriesProgressArgs,
    SeriesRelationMemo,
    SeriesProgressService,
    collection_map,
    inspect_series_candidate,
)
from otomo.tools.watchorder.tool import WatchCopilotArgs, WatchCopilotTool
from otomo.series_overrides import (
    SeriesOverrideMember,
    SeriesOverrideRule,
    SeriesOverrideStore,
)


class FakeSeriesBangumi:
    subjects = {
        1: {"id": 1, "type": 2, "name_cn": "测试动画 第一季", "date": "2020-01-01", "eps": 12, "images": {"common": "1.jpg"}},
        2: {"id": 2, "type": 2, "name_cn": "测试动画 第二季", "date": "2021-01-01", "eps": 12, "images": {"common": "2.jpg"}},
        3: {"id": 3, "type": 2, "name_cn": "测试动画 第三季", "date": "2022-01-01", "eps": 12, "images": {"common": "3.jpg"}},
        4: {"id": 4, "type": 2, "name_cn": "测试动画 OVA", "date": "2020-06-01", "eps": 1},
    }
    relations = {
        1: [
            {"id": 2, "type": 2, "relation": "续集", "name_cn": "测试动画 第二季", "date": "2021-01-01"},
            {"id": 4, "type": 2, "relation": "番外篇", "name_cn": "测试动画 OVA", "date": "2020-06-01"},
        ],
        2: [
            {"id": 1, "type": 2, "relation": "前传", "name_cn": "测试动画 第一季", "date": "2020-01-01"},
            {"id": 3, "type": 2, "relation": "续集", "name_cn": "测试动画 第三季", "date": "2022-01-01"},
        ],
        3: [{"id": 2, "type": 2, "relation": "前传", "name_cn": "测试动画 第二季", "date": "2021-01-01"}],
        4: [{"id": 1, "type": 2, "relation": "主线故事", "name_cn": "测试动画 第一季", "date": "2020-01-01"}],
    }

    def __init__(self, rows):
        self.rows = rows

    async def get_me(self):
        return {"username": "alice"}

    async def get_subject(self, subject_id):
        return dict(self.subjects[subject_id])

    async def get_subject_relations(self, subject_id):
        return list(self.relations.get(subject_id, []))

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=1200):
        return list(self.rows)

    async def search_subjects(self, keyword, subject_type=None, limit=1, **_kwargs):
        return {"data": [dict(self.subjects[3])]}


def collection(subject_id: int, ctype: int, ep_status: int = 0):
    return {
        "type": ctype,
        "ep_status": ep_status,
        "subject": dict(FakeSeriesBangumi.subjects[subject_id]),
    }


def test_first_watched_second_missing_points_to_second_and_blocks_third():
    client = FakeSeriesBangumi([collection(1, 2, 12)])
    result = asyncio.run(SeriesProgressService(client).build(SeriesProgressArgs(subject_id=3, username="alice")))
    assert result is not None
    assert [item.id for item in result.mainline] == [1, 2, 3]
    assert result.next_unwatched is not None
    assert result.next_unwatched.id == 2
    third = next(item for item in result.mainline if item.id == 3)
    assert third.prerequisites_satisfied is False
    assert third.blocked_by == [2]
    assert result.completed_required == 1
    assert result.total_required == 3


def test_episode_completion_unlocks_third_without_silently_changing_collection_state():
    rows = [collection(1, 2, 12), collection(2, 3, 12)]
    result = asyncio.run(SeriesProgressService(FakeSeriesBangumi(rows)).build(
        SeriesProgressArgs(subject_id=3, username="alice")
    ))
    assert result is not None
    second = next(item for item in result.mainline if item.id == 2)
    assert second.collection_state == "watching"
    assert second.completed is True
    assert "尚未改成看过" in second.completion_source
    assert result.next_unwatched is not None
    assert result.next_unwatched.id == 3


def test_dropped_predecessor_is_not_completion_and_does_not_unlock_sequel():
    rows = [collection(1, 2, 12), collection(2, 5, 4)]
    result = asyncio.run(SeriesProgressService(FakeSeriesBangumi(rows)).build(
        SeriesProgressArgs(subject_id=3, username="alice")
    ))
    assert result is not None
    assert result.next_unwatched is not None
    assert result.next_unwatched.id == 2
    assert result.next_unwatched.collection_state == "dropped"
    assert "不会自动放行" in result.next_unwatched.action


def test_optional_side_story_does_not_block_mainline():
    rows = [collection(1, 2, 12), collection(2, 2, 12)]
    result = asyncio.run(SeriesProgressService(FakeSeriesBangumi(rows)).build(
        SeriesProgressArgs(subject_id=3, username="alice")
    ))
    assert result is not None
    assert [item.id for item in result.optional] == [4]
    assert result.next_unwatched is not None
    assert result.next_unwatched.id == 3


def test_manual_override_replaces_wrong_relation_edges_and_skips_recap(tmp_path):
    store = SeriesOverrideStore(tmp_path / "series.json")
    store.upsert(SeriesOverrideRule(
        id="complex-test",
        title="复杂测试系列",
        mainline=[
            SeriesOverrideMember(subject_id=1, name="第一季"),
            SeriesOverrideMember(subject_id=4, name="总集篇", necessity="skip"),
            SeriesOverrideMember(subject_id=3, name="正确下一部"),
        ],
        optional=[SeriesOverrideMember(subject_id=2, name="另一条线", necessity="optional")],
        notes=["管理员确认第二季关系边不属于这条主线。"],
    ))
    client = FakeSeriesBangumi([collection(1, 2, 12)])
    result = asyncio.run(SeriesProgressService(client, store).build(
        SeriesProgressArgs(subject_id=3, username="alice")
    ))
    assert result is not None
    assert result.order_source == "manual_override"
    assert result.override_id == "complex-test"
    assert [item.id for item in result.mainline] == [1, 4, 3]
    assert result.mainline[1].necessity == "skip"
    assert result.next_unwatched is not None
    assert result.next_unwatched.id == 3
    assert result.mainline[2].prerequisite_ids == [1]
    assert [item.id for item in result.optional] == [2]


def test_candidate_audit_uses_same_manual_override_as_progress_service(tmp_path):
    store = SeriesOverrideStore(tmp_path / "series.json")
    store.upsert(SeriesOverrideRule(
        id="candidate-test",
        title="候选校正规则",
        mainline=[
            SeriesOverrideMember(subject_id=1, name="必要前作"),
            SeriesOverrideMember(subject_id=4, name="不阻塞总集篇", necessity="skip"),
            SeriesOverrideMember(subject_id=3, name="候选续作"),
        ],
    ))
    client = FakeSeriesBangumi([])
    status = asyncio.run(inspect_series_candidate(
        client,
        3,
        {},
        collection_available=True,
        subject_name="候选续作",
        override_store=store,
    ))
    assert status.order_source == "manual_override"
    assert status.predecessor_ids == [1]
    assert [item["id"] for item in status.missing_predecessors] == [1]
    assert status.next_subject_id == 1
    assert status.prerequisites_satisfied is False


def test_lightweight_candidate_audit_requires_every_predecessor():
    client = FakeSeriesBangumi([collection(1, 2, 12)])
    status = asyncio.run(inspect_series_candidate(
        client,
        3,
        collection_map(client.rows),
        collection_available=True,
        subject_name="测试动画 第三季",
    ))
    assert status.is_sequel is True
    assert status.prerequisites_satisfied is False
    assert status.next_subject_id == 2
    assert [item["id"] for item in status.missing_predecessors] == [2]


def test_recommender_context_replaces_third_with_first_missing_required_season():
    client = FakeSeriesBangumi([collection(1, 2, 12)])
    context = asyncio.run(RecommendTool(client)._series_context(
        3, 2, collection_map(client.rows), True
    ))
    assert context["seen_predecessor"] is True
    assert context["all_predecessors_completed"] is False
    assert context["next_required"][0] == 2
    assert [item["id"] for item in context["missing_predecessors"]] == [2]


def test_relation_memo_coalesces_concurrent_predecessor_walks():
    class CountingClient(FakeSeriesBangumi):
        def __init__(self, rows):
            super().__init__(rows)
            self.calls: dict[int, int] = {}

        async def get_subject_relations(self, subject_id):
            self.calls[subject_id] = self.calls.get(subject_id, 0) + 1
            await asyncio.sleep(0.01)
            return await super().get_subject_relations(subject_id)

    async def run_audits():
        client = CountingClient([collection(1, 2, 12)])
        memo = SeriesRelationMemo(client)
        await asyncio.gather(*[
            inspect_series_candidate(
                client,
                3,
                collection_map(client.rows),
                collection_available=True,
                relation_memo=memo,
            )
            for _ in range(3)
        ])
        return client.calls

    assert asyncio.run(run_audits()) == {3: 1, 2: 1, 1: 1}


def test_watch_copilot_surfaces_only_the_direct_unwatched_next_season():
    class CopilotClient(FakeSeriesBangumi):
        async def get_all_user_collections(
            self, username, subject_type=2, collection_type=None, max_items=1200,
        ):
            if collection_type == 2:
                return [collection(1, 2, 12)]
            return []

    result = asyncio.run(WatchCopilotTool(CopilotClient([])).run(
        WatchCopilotArgs(username="alice", limit=5)
    ))
    assert result.ok and result.data is not None
    assert [item.id for item in result.data.continue_series] == [2]
    assert result.data.continue_series[0].status == "续作可开"
    assert "必要前作已完成" in result.data.continue_series[0].action
