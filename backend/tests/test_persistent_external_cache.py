from __future__ import annotations

from otomo.tools._persistent_cache import PersistentJsonCache


def test_persistent_json_cache_is_shared_across_instances(tmp_path):
    path = tmp_path / "external.sqlite3"
    first = PersistentJsonCache(path, "bilibili-search")
    created_at = first.set("轻音少女 正片", {"code": 0, "data": {"result": [{"bvid": "BV1"}]}})

    second = PersistentJsonCache(path, "bilibili-search")
    hit = second.get("轻音少女 正片", ttl=3600)

    assert hit is not None
    assert hit[0]["data"]["result"][0]["bvid"] == "BV1"
    assert hit[1] == created_at
    assert PersistentJsonCache(path, "another-namespace").get("轻音少女 正片", ttl=3600) is None
