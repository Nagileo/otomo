from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from otomo import config
from otomo.agent.contracts import ToolResult
from otomo.api.app import app
from otomo.auth import BangumiToken
from otomo.memory import LongTermMemory
from otomo.memory.models import FeedbackItem
from otomo.security_context import tenant_scope
from otomo.tools.product_loop.tool import SubjectDossierResult
from otomo.tools.profile.tool import (
    CollectionDashboardArgs,
    CollectionDashboardResult,
    CollectionDashboardTool,
)
from otomo.tools.recommend.tool import RecommendResult
from otomo.tools.season.tool import SeasonGuideBriefResult


def _configure_stores(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config.settings, "auth_store_path", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setattr(config.settings, "session_store_path", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setattr(config.settings, "share_store_path", str(tmp_path / "shares.sqlite3"))
    monkeypatch.setattr(config.settings, "subscription_store_path", str(tmp_path / "subs.sqlite3"))
    monkeypatch.setattr(config.settings, "ltm_store_path", str(tmp_path / "ltm.sqlite3"))
    monkeypatch.setattr(config.settings, "quota_store_path", str(tmp_path / "quota.json"))
    monkeypatch.setattr(config.settings, "recommendation_event_store_path", str(tmp_path / "rec.sqlite3"))
    monkeypatch.setattr(config.settings, "today_store_path", str(tmp_path / "today.sqlite3"))
    monkeypatch.setattr(config.settings, "workspace_store_path", str(tmp_path / "workspace.sqlite3"))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)
    monkeypatch.setattr(config.settings, "rate_limit_enabled", False)


def _login(client: TestClient) -> tuple[str, str]:
    auth = client.get("/auth/session").json()
    session_id = client.cookies.get(config.settings.session_cookie_name)
    assert session_id
    app.state.auth.save_token(BangumiToken(
        auth_session_id=session_id,
        access_token="token",
        username="alice",
    ))
    return session_id, auth["csrf_token"]


def test_product_surfaces_enforce_identity_and_inject_current_username(tmp_path, monkeypatch):
    _configure_stores(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    async def season_run(_self, args):
        seen["season_username"] = args.username
        return ToolResult(ok=True, data=SeasonGuideBriefResult(season="2026 夏", count=0))

    async def recommendation_run(_self, args):
        seen["recommend_username"] = args.username
        return ToolResult(ok=True, data=RecommendResult(subject_type=args.subject_type, based_on_tags=[]))

    async def library_run(_self, args):
        seen["library_username"] = args.username
        return ToolResult(ok=True, data=CollectionDashboardResult(username=args.username or ""))

    monkeypatch.setattr("otomo.api.app.SeasonGuideBriefTool.run", season_run)
    monkeypatch.setattr("otomo.api.app.RecommendTool.run", recommendation_run)
    monkeypatch.setattr("otomo.api.app.CollectionDashboardTool.run", library_run)

    with TestClient(app) as client:
        public = client.get("/product/season-guide?year=2026&month=7")
        assert public.status_code == 200
        assert seen["season_username"] is None
        assert client.get("/product/library").status_code == 401

        _session_id, csrf = _login(client)
        rec = client.post(
            "/product/recommendations",
            headers={"x-otomo-csrf": csrf},
            json={"subject_type": "anime", "username": "mallory", "limit": 3},
        )
        assert rec.status_code == 200
        assert seen["recommend_username"] == "alice"
        library = client.get("/product/library?subject_types=anime,invalid")
        assert library.status_code == 200
        assert seen["library_username"] == "alice"


def test_subject_surface_is_public_and_forwards_spoiler_and_release_flags(tmp_path, monkeypatch):
    _configure_stores(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    async def dossier_run(_self, args):
        seen.update(args.model_dump())
        return ToolResult(ok=True, data=SubjectDossierResult(
            subject={"id": args.subject_id, "name": "测试作品"},
        ))

    monkeypatch.setattr("otomo.api.app.SubjectDossierTool.run", dossier_run)
    with TestClient(app) as client:
        response = client.get("/product/subjects/42?spoiler_level=mild&include_release=false")
        assert response.status_code == 200
        assert response.json()["data"]["subject"]["id"] == 42
        assert seen["spoiler_level"] == "mild"
        assert seen["include_release"] is False


def test_collection_dashboard_reads_feedback_from_full_user_memory(tmp_path):
    class FakeClient:
        async def get_all_user_collections(self, *_args, **_kwargs):
            return []

    ltm = LongTermMemory(tmp_path)
    with tenant_scope("alice", authenticated=True):
        memory = ltm.load_user("alice")
        memory.feedback.append(FeedbackItem(subject_id=42, name="测试作品", signal="more"))
        ltm.save_user(memory)
        result = asyncio.run(CollectionDashboardTool(FakeClient(), ltm).run(CollectionDashboardArgs(
            username="alice",
            subject_types=["anime"],
            max_items_per_type=100,
            enrich_people=False,
        )))

    assert result.ok and result.data is not None
    assert result.data.memory_signals["recent_feedback"][0]["subject_id"] == 42


def test_workspace_and_inbox_are_account_scoped(tmp_path, monkeypatch):
    _configure_stores(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _session_id, csrf = _login(client)
        created = client.post(
            "/workspace/views",
            headers={"x-otomo-csrf": csrf},
            json={"name": "夏季百合", "surface": "discover", "params": {"tags": ["百合"]}},
        )
        assert created.status_code == 200
        assert client.get("/workspace/views").json()["data"][0]["name"] == "夏季百合"

        custom = client.post(
            "/workspace/lists",
            headers={"x-otomo-csrf": csrf},
            json={"title": "周末补番", "description": "短篇优先"},
        ).json()["data"]
        updated = client.put(
            f"/workspace/lists/{custom['id']}/items",
            headers={"x-otomo-csrf": csrf},
            json={"subject_id": 42, "name": "测试作品", "subject_type": "anime"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["items"][0]["subject_id"] == 42

        with tenant_scope("alice", authenticated=True):
            mem = app.state.ltm.load_user("alice")
            from otomo.memory.models import InboxItem
            mem.inbox.append(InboxItem(id="notice-1", title="今日更新"))
            app.state.ltm.save_user(mem)
        assert client.get("/product/inbox").json()["data"]["unread"] == 1
        read = client.patch(
            "/product/inbox/notice-1",
            headers={"x-otomo-csrf": csrf},
            json={"unread": False},
        )
        assert read.status_code == 200
        assert client.get("/product/inbox").json()["data"]["unread"] == 0


def test_webpush_api_binds_devices_without_exposing_capability_secrets(tmp_path, monkeypatch):
    _configure_stores(tmp_path, monkeypatch)
    monkeypatch.setattr(config.settings, "webpush_enabled", True)
    monkeypatch.setattr(config.settings, "webpush_vapid_public_key", "public-key")
    monkeypatch.setattr(config.settings, "webpush_vapid_private_key", "private-key")

    async def valid_endpoint(_url, _fmt="generic"):
        return _url

    monkeypatch.setattr("otomo.api.app.validate_webhook_url", valid_endpoint)
    with TestClient(app) as client:
        _session_id, csrf = _login(client)
        created = client.post(
            "/subscriptions/webpush",
            headers={"x-otomo-csrf": csrf},
            json={
                "endpoint": "https://push.example.test/subscription/alice",
                "keys": {"p256dh": "p" * 65, "auth": "a" * 24},
            },
        )
        assert created.status_code == 200
        device_id = created.json()["device"]["id"]
        config_payload = client.get("/subscriptions/webpush/config").json()
        assert config_payload["enabled"] is True
        assert config_payload["public_key"] == "public-key"
        assert config_payload["devices"][0]["id"] == device_id
        assert "endpoint" not in config_payload["devices"][0]
        assert "keys" not in config_payload["devices"][0]
        deleted = client.delete(
            f"/subscriptions/webpush/{device_id}",
            headers={"x-otomo-csrf": csrf},
        )
        assert deleted.status_code == 200
        assert client.get("/subscriptions/webpush/config").json()["devices"] == []


def test_subscription_rule_rejects_webpush_when_vapid_is_not_ready(tmp_path, monkeypatch):
    _configure_stores(tmp_path, monkeypatch)
    monkeypatch.setattr(config.settings, "webpush_enabled", False)
    with TestClient(app) as client:
        _session_id, csrf = _login(client)
        response = client.post(
            "/subscriptions/rules",
            headers={"x-otomo-csrf": csrf},
            json={"kind": "daily_airing", "channels": ["webpush"]},
        )
        assert response.status_code == 400
        assert "VAPID" in response.json()["detail"]
