from __future__ import annotations

import asyncio

from recsys_offline import bangumi_collect


def test_fetch_user_returns_none_when_any_page_exhausts_retries(monkeypatch):
    calls = 0

    async def fake_fetch_page(_client, _uid, _stype, offset):
        nonlocal calls
        calls += 1
        if offset == 0:
            return [
                {
                    "type": 2,
                    "rate": 9,
                    "updated_at": "2026-08-01T00:00:00Z",
                    "subject": {"id": item_id},
                }
                for item_id in range(50)
            ], "ok"
        return [], "failed"

    monkeypatch.setattr(bangumi_collect, "_fetch_page", fake_fetch_page)

    rows = asyncio.run(bangumi_collect.fetch_user(object(), 7, 2))

    assert calls == 2
    assert rows is None


def test_fetch_user_treats_missing_user_as_completed_empty(monkeypatch):
    async def fake_fetch_page(_client, _uid, _stype, _offset):
        return [], "missing"

    monkeypatch.setattr(bangumi_collect, "_fetch_page", fake_fetch_page)

    assert asyncio.run(bangumi_collect.fetch_user(object(), 404, 2)) == []
