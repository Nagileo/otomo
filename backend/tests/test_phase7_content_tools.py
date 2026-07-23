from __future__ import annotations

import asyncio
from otomo.tools.videos.tool import BiliSubtitleSegment, _parse_danmaku, _rough_subtitle_summary
from otomo.tools.videos.tool import _guide_links, classify_subject_verticals
from otomo.tools.watchorder.tool import (
    WeeklyDigestArgs,
    WeeklyDigestTool,
    _watch_metadata,
)
from otomo.tools.websearch.tool import _TextExtractor, _highlights
from otomo.tools.websearch.tool import BrowserFetchArgs, BrowserFetchSummaryTool, _validate_public_url


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


def test_watch_order_metadata_marks_recap_and_ova():
    necessity, advice, hint = _watch_metadata("某作品 总集篇", "续集", "main", 1)
    assert necessity == "skip"
    assert "总集篇" in advice
    assert "总集篇" in hint

    necessity, advice, hint = _watch_metadata("某作品 OVA", "番外篇", "side", 2)
    assert necessity == "optional"
    assert "OVA" in advice or "番外" in advice
    assert "2" in hint


def test_guide_routing_prefers_vertical_up_sources():
    yuri = classify_subject_verticals(["百合", "校园", "日常"], title="百合新番")
    assert yuri[0].name == "yuri_core"
    yuri_links = _guide_links("某百合番", "review", 3, ["百合", "校园"])
    assert yuri_links[0].up_name == "FlowerMX-花梦"
    assert any(v.name == "yuri_core" for v in yuri_links[0].verticals)
    assert yuri_links[0].route_score > yuri_links[-1].route_score

    kirara = classify_subject_verticals(["まんがタイムきらら", "日常"], title="芳文社新番")
    assert kirara[0].name == "kirara"
    kirara_links = _guide_links("某芳文番", "review", 3, ["まんがタイムきらら", "日常"])
    assert kirara_links[0].up_name in {"芳文观星台", "大猫猫组"}
    assert any(v.name == "kirara" for v in kirara_links[0].verticals)


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
