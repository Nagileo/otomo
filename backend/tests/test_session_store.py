from __future__ import annotations

import sqlite3

from otomo.agent.contracts import AgentState
from otomo.session_store import SessionStore


def test_session_store_persists_messages_evidence_and_state(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    sid = "s1"
    auth = "auth1"

    store.append_message(sid, auth, role="user", content="今天有什么番更新？")
    store.append_message(
        sid,
        auth,
        role="assistant",
        content="今日有动画A更新。",
        evidence={"get_broadcast_calendar": [{"count": 1}]},
        sources=[{"title": "动画A", "url": "https://bgm.tv/subject/100", "source": "bangumi"}],
        trace=[{"kind": "obs", "name": "get_broadcast_calendar", "ok": True, "summary": "查到今日放送"}],
        steps=["规划：查询今日放送", "✓ 查到今日放送"],
        turn_id="turn-1",
        elapsed_ms=1450,
    )
    state = AgentState(
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        short_term={"spoiler": {"mode": "none", "progress_episode": 3}},
        status="done",
    )
    store.save_state(sid, auth, state)

    listed = store.list_sessions(auth)
    assert listed[0]["id"] == sid
    assert listed[0]["message_count"] == 2

    restored = store.load_messages(sid, auth)
    assert len(restored["messages"]) == 2
    assert restored["evidence"]["get_broadcast_calendar"][0]["count"] == 1
    assert restored["sources"][0]["title"] == "动画A"
    assistant = restored["messages"][1]
    assert assistant["trace"][0]["name"] == "get_broadcast_calendar"
    assert assistant["steps"] == ["规划：查询今日放送", "✓ 查到今日放送"]
    assert assistant["turn_id"] == "turn-1"
    assert assistant["elapsed_ms"] == 1450

    restored_state = store.load_state(sid, auth)
    assert restored_state is not None
    assert restored_state.short_term["spoiler"]["progress_episode"] == 3


def test_session_store_rejects_cross_auth_access(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.ensure_session("s1", "auth1")
    try:
        store.ensure_session("s1", "auth2")
    except PermissionError:
        pass
    else:
        raise AssertionError("expected owner mismatch")


def test_discord_handoff_is_identity_bound_single_use_and_clones_messages(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    state = AgentState(
        messages=[{"role": "user", "content": "摇曳露营讲到哪了"}],
        short_term={"discord_identity": "sunshineclover", "spoiler": {"mode": "none"}},
    )
    store.ensure_session(
        "discord-1",
        "discord:42",
        "摇曳露营",
        source="discord",
        source_label="Discord 私聊",
    )
    store.append_message("discord-1", "discord:42", role="user", content="摇曳露营讲到哪了")
    store.append_message("discord-1", "discord:42", role="assistant", content="第三季。")
    store.save_state("discord-1", "discord:42", state)
    code = store.create_handoff("discord-1", "discord:42", "sunshineclover")

    try:
        store.consume_handoff(code, "another-user", "user:another-user")
    except PermissionError:
        pass
    else:
        raise AssertionError("expected identity mismatch")

    imported = store.consume_handoff(code, "sunshineclover", "user:sunshineclover")
    restored = store.load_messages(imported["id"], "user:sunshineclover")
    assert imported["source"] == "discord_import"
    assert imported["message_count"] == 2
    assert [row["content"] for row in restored["messages"]] == ["摇曳露营讲到哪了", "第三季。"]
    assert "discord_identity" not in restored["state"]["short_term"]

    try:
        store.consume_handoff(code, "sunshineclover", "user:sunshineclover")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected one-use handoff")


def test_session_store_migrates_legacy_session_schema(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                auth_session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
            ("old", "user:u", "旧会话", "{}", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )

    store = SessionStore(str(path))
    row = store.list_sessions("user:u")[0]
    assert row["source"] == "web"
    assert row["source_label"] == ""
    assert row["revision"] == 0


def test_session_store_migrates_legacy_message_schema(tmp_path):
    path = tmp_path / "legacy-messages.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                auth_session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'web',
                source_label TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)",
            ("old", "user:u", "旧会话", "{}", "web", "", 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            ("old", "assistant", "旧回答", "2026-01-01T00:00:00"),
        )

    restored = SessionStore(str(path)).load_messages("old", "user:u")["messages"][0]
    assert restored["trace"] == []
    assert restored["steps"] == []
    assert restored["turn_id"] == ""
    assert restored["elapsed_ms"] is None
