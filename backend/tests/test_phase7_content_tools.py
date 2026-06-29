from __future__ import annotations

import asyncio
from datetime import datetime

from otomo.auth import AuthStore
from otomo.memory import LongTermMemory
from otomo.tools.videos.tool import BiliSubtitleSegment, _parse_danmaku, _rough_subtitle_summary
from otomo.tools.watchorder.tool import (
    ConfigureWeeklyDigestArgs,
    ConfigureWeeklyDigestTool,
    GenerateWeeklyDigestNowArgs,
    GenerateWeeklyDigestNowTool,
    WeeklyDigestArgs,
    WeeklyDigestTool,
)
from otomo.tools.websearch.tool import _TextExtractor, _highlights
from otomo.tools.websearch.tool import BrowserFetchArgs, BrowserFetchSummaryTool, _validate_public_url
from otomo.weekly import WeeklyDigestService


def test_url_text_extractor_and_highlights():
    parser = _TextExtractor()
    parser.feed(
        "<html><head><title>测试帖</title><script>bad()</script></head>"
        "<body><h1>标题</h1><p>摇曳露营 第一集 讨论很好。</p><p>大家喜欢露营氛围。</p></body></html>"
    )
    assert parser.title == "测试帖"
    assert "bad" not in parser.text
    hits = _highlights(parser.text, "摇曳露营", limit=2)
    assert hits and "摇曳露营" in hits[0]


def test_browser_fetch_rejects_local_urls():
    for url in ["http://localhost:3000", "http://127.0.0.1/a", "http://192.168.1.2/a"]:
        try:
            _validate_public_url(url)
        except ValueError as e:
            assert "不允许" in str(e)
        else:
            raise AssertionError(f"expected reject: {url}")


def test_browser_fetch_missing_playwright_is_clear():
    tool = BrowserFetchSummaryTool()
    res = asyncio.run(tool.run(BrowserFetchArgs(url="https://example.com", render="always")))
    if not res.ok:
        assert "Playwright" in (res.error or "") or "浏览器摘要失败" in (res.error or "")


def test_rough_subtitle_summary_samples_timeline():
    segments = [BiliSubtitleSegment(start=i, end=i + 1, text=f"片段{i}") for i in range(12)]
    summary = _rough_subtitle_summary(segments)
    assert len(summary) == 3
    assert "片段0" in summary[0]


def test_parse_bili_danmaku_xml():
    items = _parse_danmaku('<i><d p="1.2,1,25,16777215,0,0,0,0">好耶</d><d p="3.0">期待</d></i>')
    assert len(items) == 2
    assert items[0].time == 1.2
    assert items[1].text == "期待"


class FakeBangumiClient:
    async def get_me(self) -> dict:
        return {"username": "Nagileo"}

    async def get_all_user_collections(self, username, subject_type=2, collection_type=None, max_items=300):
        subject = {
            "id": 1 + (collection_type or 0),
            "name_cn": {1: "想看番", 2: "已看番", 3: "在看番", 4: "搁置番"}.get(collection_type, "番"),
            "eps": 12,
            "rating": {"score": 8.0, "rank": 500},
            "images": {"common": "img"},
            "tags": [{"name": "治愈"}, {"name": "日常"}],
        }
        if collection_type == 2:
            return [{"rate": 9, "subject": subject}]
        if collection_type in {1, 3, 4}:
            return [{"ep_status": 3, "subject": subject}]
        return []


def test_weekly_digest_builds_sections():
    tool = WeeklyDigestTool(FakeBangumiClient())
    res = asyncio.run(tool.run(WeeklyDigestArgs(limit=6)))
    assert res.ok and res.data is not None
    assert res.data.sections
    titles = {s.title for s in res.data.sections}
    assert {"继续追", "想看开坑"} <= titles
    assert res.data.next_actions


def test_weekly_digest_subscription_and_inbox(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    cfg = ConfigureWeeklyDigestTool(client, ltm)
    res = asyncio.run(cfg.run(ConfigureWeeklyDigestArgs(enabled=True, weekday=1, hour=20)))
    assert res.ok and res.data is not None
    assert res.data.subscription.enabled
    assert res.data.memory.weekly_digest_subscription.hour == 20

    gen = GenerateWeeklyDigestNowTool(client, ltm)
    inbox = asyncio.run(gen.run(GenerateWeeklyDigestNowArgs(limit=6)))
    assert inbox.ok and inbox.data is not None
    assert inbox.data.items
    assert inbox.data.memory.inbox[-1].kind == "weekly_digest"


def test_weekly_digest_scheduler_runs_due_once(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    cfg = ConfigureWeeklyDigestTool(client, ltm)
    asyncio.run(cfg.run(ConfigureWeeklyDigestArgs(enabled=True, weekday=0, hour=9)))

    service = WeeklyDigestService(ltm, AuthStore(tmp_path / "auth"), client_factory=lambda _u, _t: FakeBangumiClient())
    now = datetime(2026, 6, 29, 9, 5)  # Monday
    first = asyncio.run(service.run_due_once(now))
    second = asyncio.run(service.run_due_once(now))
    assert first == 1
    assert second == 0
    assert ltm.load_user("Nagileo").inbox
