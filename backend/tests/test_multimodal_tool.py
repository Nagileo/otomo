from __future__ import annotations

import asyncio

from otomo.tools.multimodal.tool import IdentifyScreenshotArgs, IdentifyScreenshotTool, _extract_titles


class FakeBangumiClient:
    async def search_subjects(self, keyword: str, subject_type: int | None = None, limit: int = 10):
        return {"data": []}


def test_extract_titles_from_vlm_text():
    titles = _extract_titles("可能是《摇曳露营△》或《向山进发》，画面里有户外露营元素。")
    assert "摇曳露营△" in titles
    assert "向山进发" in titles


def test_identify_screenshot_requires_vlm_config(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")
    tool = IdentifyScreenshotTool(FakeBangumiClient())
    res = asyncio.run(tool.run(IdentifyScreenshotArgs(image_url="https://example.com/a.png")))
    assert not res.ok
    assert "VLM" in (res.error or "")
