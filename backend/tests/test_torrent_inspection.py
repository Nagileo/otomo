from __future__ import annotations

from otomo.tools.release.tool import _torrent_files


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(type(value))


def test_torrent_parser_reads_internal_file_names_and_sizes():
    payload = bencode({
        b"announce": b"https://tracker.example/announce",
        b"info": {
            b"name": "测试动画 第二季".encode(),
            b"files": [
                {b"length": 100, b"path": ["测试动画 第二季 [01].mkv".encode()]},
                {b"length": 120, b"path": ["测试动画 第二季 [02].mkv".encode()]},
            ],
        },
    })
    files, root = _torrent_files(payload)
    assert root == "测试动画 第二季"
    assert files == [
        ("测试动画 第二季/测试动画 第二季 [01].mkv", 100),
        ("测试动画 第二季/测试动画 第二季 [02].mkv", 120),
    ]


def test_torrent_parser_rejects_missing_info_dictionary():
    try:
        _torrent_files(bencode({b"announce": b"https://tracker.example"}))
    except ValueError as exc:
        assert "missing info" in str(exc)
    else:
        raise AssertionError("missing info dictionary should fail")
