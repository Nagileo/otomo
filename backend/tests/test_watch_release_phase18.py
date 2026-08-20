from __future__ import annotations

import asyncio
import httpx

from otomo.auth import AuthStore
from otomo.memory import LongTermMemory
from otomo.subscriptions import CreateSubscriptionRuleRequest, SubscriptionSchedule, SubscriptionService, SubscriptionStore
from otomo.tools.release.tool import (
    AnimeReleaseFeedsArgs,
    GetAnimeReleaseFeedsTool,
    _classify_release_content,
    _parse_rss,
)
from otomo.tools.media_identity import assess_media_scope, build_media_identity
from otomo.tools.watch.tool import (
    WhereToWatchArgs,
    WhereToWatchTool,
    _bili_title_match,
    _probe_official_url,
)
from otomo.tools.yuc.tool import _parse as parse_yuc


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


def test_bilibili_bangumi_match_rejects_second_season_for_unmarked_first_season():
    first = {"title": "轻音少女 第一季", "org_title": "K-ON!"}
    second = {"title": "轻音少女 第二季", "org_title": "K-ON!!"}
    assert _bili_title_match("轻音少女", "けいおん！", first)[0] > 0
    assert _bili_title_match("轻音少女", "けいおん！", second)[0] == 0
    assert _bili_title_match("轻音少女 第二季", "けいおん！！", first)[0] == 0
    assert _bili_title_match("轻音少女 第二季", "けいおん！！", second)[0] > 0

    highlighted_first = {
        "title": '<em class="keyword">轻音少女</em> <em class="keyword">第</em>一季',
        "org_title": "けいおん!",
    }
    assert _bili_title_match("轻音少女 第二季", "けいおん！！", highlighted_first)[0] == 0


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
    assert res.data.official_sources[0].availability_status == "catalog_match"
    assert res.data.availability_status in {"verified", "catalog_match"}
    assert res.data.last_verified
    assert res.data.official_sources[0].playability_verified is False


