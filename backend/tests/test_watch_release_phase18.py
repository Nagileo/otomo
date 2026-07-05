from __future__ import annotations

import asyncio
from datetime import datetime

from otomo.auth import AuthStore
from otomo.memory import LongTermMemory
from otomo.tools.release.tool import (
    AnimeReleaseFeedsArgs,
    GetAnimeReleaseFeedsTool,
    _parse_rss,
)
from otomo.tools.watch.tool import WhereToWatchArgs, WhereToWatchTool
from otomo.tools.yuc.tool import _parse as parse_yuc
from otomo.weekly import DailyAiringService


class FakeBangumi:
    async def get_me(self):
        return {"username": "alice"}

    async def get_subject(self, subject_id: int):
        return {
            "id": subject_id,
            "name": "Yuru Camp",
            "name_cn": "摇曳露营△",
            "date": "2018-01-04",
            "type": 2,
            "rating": {"score": 8.1, "rank": 300},
            "images": {"common": "cover.jpg"},
        }

    async def search_subjects(self, keyword, subject_type=None, sort="match", limit=10, tags=None, offset=0, air_date=None):
        return {"data": [await self.get_subject(207195)]}

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        if collection_type == 3:
            return [
                {
                    "ep_status": 1,
                    "subject": {
                        "id": 207195,
                        "name_cn": "摇曳露营△",
                        "eps": 12,
                        "rating": {"score": 8.1},
                        "images": {"common": "cover.jpg"},
                    },
                }
            ]
        return []

    async def get_episodes(self, subject_id, ep_type=None, limit=100, offset=0):
        return {"data": [{"sort": 1, "airdate": "2026-07-01"}, {"sort": 2, "airdate": "2026-07-05"}]}


def test_yuc_parser_keeps_bilibili_bangumi_links_only():
    html = """
    <!--#A01-->
    <img src="/img.jpg">
    <table>
      <p class="title_cn">测试番</p><p class="title_jp">Test Anime</p>
      <td class="type_a">TV</td><td class="type_tag">日常/治愈</td>
      <p class="broadcast">周六 22:00</p>
      <td class="staff">动画制作：A-1 Pictures</td>
      <a href="https://www.bilibili.com/bangumi/media/md123">B站</a>
      <a href="https://space.bilibili.com/63231">泛式</a>
      <a href="https://anime.example/">动画官网</a>
    </table>
    """
    rows = parse_yuc(html, 5)
    assert rows
    assert rows[0].bili_url == "https://www.bilibili.com/bangumi/media/md123"
    assert len(rows[0].stream_urls) == 1
    assert rows[0].official_url == "https://anime.example/"


def test_where_to_watch_uses_bangumi_data(monkeypatch):
    async def fake_data():
        return {
            "siteMeta": {"bilibili": {"title": "哔哩哔哩", "urlTemplate": "https://www.bilibili.com/bangumi/media/{{id}}", "type": "onair", "regions": ["CN"]}},
            "items": [
                {
                    "title": "摇曳露营△",
                    "sites": [
                        {"site": "bangumi", "id": "207195"},
                        {"site": "bilibili", "id": "md28223005", "type": "onair", "regions": ["CN"]},
                    ],
                }
            ],
        }

    monkeypatch.setattr("otomo.tools.watch.tool.load_bangumi_data", fake_data)
    tool = WhereToWatchTool(FakeBangumi())
    res = asyncio.run(tool.run(WhereToWatchArgs(subject_id=207195)))
    assert res.ok and res.data is not None
    assert res.data.official_sources
    assert res.data.official_sources[0].label == "哔哩哔哩"
    assert "md28223005" in res.data.official_sources[0].url


def test_parse_release_rss_extracts_torrent_metadata():
    xml = """
    <rss xmlns:torrent="https://mikanani.me/0.1/"><channel>
      <item>
        <title>[喵萌奶茶屋] 测试番 - 01 [1080p]</title>
        <link>https://mikanani.me/Home/Episode/1</link>
        <pubDate>Sun, 05 Jul 2026 12:00:00 GMT</pubDate>
        <enclosure url="https://mikanani.me/Download/1.torrent" length="1024" />
        <torrent:contentLength>1024</torrent:contentLength>
      </item>
    </channel></rss>
    """
    rows = _parse_rss(xml, "mikan")
    assert rows[0].subgroup == "喵萌奶茶屋"
    assert rows[0].torrent_url.endswith("1.torrent")
    assert rows[0].quality == "hd"
    assert rows[0].size_bytes == 1024


def test_release_tool_groups_mikan_items(monkeypatch):
    async def fake_mapping():
        return {207195: [123]}

    async def fake_fetch(url, source):
        return [
            _parse_rss(
                """
                <rss><channel><item>
                  <title>[喵萌] 摇曳露营△ - 01 [1080p]</title>
                  <link>https://mikanani.me/Home/Episode/1</link>
                  <pubDate>Sun, 05 Jul 2026 12:00:00 GMT</pubDate>
                  <enclosure url="https://mikanani.me/Download/1.torrent" />
                </item></channel></rss>
                """,
                source,
            )[0]
        ]

    monkeypatch.setattr("otomo.tools.release.tool.load_mikan_mapping", fake_mapping)
    monkeypatch.setattr("otomo.tools.release.tool.fetch_release_items_from_url", fake_fetch)
    res = asyncio.run(GetAnimeReleaseFeedsTool(FakeBangumi()).run(AnimeReleaseFeedsArgs(subject_id=207195)))
    assert res.ok and res.data is not None
    assert res.data.mikan_ids == [123]
    assert res.data.groups[0].subgroup == "喵萌"


def test_daily_airing_service_writes_once_and_updates_rss(monkeypatch, tmp_path):
    from otomo import config
    from otomo.memory.models import WatchPlanItem

    async def fake_fetch(_url, source):
        return [
            _parse_rss(
                """
                <rss><channel><item>
                  <title>[喵萌] 摇曳露营△ - 02</title>
                  <link>https://mikanani.me/Home/Episode/2</link>
                  <pubDate>Sun, 05 Jul 2026 12:30:00 GMT</pubDate>
                </item></channel></rss>
                """,
                source,
            )[0]
        ]

    monkeypatch.setattr(config.settings, "daily_airing_enabled", True, raising=False)
    monkeypatch.setattr(config.settings, "daily_airing_hour", 9, raising=False)
    monkeypatch.setattr("otomo.weekly.fetch_release_items_from_url", fake_fetch)
    ltm = LongTermMemory(tmp_path)
    mem = ltm.load_user("alice")
    mem.weekly_digest_subscription.enabled = True
    mem.watch_plan.append(
        WatchPlanItem(
            id="plan1",
            subject_id=207195,
            name="摇曳露营△",
            rss_url="https://mikanani.me/RSS/Bangumi?bangumiId=123",
            subgroup="喵萌",
        )
    )
    ltm.save_user(mem)
    service = DailyAiringService(ltm, AuthStore(tmp_path / "auth"), client_factory=lambda _u, _t: FakeBangumi())
    now = datetime(2026, 7, 5, 9, 10)
    first = asyncio.run(service.run_due_once(now))
    second = asyncio.run(service.run_due_once(now))
    saved = ltm.load_user("alice")
    assert first == 1
    assert second == 0
    assert saved.inbox[-1].kind == "daily_airing"
    assert saved.watch_plan[0].last_seen_pub_date
