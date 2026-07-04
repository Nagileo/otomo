from __future__ import annotations

import asyncio

from otomo.tools.calendar.tool import (
    AiringProgressArgs,
    AiringProgressTool,
    BroadcastCalendarArgs,
    BroadcastCalendarTool,
)


class FakeBangumi:
    async def get_me(self):
        return {"username": "alice"}

    async def get_calendar(self):
        return [
            {
                "weekday": {"id": 6, "cn": "星期六"},
                "items": [
                    {
                        "id": 100,
                        "name": "A",
                        "name_cn": "动画A",
                        "air_date": "2026-07-04",
                        "air_weekday": 6,
                        "rating": {"score": 8.1, "rank": 100},
                        "images": {"common": "a.jpg"},
                        "collection": {"doing": 123},
                    },
                    {
                        "id": 200,
                        "name": "B",
                        "name_cn": "动画B",
                        "air_date": "2026-07-04",
                        "air_weekday": 6,
                    },
                ],
            }
        ]

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        if collection_type == 3:
            return [
                {
                    "ep_status": 2,
                    "subject": {
                        "id": 100,
                        "name_cn": "动画A",
                        "eps": 12,
                        "images": {"common": "a.jpg"},
                        "rating": {"score": 8.1},
                    },
                }
            ]
        return []

    async def get_episodes(self, subject_id, ep_type=None, limit=100, offset=0):
        return {
            "data": [
                {"sort": 1, "airdate": "2026-07-01"},
                {"sort": 2, "airdate": "2026-07-02"},
                {"sort": 3, "airdate": "2026-07-03"},
                {"sort": 4, "airdate": "2026-07-10"},
            ]
        }


def test_broadcast_calendar_only_mine_filters_collection(monkeypatch):
    monkeypatch.setattr("otomo.tools.calendar.tool._today", lambda: __import__("datetime").date(2026, 7, 4))
    tool = BroadcastCalendarTool(FakeBangumi())
    res = asyncio.run(tool.run(BroadcastCalendarArgs(day="today", only_mine=True)))
    assert res.ok
    assert res.data is not None
    assert res.data.username == "alice"
    assert res.data.count == 1
    item = res.data.days[0].items[0]
    assert item.id == 100
    assert item.my_collection == "watching"


def test_airing_progress_computes_behind(monkeypatch):
    monkeypatch.setattr("otomo.tools.calendar.tool._today", lambda: __import__("datetime").date(2026, 7, 4))
    tool = AiringProgressTool(FakeBangumi())
    res = asyncio.run(tool.run(AiringProgressArgs(username="alice")))
    assert res.ok
    assert res.data is not None
    assert res.data.behind_count == 1
    item = res.data.items[0]
    assert item.aired_ep == 3
    assert item.my_ep == 2
    assert item.behind == 1
    assert item.next_air_date == "2026-07-10"
