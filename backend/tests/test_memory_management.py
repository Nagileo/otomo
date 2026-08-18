from __future__ import annotations

from fastapi.testclient import TestClient

from otomo import config
from otomo.api.app import app
from otomo.auth import BangumiToken
from otomo.memory.models import VisualFeedbackItem
from otomo.security_context import tenant_scope


def test_memory_management_is_owner_scoped_editable_and_exportable(tmp_path, monkeypatch):
    for setting, filename in {
        "auth_store_path": "auth.sqlite3",
        "session_store_path": "sessions.sqlite3",
        "background_run_store_path": "runs.sqlite3",
        "share_store_path": "shares.sqlite3",
        "subscription_store_path": "subs.sqlite3",
        "today_store_path": "today.sqlite3",
        "recommendation_event_store_path": "rec.sqlite3",
        "recommendation_artifact_cache_path": "rec-cache.sqlite3",
        "workspace_store_path": "workspace.sqlite3",
        "community_store_path": "community.sqlite3",
        "ltm_store_path": "ltm.sqlite3",
        "quota_store_path": "quota.json",
    }.items():
        monkeypatch.setattr(config.settings, setting, str(tmp_path / filename))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)
    monkeypatch.setattr(config.settings, "rate_limit_enabled", False)

    with TestClient(app) as client:
        auth = client.get("/auth/session").json()
        assert client.get("/memory").status_code == 401
        session_id = client.cookies.get(config.settings.session_cookie_name)
        assert session_id
        app.state.auth.save_token(BangumiToken(
            auth_session_id=session_id, access_token="token", username="alice",
        ))
        headers = {"x-otomo-csrf": auth["csrf_token"]}
        updated = client.patch("/memory", headers=headers, json={
            "likes": [{
                "value": "慢节奏日常", "source": "explicit_user",
                "confidence": 1, "ts": "2026-08-19T00:00:00Z",
            }],
            "progress": {"摇曳露营": {
                "episode": 5, "source": "explicit_user", "confidence": 1,
            }},
            "spoiler_default": "mild",
        })
        assert updated.status_code == 200
        assert updated.json()["data"]["counts"]["explicit"] == 2
        assert updated.json()["data"]["progress"]["摇曳露营"]["episode"] == 5

        exported = client.get("/memory/export").json()
        assert exported["schema"] == "otomo-memory-v1"
        assert exported["data"]["username"] == "alice"
        assert exported["data"]["likes"][0]["value"] == "慢节奏日常"

        with tenant_scope("alice", authenticated=True):
            memory = app.state.ltm.load_user("alice")
            memory.visual_feedback.append(VisualFeedbackItem(
                id="visual-1", predicted_title="旧识别", signal="wrong",
            ))
            app.state.ltm.save_user(memory)

        cleared = client.delete("/memory/all", headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["data"]["likes"] == []
        assert cleared.json()["data"]["progress"] == {}
        assert cleared.json()["data"]["spoiler_default"] == "none"
        with tenant_scope("alice", authenticated=True):
            assert app.state.ltm.load_user("alice").visual_feedback == []
