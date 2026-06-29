from __future__ import annotations

import asyncio

from otomo.agent._common import runtime_state_prompt
from otomo.agent.contracts import AgentState
from otomo.tools.multimodal.tool import IdentifyScreenshotArgs, IdentifyScreenshotTool, _extract_titles
from otomo.uploads import ImageUploadStore


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


def test_upload_store_resolves_upload_uri(tmp_path):
    store = ImageUploadStore(tmp_path)
    image = store.save_data_url(
        "data:image/png;base64,iVBORw0KGgo=",
        filename="shot.png",
    )
    assert image.uri.startswith("upload://")
    resolved = store.resolve_image_url(image.uri)
    assert resolved.startswith("data:image/png;base64,")


def test_runtime_state_mentions_uploaded_images():
    state = AgentState(short_term={"attachments": [{"uri": "upload://abc", "filename": "shot.png", "mime_type": "image/png"}]})
    prompt = runtime_state_prompt(state)
    assert "upload://abc" in prompt
    assert "identify_acgn_screenshot" in prompt
