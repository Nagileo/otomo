from __future__ import annotations

import asyncio

from otomo.agent._common import runtime_state_prompt
from otomo.agent.contracts import AgentState
from otomo.tools.multimodal import tool as multimodal_tool
from otomo.tools.multimodal.tool import (
    ExtractVisualTextArgs,
    ExtractVisualTextTool,
    IdentifyScreenshotArgs,
    IdentifyScreenshotTool,
    _extract_titles,
    _image_inputs,
)
from otomo.uploads import ImageUploadStore


class FakeBangumiClient:
    async def search_subjects(self, keyword: str, subject_type: int | None = None, limit: int = 10):
        if keyword in {"摇曳露营△", "Yuru Camp"}:
            return {
                "data": [
                    {
                        "id": 207195,
                        "name": "ゆるキャン△",
                        "name_cn": "摇曳露营△",
                        "rating": {"score": 8.1},
                        "images": {"common": "https://img.example/yuru.jpg"},
                    }
                ]
            }
        return {"data": []}

    async def search_characters(self, keyword: str, limit: int = 10):
        if keyword in {"各务原抚子", "各務原なでしこ"}:
            return {"data": [{"id": 123, "name": "各務原なでしこ"}]}
        return {"data": []}


def test_extract_titles_from_vlm_text():
    titles = _extract_titles("可能是《摇曳露营△》或《向山进发》，画面里有户外露营元素。")
    assert "摇曳露营△" in titles
    assert "向山进发" in titles


def test_identify_screenshot_requires_vlm_config(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")
    tool = IdentifyScreenshotTool(FakeBangumiClient())
    res = asyncio.run(tool.run(IdentifyScreenshotArgs(image_url="https://example.com/a.png", use_trace_moe=False)))
    assert not res.ok
    assert "视觉候选" in (res.error or "")


def test_image_inputs_support_multiple_and_dedupe():
    args = IdentifyScreenshotArgs(
        image_url="upload://a",
        image_urls=["upload://b", "upload://a", "upload://c", "upload://d", "upload://e"],
    )
    assert _image_inputs(args) == ["upload://a", "upload://b", "upload://c", "upload://d"]


def test_trace_moe_candidate_anchors_to_bangumi(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")

    async def fake_trace(_image_url: str):
        return [
            {
                "anilist": {"id": 98444, "title": {"native": "摇曳露营△", "romaji": "Yuru Camp"}},
                "episode": 1,
                "from": 83.2,
                "to": 86.0,
                "similarity": 0.94,
                "image": "https://trace.example/shot.jpg",
            }
        ]

    monkeypatch.setattr(multimodal_tool, "_trace_moe_search", fake_trace)
    tool = IdentifyScreenshotTool(FakeBangumiClient())
    res = asyncio.run(tool.run(IdentifyScreenshotArgs(image_url="https://example.com/shot.jpg")))
    assert res.ok
    assert res.data
    assert res.data.candidates[0].bangumi_id == 207195
    assert res.data.candidates[0].source == "trace.moe"
    assert res.data.candidates[0].timestamp == "01:23"


def test_vlm_character_candidate_anchors_to_bangumi(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")

    async def fake_vlm(_image_url: str, _question: str):
        return '{"candidates":[{"title":"摇曳露营△","reason":"露营画面","confidence":0.6}],"characters":[{"name":"各务原抚子","reason":"粉发角色","confidence":0.55}],"visual_tags":["日常","户外"],"ocr_text":"欢迎来到露营地"}'

    monkeypatch.setattr(multimodal_tool, "_call_vlm", fake_vlm)
    tool = IdentifyScreenshotTool(FakeBangumiClient())
    res = asyncio.run(tool.run(IdentifyScreenshotArgs(image_url="data:image/png;base64,iVBORw0KGgo=", use_trace_moe=False)))
    assert res.ok
    assert res.data
    assert res.data.candidates[0].bangumi_id == 207195
    assert res.data.character_candidates[0].bangumi_id == 123
    assert "日常" in res.data.visual_tags
    assert "欢迎来到露营地" in res.data.ocr_text


def test_extract_visual_text_requires_vlm(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")
    tool = ExtractVisualTextTool(FakeBangumiClient())
    res = asyncio.run(tool.run(ExtractVisualTextArgs(image_url="upload://missing")))
    assert not res.ok
    assert "VLM_MODEL" in (res.error or "")


def test_extract_visual_text_anchors_entities(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")

    async def fake_vlm(_image_url: str, _system_prompt: str, _question: str):
        return '{"markdown_text":"|作品|评分|\\n|摇曳露营△|8.1|","structured_items":[{"type":"work","name":"摇曳露营△","value":"8.1","note":"榜单评分"}],"entities":["摇曳露营△"],"visual_tags":["榜单","露营"],"confidence":0.77,"notes":["清晰"]}'

    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_vlm)
    tool = ExtractVisualTextTool(FakeBangumiClient())
    res = asyncio.run(tool.run(ExtractVisualTextArgs(image_url="data:image/png;base64,iVBORw0KGgo=", mode="ranking")))
    assert res.ok
    assert res.data
    assert "摇曳露营" in res.data.markdown_text
    assert res.data.structured_items[0].type == "work"
    assert res.data.entities[0].bangumi_id == 207195
    assert "榜单" in res.data.visual_tags


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
