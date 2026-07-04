from __future__ import annotations

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
