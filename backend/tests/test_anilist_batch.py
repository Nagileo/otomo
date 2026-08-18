from __future__ import annotations

import pytest

from otomo.tools.anilist import tool as anilist_module
from otomo.tools.anilist.tool import AniListArgs, SearchAniListTool


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeAsyncClient:
    calls: list[dict] = []

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, _url: str, *, json: dict) -> FakeResponse:
        self.calls.append(json)
        data = {}
        for key, title in json["variables"].items():
            index = int(key.removeprefix("s"))
            data[f"q{index}"] = {
                "media": [{
                    "id": 1000 + index,
                    "title": {"romaji": f"Roman {index}", "native": title, "english": None},
                    "averageScore": 80 + index,
                    "seasonYear": 2020,
                    "format": "TV",
                    "episodes": 12,
                }],
            }
        return FakeResponse({"data": data})


@pytest.mark.asyncio
async def test_anilist_bulk_search_preserves_each_result_and_batches(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(anilist_module.httpx, "AsyncClient", FakeAsyncClient)
    requests = [AniListArgs(keyword=f"原名{i}", type="anime", limit=3) for i in range(10)]

    results = await SearchAniListTool().run_many(requests)

    assert len(FakeAsyncClient.calls) == 2
    assert len(results) == 10
    assert all(result.ok and result.data for result in results)
    assert [result.data.query for result in results if result.data] == [f"原名{i}" for i in range(10)]
    assert [result.data.results[0].id for result in results if result.data] == list(range(1000, 1010))
    assert all(result.data.mapping_status == "verified" for result in results if result.data)
    assert all(result.data.results[0].verified for result in results if result.data)
    assert all("Page(perPage:3)" in call["query"] for call in FakeAsyncClient.calls)


def test_anilist_mapping_rejects_search_rank_without_title_match():
    result = SearchAniListTool._result(
        AniListArgs(keyword="サクラノ刻", type="anime", expected_year=2023),
        [{
            "id": 1,
            "title": {"native": "サクラノ詩", "romaji": "Sakura no Uta"},
            "averageScore": 90,
            "seasonYear": 2023,
        }],
    )

    assert result.data
    assert result.data.mapping_status == "unmatched"
    assert not result.data.results[0].verified
    assert result.sources == []


def test_anilist_mapping_rejects_ambiguous_remakes_and_uses_year_to_disambiguate():
    rows = [
        {"id": 1, "title": {"native": "同名作品"}, "seasonYear": 2000, "episodes": 12},
        {"id": 2, "title": {"native": "同名作品"}, "seasonYear": 2025, "episodes": 12},
    ]
    ambiguous = SearchAniListTool._result(AniListArgs(keyword="同名作品"), rows)
    resolved = SearchAniListTool._result(
        AniListArgs(keyword="同名作品", expected_year=2025, expected_episodes=12),
        rows,
    )

    assert ambiguous.data and ambiguous.data.mapping_status == "ambiguous"
    assert not any(item.verified for item in ambiguous.data.results)
    assert resolved.data and resolved.data.mapping_status == "verified"
    assert next(item for item in resolved.data.results if item.verified).id == 2