def test_official_page_probe_reports_reachability_without_claiming_playability(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def head(self, url):
            code = 200 if "reachable" in url else 403 if "blocked" in url else 404
            return httpx.Response(code, request=httpx.Request("HEAD", url))

        async def get(self, url):
            return await self.head(url)

    monkeypatch.setattr("otomo.tools.watch.tool.httpx.AsyncClient", FakeClient)
    reachable = asyncio.run(_probe_official_url("https://www.netflix.com/reachable-title-20260820"))
    blocked = asyncio.run(_probe_official_url("https://www.netflix.com/blocked-title-20260820"))
    missing = asyncio.run(_probe_official_url("https://www.netflix.com/missing-title-20260820"))
    assert reachable["status"] == "reachable"
    assert reachable["label"] == "官方页面可达"
    assert "播放" not in str(reachable)
    assert blocked == {"status": "blocked", "label": "平台阻止自动探测", "http_status": 403}
    assert missing == {"status": "unavailable", "label": "页面疑似下架", "http_status": 404}


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
    assert rows[0].resolution == "1080p"
    assert rows[0].episode_label == "第 1 集"
    assert rows[0].release_kind == "episode"

    codec_only = _parse_rss("""
    <rss><channel><item><title>[VCB-Studio] 测试番 [1080p][x264][10bit]</title>
    <link>https://example.test/release</link></item></channel></rss>
    """, "vcb")
    assert codec_only[0].episode_label == ""


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


def test_shared_media_identity_separates_current_installment_bundle_and_movie():
    identity = build_media_identity(
        subject_id=1,
        title="轻音少女 第一季",
        aliases=["K-ON!"],
        platform="TV",
    )
    assert assess_media_scope(identity, "[字幕组] 轻音少女 第一季 01").status == "exact"
    assert assess_media_scope(identity, "轻音少女 S1-S2 + MOVIE + LIVE").status == "bundle"
    assert assess_media_scope(identity, "轻音少女合集【14+27+Movie】BDRIP").status == "bundle"
    movie = assess_media_scope(identity, "轻音少女 剧场版 BDRip")
    assert movie.status == "conflict"
    assert "剧场版" in movie.reason
    concert = assess_media_scope(identity, "K-ON! Live Event 横滨演唱会 1080p BDRip")
    assert concert.status == "conflict"
    assert "衍生内容" in concert.reason

    lost_identity = build_media_identity(title="Lost Universe", platform="TV")
    assert assess_media_scope(lost_identity, "Lost Universe 01 BDRip").status == "compatible"


def test_release_resolution_is_not_mistaken_for_a_version_year():
    identity = build_media_identity(
        title="轻音少女",
        aliases=["K-ON!"],
        platform="TV",
        air_date="2009-04-03",
    )
    resolution_only = assess_media_scope(identity, "K-ON! [BDrip 1920x1080 x264]")
    assert resolution_only.status == "compatible"
    unicode_resolution = assess_media_scope(identity, "K-ON! [1920×1080 HEVC]")
    assert unicode_resolution.status == "compatible"

    explicit_version = assess_media_scope(identity, "K-ON! 2025 [1920x1080]")
    assert explicit_version.status == "conflict"
    assert "2025 年" in explicit_version.reason


def test_release_tool_moves_cross_installment_items_out_of_default_groups(monkeypatch):
    class FirstSeasonBangumi(FakeBangumi):
        async def get_subject(self, subject_id: int):
            return {
                "id": subject_id,
                "name": "K-ON!",
                "name_cn": "轻音少女 第一季",
                "platform": "TV",
                "eps": 12,
                "type": 2,
                "images": {"common": "cover.jpg"},
            }

    async def fake_mapping():
        return {1424: [777]}

    async def fake_fetch(_url, source):
        return [
            _parse_rss(f"""
              <rss><channel><item><title>{title}</title><link>https://example.test/{index}</link></item></channel></rss>
            """, source)[0]
            for index, title in enumerate((
                "[字幕组] 轻音少女 第一季 01",
                "轻音少女 S1-S2 + MOVIE + LIVE",
                "轻音少女 剧场版 BDRip",
                "轻音少女 第一季 音乐专辑 [FLAC]",
                "轻音少女 第一季 漫画合集 [EPUB]",
            ), 1)
        ]

    async def fake_subgroups(_mikan_id):
        return {}

    monkeypatch.setattr("otomo.tools.release.tool.load_mikan_mapping", fake_mapping)
    monkeypatch.setattr("otomo.tools.release.tool.fetch_release_items_from_url", fake_fetch)
    monkeypatch.setattr("otomo.tools.release.tool._subgroup_rss_map", fake_subgroups)
    result = asyncio.run(GetAnimeReleaseFeedsTool(FirstSeasonBangumi()).run(AnimeReleaseFeedsArgs(
        subject_id=1424,
        prefer="mikan",
    )))

    assert result.ok and result.data is not None
    default_titles = [item.title for group in result.data.groups for item in group.latest_items]
    assert default_titles == ["[字幕组] 轻音少女 第一季 01"]
    assert result.data.filtered_count == 4
    assert {item.scope_status for item in result.data.related_items} == {"bundle", "conflict"}
    assert any("音乐" in item.scope_reason for item in result.data.related_items)
    assert any("漫画" in item.scope_reason for item in result.data.related_items)


def test_release_content_kind_rejects_books_games_and_keeps_title_words_safe():
    identity = build_media_identity(title="音乐少女", aliases=["Music Girls"], platform="TV")
    assert _classify_release_content(
        "[字幕组] 音乐少女 - 01 [1080p][HEVC]", identity,
    )[0] == "anime_video"
    assert _classify_release_content("音乐少女 原声集 [FLAC]", identity)[0] == "audio"
    assert _classify_release_content("音乐少女 轻小说合集 [EPUB]", identity)[0] == "book"
    assert _classify_release_content("音乐少女 Visual Novel PC Game", identity)[0] == "game"
    assert _classify_release_content("音乐少女 漫画版 Scanlation", identity)[0] == "comic"
    assert _classify_release_content("音乐少女", identity)[0] == "unknown"


def test_release_content_kind_rejects_real_world_wallpaper_subtitle_and_ncop_false_positives():
    identity = build_media_identity(title="轻音少女", aliases=["K-ON!"])
    cases = {
        "轻音少女 壁纸合集 [JPG][PNG]": "image",
        "轻音少女 NCOP NCED 合集 [1080p]": "extras",
        "轻音少女 字幕合集 [ASS]": "subtitle",
    }
    for title, expected in cases.items():
        assert _classify_release_content(title, identity)[0] == expected


def test_ambiguous_title_only_release_stays_out_of_default_area(monkeypatch):
    async def fake_mapping():
        return {207195: [123]}

    async def fake_fetch(_url, source):
        return [_parse_rss("""
          <rss><channel><item><title>摇曳露营△</title>
          <link>https://example.test/ambiguous</link></item></channel></rss>
        """, source)[0]]

    async def fake_subgroups(_mikan_id):
        return {}

    monkeypatch.setattr("otomo.tools.release.tool.load_mikan_mapping", fake_mapping)
    monkeypatch.setattr("otomo.tools.release.tool.fetch_release_items_from_url", fake_fetch)
    monkeypatch.setattr("otomo.tools.release.tool._subgroup_rss_map", fake_subgroups)
    result = asyncio.run(GetAnimeReleaseFeedsTool(FakeBangumi()).run(
        AnimeReleaseFeedsArgs(subject_id=207195, prefer="mikan")
    ))
    assert result.ok and result.data is not None
    assert not result.data.groups
    assert result.data.related_items[0].content_kind == "unknown"
    assert result.data.related_items[0].scope_status == "unknown"


def test_daily_airing_service_writes_once_and_updates_rss(monkeypatch, tmp_path):
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

    monkeypatch.setattr("otomo.subscriptions.fetch_release_items_from_url", fake_fetch)
    ltm = LongTermMemory(tmp_path)
    mem = ltm.load_user("alice")
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
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    rule = store.create(
        CreateSubscriptionRuleRequest(
            kind="daily_airing",
            title="每日追番",
            filters={"include_birthday": False, "include_radar": False, "include_rss": True},
            schedule=SubscriptionSchedule(timezone="Asia/Shanghai", hour=9, minute=0),
            channels=["inbox"],
        ),
        owner_key="user:alice",
        username="alice",
    )
    service = SubscriptionService(store, ltm, AuthStore(tmp_path / "auth"), client_factory=lambda _u, _t: FakeBangumi())
    first = asyncio.run(service.run_rule(rule))
    second = asyncio.run(service.run_rule(rule))
    saved = ltm.load_user("alice")
    assert first.status == "sent"
    assert second.status == "skipped"
    assert saved.inbox[-1].kind == "daily_airing"
    assert saved.watch_plan[0].last_seen_pub_date


def test_reverse_mikan_map_real_direction():
    """实测数据方向是 {mikan_id: bangumi_subject_id}（key 为 183~4042 的小数字）。

    曾把 key 当成 bangumi_id 解析，导致反查表 key 全是 mikan id、
    mapping.get(subject_id) 永远 miss、蜜柑主路静默失效。"""
    from otomo.tools.release.tool import _reverse_mikan_map

    real_shape = {"183": "139317", "3644": "425998"}
    reversed_map = _reverse_mikan_map(real_shape)
    assert reversed_map == {139317: [183], 425998: [3644]}


def test_release_tool_multi_mikan_ids_no_nested_deadlock(monkeypatch):
    """外层曾对 jobs 再套 gather_limited(host='mikan')：mikan 上限 2，
    mikan_ids >= 2 时外层占满槽、内层 _fetch_text 永远等待 → 死锁。
    本用例保留内层真实的 gather_limited 路径，修复前会 10s 超时。"""
    from otomo.tools.release import tool as release_tool

    async def fake_mapping():
        return {207195: [11, 22, 33]}

    async def fake_fetch_text(url: str, host: str) -> str:
        from otomo.tools._concurrency import gather_limited

        async def one() -> str:
            await asyncio.sleep(0.01)
            return """<rss><channel><item>
              <title>[喵萌] 摇曳露营△ - 01 [1080p]</title>
              <link>https://mikanani.me/Home/Episode/1</link>
              <pubDate>Sun, 05 Jul 2026 12:00:00 GMT</pubDate>
              <enclosure url="https://mikanani.me/Download/1.torrent" />
            </item></channel></rss>"""

        result = await gather_limited([one()], host=host)
        first = result[0]
        if isinstance(first, BaseException):
            raise first
        return first

    monkeypatch.setattr(release_tool, "load_mikan_mapping", fake_mapping)
    monkeypatch.setattr(release_tool, "_fetch_text", fake_fetch_text)

    async def scenario():
        return await asyncio.wait_for(
            GetAnimeReleaseFeedsTool(FakeBangumi()).run(
                AnimeReleaseFeedsArgs(subject_id=207195, prefer="mikan")
            ),
            timeout=10,
        )

    res = asyncio.run(scenario())
    assert res.ok and res.data is not None
    assert res.data.mikan_ids == [11, 22, 33]
    assert res.data.groups
