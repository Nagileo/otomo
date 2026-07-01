from __future__ import annotations

import asyncio

from otomo.agent._common import runtime_state_prompt
from otomo.agent.contracts import AgentState
from otomo.tools.multimodal import tool as multimodal_tool
from otomo.tools.multimodal.tool import (
    AnalyzeVideoFramesArgs,
    AnalyzeVideoFramesTool,
    ExtractVisualTextArgs,
    ExtractVisualTextTool,
    ImageSourceSearchArgs,
    ImageSourceSearchTool,
    ImageInputArgs,
    RouteImageSourceArgs,
    RouteImageSourceTool,
    VisualStyleRecommendArgs,
    VisualStyleRecommendTool,
    _extract_titles,
    _image_inputs,
)
from otomo.uploads import ImageUploadStore


class FakeBangumiClient:
    async def search_subjects(
        self,
        keyword: str = "",
        subject_type: int | None = None,
        sort: str = "match",
        limit: int = 10,
        tags: list[str] | None = None,
        offset: int = 0,
        air_date: list[str] | None = None,
    ):
        if keyword in {"摇曳露营△", "Yuru Camp"} or tags:
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
        if keyword in {"サクラノ刻", "樱之刻"} and subject_type == 4:
            return {
                "data": [
                    {
                        "id": 500001,
                        "name": "サクラノ刻",
                        "name_cn": "樱之刻",
                        "rating": {"score": 8.4},
                        "images": {"common": "https://img.example/sakura.jpg"},
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


def test_route_image_source_reports_missing_vlm_without_failing(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")
    monkeypatch.setattr(config.settings, "saucenao_api_key", "")
    tool = RouteImageSourceTool(FakeBangumiClient())
    res = asyncio.run(tool.run(RouteImageSourceArgs(image_url="data:image/png;base64,iVBORw0KGgo=", routes=["unknown"])))
    assert res.ok
    assert res.data
    assert res.data.candidates == []
    assert any("VLM_MODEL" in x for x in res.data.caveats)


def test_image_inputs_support_multiple_and_dedupe():
    args = ImageInputArgs(
        image_url="upload://a",
        image_urls=["upload://b", "upload://a", "upload://c", "upload://d", "upload://e"],
    )
    assert _image_inputs(args) == ["upload://a", "upload://b", "upload://c", "upload://d"]


def test_route_trace_moe_candidate_anchors_to_bangumi(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "")
    monkeypatch.setattr(config.settings, "saucenao_api_key", "")

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
    tool = RouteImageSourceTool(FakeBangumiClient())
    res = asyncio.run(tool.run(RouteImageSourceArgs(image_url="https://example.com/shot.jpg", routes=["anime"])))
    assert res.ok
    assert res.data
    assert res.data.candidates[0].bangumi_id == 207195
    assert res.data.candidates[0].source == "trace.moe"
    assert res.data.candidates[0].timestamp == "01:23"


def test_route_vlm_character_candidate_anchors_to_bangumi(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")
    monkeypatch.setattr(config.settings, "saucenao_api_key", "")

    async def fake_vlm(_image_url: str, _question: str):
        return '{"candidates":[{"title":"摇曳露营△","reason":"露营画面","confidence":0.6}],"characters":[{"name":"各务原抚子","reason":"粉发角色","confidence":0.55}],"visual_tags":["日常","户外"],"ocr_text":"欢迎来到露营地"}'

    async def fake_ocr(_image_url: str, _system_prompt: str, _question: str):
        return '{"markdown_text":"","structured_items":[],"entities":[],"visual_tags":[],"confidence":0.0}'

    monkeypatch.setattr(multimodal_tool, "_call_vlm", fake_vlm)
    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_ocr)
    tool = RouteImageSourceTool(FakeBangumiClient())
    res = asyncio.run(tool.run(RouteImageSourceArgs(image_url="data:image/png;base64,iVBORw0KGgo=", routes=["anime"])))
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


def test_extract_visual_text_respects_empty_entities_in_json(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")

    async def fake_vlm(_image_url: str, _system_prompt: str, _question: str):
        return '```json\n{"markdown_text":"","structured_items":[],"entities":[],"visual_tags":["blank_image"],"confidence":1.0,"notes":["无文字"]}\n```'

    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_vlm)
    tool = ExtractVisualTextTool(FakeBangumiClient())
    res = asyncio.run(tool.run(ExtractVisualTextArgs(image_url="data:image/png;base64,iVBORw0KGgo=", mode="auto")))
    assert res.ok
    assert res.data
    assert res.data.entities == []
    assert "blank_image" in res.data.visual_tags


def test_visual_style_recommend_maps_tags(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")

    async def fake_vlm(_image_url: str, _system_prompt: str, _question: str):
        return '{"style_description":"柔和色调的户外日常","visual_tags":["日常","治愈","露营"],"confidence":0.7}'

    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_vlm)
    tool = VisualStyleRecommendTool(FakeBangumiClient())
    res = asyncio.run(tool.run(VisualStyleRecommendArgs(image_url="data:image/png;base64,iVBORw0KGgo=")))
    assert res.ok
    assert res.data
    assert "日常" in res.data.bangumi_tags
    assert res.data.candidates[0].id == 207195


def test_image_source_search_merges_trace_and_saucenao(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "saucenao_api_key", "fake-key")

    async def fake_trace(_image_url: str):
        return [
            {
                "anilist": {"id": 98444, "title": {"native": "摇曳露营△"}},
                "episode": 1,
                "from": 12.0,
                "similarity": 0.91,
                "image": "https://trace.example/shot.jpg",
                "video": "https://trace.example/shot.mp4",
            }
        ]

    async def fake_saucenao(_image_url: str, _limit: int):
        return [
            {
                "header": {"similarity": "88.5", "index_name": "Pixiv", "thumbnail": "https://thumb.example/a.jpg"},
                "data": {
                    "title": "camp fanart",
                    "member_name": "artist",
                    "ext_urls": ["https://www.pixiv.net/artworks/123"],
                },
            }
        ]

    monkeypatch.setattr(multimodal_tool, "_trace_moe_search", fake_trace)
    monkeypatch.setattr(multimodal_tool, "_saucenao_search", fake_saucenao)
    tool = ImageSourceSearchTool()
    res = asyncio.run(tool.run(ImageSourceSearchArgs(image_url="https://example.com/a.jpg", engines=["trace_moe", "saucenao", "pixiv"])))
    assert res.ok
    assert res.data
    assert {m.engine for m in res.data.matches} == {"trace.moe", "saucenao"}
    assert any(link["source"] == "pixiv" for link in res.data.navigation_links)


def test_route_image_source_aggregates_trace_saucenao_ocr_and_book_sources(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")
    monkeypatch.setattr(config.settings, "saucenao_api_key", "fake-key")

    async def fake_trace(_image_url: str):
        return [
            {
                "anilist": {"id": 98444, "title": {"native": "摇曳露营△"}},
                "episode": 1,
                "from": 12.0,
                "similarity": 0.82,
                "image": "https://trace.example/shot.jpg",
            }
        ]

    async def fake_saucenao(_image_url: str, _limit: int):
        return [
            {
                "header": {"similarity": "91.0", "index_name": "H-Game CG", "thumbnail": "https://thumb.example/gal.jpg"},
                "data": {"title": "サクラノ刻", "ext_urls": ["https://vndb.org/v999"]},
            }
        ]

    async def fake_vlm(_image_url: str, _system_prompt: str, _question: str):
        return '{"markdown_text":"封面标题：《摇曳露营△》","structured_items":[],"entities":["摇曳露营△"],"visual_tags":["封面","漫画"],"confidence":0.8}'

    async def fake_semantic_vlm(_image_url: str, _question: str):
        return '{"candidates":[{"title":"摇曳露营△","reason":"露营封面","confidence":0.55}],"characters":[{"name":"各务原抚子","reason":"粉发角色","confidence":0.5}],"visual_tags":["日常"],"ocr_text":"摇曳露营△"}'

    async def fake_google_books(_query: str, _limit: int):
        return [{"source": "google_books", "title": "摇曳露营△", "url": "https://books.example/yuru", "external_id": "gb1"}]

    async def fake_open_library(_query: str, _limit: int):
        return []

    async def fake_mangadex(_query: str, _limit: int):
        return [{"source": "mangadex", "title": "摇曳露营△", "url": "https://mangadex.org/title/yuru", "external_id": "md1"}]

    monkeypatch.setattr(multimodal_tool, "_trace_moe_search", fake_trace)
    monkeypatch.setattr(multimodal_tool, "_saucenao_search", fake_saucenao)
    monkeypatch.setattr(multimodal_tool, "_call_vlm", fake_semantic_vlm)
    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_vlm)
    monkeypatch.setattr(multimodal_tool, "_google_books_search", fake_google_books)
    monkeypatch.setattr(multimodal_tool, "_open_library_search", fake_open_library)
    monkeypatch.setattr(multimodal_tool, "_mangadex_search", fake_mangadex)

    tool = RouteImageSourceTool(FakeBangumiClient())
    res = asyncio.run(
        tool.run(
            RouteImageSourceArgs(
                image_url="https://example.com/a.jpg",
                routes=["auto"],
                include_book_sources=True,
                limit=12,
            )
        )
    )
    assert res.ok
    assert res.data
    routes = {c.route for c in res.data.candidates}
    assert {"anime", "galgame", "comic"}.issubset(routes)
    assert any(c.bangumi_id == 207195 for c in res.data.candidates)
    assert any(c.bangumi_id == 500001 for c in res.data.candidates)
    assert res.data.character_candidates[0].bangumi_id == 123
    assert "get_subject" in res.data.next_tools
    assert res.data.navigation_links


def test_analyze_video_frames_uses_frame_images(monkeypatch):
    from otomo import config

    monkeypatch.setattr(config.settings, "vlm_model", "fake-vlm")

    async def fake_vlm(_image_url: str, _system_prompt: str, _question: str):
        return '{"markdown_text":"本月推荐：摇曳露营△","structured_items":[{"type":"work","name":"摇曳露营△","value":"推荐","note":"PPT"}],"visual_tags":["PPT","日常"],"confidence":0.66}'

    async def fake_trace(_image_url: str):
        return [
            {
                "anilist": {"id": 98444, "title": {"native": "摇曳露营△"}},
                "episode": 2,
                "from": 34.0,
                "similarity": 0.9,
                "image": "https://trace.example/frame.jpg",
            }
        ]

    monkeypatch.setattr(multimodal_tool, "_call_vlm_with_prompt", fake_vlm)
    monkeypatch.setattr(multimodal_tool, "_trace_moe_search", fake_trace)
    tool = AnalyzeVideoFramesTool(FakeBangumiClient())
    res = asyncio.run(
        tool.run(
            AnalyzeVideoFramesArgs(
                frame_image_urls=["data:image/png;base64,iVBORw0KGgo="],
                purpose="both",
                mode="ppt",
            )
        )
    )
    assert res.ok
    assert res.data
    assert res.data.frame_count == 1
    assert "摇曳露营" in res.data.merged_ocr_text
    assert res.data.candidate_subjects[0].bangumi_id == 207195


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
    assert "route_image_source" in prompt
