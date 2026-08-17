from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from otomo import config
from otomo.agent.contracts import AnswerDeltaEvent, FinalEvent


app_module = importlib.import_module("otomo.api.app")
app = app_module.app


class DummyClient:
    async def aclose(self) -> None:
        return None


def test_chat_run_is_replayable_and_persists_the_completed_turn(tmp_path, monkeypatch):
    for setting, filename in {
        "auth_store_path": "auth.sqlite3",
        "session_store_path": "sessions.sqlite3",
        "share_store_path": "shares.sqlite3",
        "subscription_store_path": "subs.sqlite3",
        "today_store_path": "today.sqlite3",
        "recommendation_event_store_path": "rec.sqlite3",
        "workspace_store_path": "workspace.sqlite3",
        "community_store_path": "community.sqlite3",
        "ltm_store_path": "ltm.sqlite3",
        "quota_store_path": "quota.json",
    }.items():
        monkeypatch.setattr(config.settings, setting, str(tmp_path / filename))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)
    monkeypatch.setattr(config.settings, "rate_limit_enabled", False)
    monkeypatch.setattr(config.settings, "trajectory_log_enabled", False)

    async def fake_request_client(*_args, **_kwargs):
        return DummyClient()

    async def fake_attach_memory(*_args, **_kwargs):
        return None

    async def fake_stream(*_args, **_kwargs):
        yield AnswerDeltaEvent(text="后台")
        yield FinalEvent(answer="后台任务已完成")

    monkeypatch.setattr(app_module, "_request_client", fake_request_client)
    monkeypatch.setattr(app_module, "build_registry", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(app_module, "_runner_from_registry", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(app_module, "attach_memory_state", fake_attach_memory)
    monkeypatch.setattr(app_module, "traced_stream", fake_stream)

    with TestClient(app) as client:
        auth = client.get("/auth/session").json()
        session_id = "chat-background-test"
        response = client.post(
            "/chat",
            headers={"x-otomo-csrf": auth["csrf_token"]},
            json={"message": "继续执行", "session_id": session_id, "device_id": "test-device"},
        )
        assert response.status_code == 200
        run_id = response.headers["x-otomo-run-id"]
        assert '"type":"final"' in response.text

        run = client.get(f"/chat/runs/{run_id}").json()["run"]
        assert run["status"] == "completed"
        replay = client.get(f"/chat/runs/{run_id}/events")
        assert '"answer":"后台任务已完成"' in replay.text

        messages = client.get(f"/sessions/{session_id}/messages").json()["messages"]
        assert [row["content"] for row in messages] == ["继续执行", "后台任务已完成"]
