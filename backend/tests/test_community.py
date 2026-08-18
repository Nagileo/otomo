from __future__ import annotations

from fastapi.testclient import TestClient

from otomo import config
from otomo.api.app import app
from otomo.auth import BangumiToken
from otomo.community import CommunityStore


def test_community_store_aggregates_visitors_and_guestbook(tmp_path):
    store = CommunityStore(str(tmp_path / "community.sqlite3"))

    store.record_visit("visitor-a", "/")
    store.record_visit("visitor-a", "/")  # 同一访客/页面/小时只记一次
    store.record_visit("visitor-a", "/chat")
    store.record_visit("visitor-b", "/chat")

    stats = store.stats()
    assert stats["total_visitors"] == 2
    assert stats["visitors_today"] == 2
    assert stats["total_views"] == 3
    assert stats["views_today"] == 3
    assert stats["popular_pages"][0] == {"path": "/chat", "views": 2}

    comment = store.create_comment(
        "alice",
        "  很喜欢推荐解释！  \n\n 希望继续完善。 ",
        "https://lain.bgm.tv/pic/user/l/alice.jpg",
    )
    assert comment["content"] == "很喜欢推荐解释！\n希望继续完善。"
    assert comment["avatar_url"].endswith("alice.jpg")
    assert store.list_comments("alice")[0]["can_delete"] is True
    assert store.list_comments("bob")[0]["can_delete"] is False
    assert store.list_comments("moderator", admin_usernames={"moderator"})[0]["can_delete"] is True

    store.report_comment(comment["id"], "bob", "广告")
    reported = store.list_comments("bob")[0]
    assert reported["reported"] is True
    assert reported["can_report"] is False
    assert store.list_comments("moderator", admin_usernames={"moderator"})[0]["report_count"] == 1

    queue = store.moderation_overview()
    report_id = queue["reports"][0]["id"]
    store.moderate_comment(comment["id"], "hide", "moderator", "等待复核")
    assert store.list_comments("bob") == []
    assert store.list_comments("moderator", admin_usernames={"moderator"})[0]["moderation_status"] == "hidden"
    store.moderate_comment(comment["id"], "restore", "moderator")
    resolved = store.resolve_report(report_id, "resolved", "moderator", "已沟通")
    assert resolved["status"] == "resolved"
    assert store.moderation_overview()["counts"]["pending_reports"] == 0

    try:
        store.delete_comment(comment["id"], "bob")
    except PermissionError:
        pass
    else:
        raise AssertionError("expected comment owner isolation")
    store.delete_comment(comment["id"], "moderator", {"moderator"})
    assert store.list_comments("alice") == []


def test_community_api_requires_login_and_csrf_for_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "auth_store_path", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setattr(config.settings, "session_store_path", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setattr(config.settings, "share_store_path", str(tmp_path / "shares.sqlite3"))
    monkeypatch.setattr(config.settings, "subscription_store_path", str(tmp_path / "subs.sqlite3"))
    monkeypatch.setattr(config.settings, "today_store_path", str(tmp_path / "today.sqlite3"))
    monkeypatch.setattr(config.settings, "recommendation_event_store_path", str(tmp_path / "rec.sqlite3"))
    monkeypatch.setattr(config.settings, "recommendation_artifact_cache_path", str(tmp_path / "rec-cache.sqlite3"))
    monkeypatch.setattr(config.settings, "background_run_store_path", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setattr(config.settings, "workspace_store_path", str(tmp_path / "workspace.sqlite3"))
    monkeypatch.setattr(config.settings, "community_store_path", str(tmp_path / "community.sqlite3"))
    monkeypatch.setattr(config.settings, "ltm_store_path", str(tmp_path / "ltm.sqlite3"))
    monkeypatch.setattr(config.settings, "quota_store_path", str(tmp_path / "quota.json"))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)
    monkeypatch.setattr(config.settings, "rate_limit_enabled", False)
    monkeypatch.setattr(config.settings, "community_admin_usernames", "moderator")

    with TestClient(app) as client:
        auth = client.get("/auth/session").json()
        assert client.post("/community/visit", json={"path": "/chat"}).status_code == 200
        assert client.post("/community/comments", json={"content": "hello"}).status_code == 401

        session_id = client.cookies.get(config.settings.session_cookie_name)
        assert session_id
        app.state.auth.save_token(
            BangumiToken(
                auth_session_id=session_id,
                access_token="token",
                username="alice",
                avatar_url="https://lain.bgm.tv/pic/user/l/alice.jpg",
            )
        )
        assert client.post("/community/comments", json={"content": "hello"}).status_code == 403
        created = client.post(
            "/community/comments",
            headers={"x-otomo-csrf": auth["csrf_token"]},
            json={"content": "这里的执行过程更清楚了"},
        )
        assert created.status_code == 200
        comment = created.json()["comment"]
        assert comment["avatar_url"].endswith("alice.jpg")
        overview = client.get("/community").json()
        assert overview["stats"]["total_visitors"] == 1
        assert overview["comments"][0]["can_delete"] is True

        # A second authenticated account can report but cannot delete Alice's comment.
        client.cookies.clear()
        bob_auth = client.get("/auth/session").json()
        bob_session = client.cookies.get(config.settings.session_cookie_name)
        assert bob_session
        app.state.auth.save_token(
            BangumiToken(auth_session_id=bob_session, access_token="token", username="bob")
        )
        reported = client.post(
            f"/community/comments/{comment['id']}/reports",
            headers={"x-otomo-csrf": bob_auth["csrf_token"]},
            json={"reason": "广告"},
        )
        assert reported.status_code == 200
        assert client.delete(
            f"/community/comments/{comment['id']}",
            headers={"x-otomo-csrf": bob_auth["csrf_token"]},
        ).status_code == 403

        # The configured moderator sees the quality dashboard and can use
        # reversible moderation before resolving the report.
        client.cookies.clear()
        moderator_auth = client.get("/auth/session").json()
        moderator_session = client.cookies.get(config.settings.session_cookie_name)
        assert moderator_session
        app.state.auth.save_token(BangumiToken(
            auth_session_id=moderator_session, access_token="token", username="moderator",
        ))
        dashboard = client.get("/admin/overview?days=7")
        assert dashboard.status_code == 200
        report_id = dashboard.json()["community"]["moderation"]["reports"][0]["id"]
        hidden = client.post(
            f"/admin/comments/{comment['id']}/moderate",
            headers={"x-otomo-csrf": moderator_auth["csrf_token"]},
            json={"action": "hide", "note": "等待复核"},
        )
        assert hidden.status_code == 200
        assert client.get("/community").json()["comments"][0]["moderation_status"] == "hidden"
        assert client.post(
            f"/admin/comments/{comment['id']}/moderate",
            headers={"x-otomo-csrf": moderator_auth["csrf_token"]},
            json={"action": "restore"},
        ).status_code == 200
        assert client.post(
            f"/admin/reports/{report_id}/resolve",
            headers={"x-otomo-csrf": moderator_auth["csrf_token"]},
            json={"status": "resolved", "note": "已复核"},
        ).status_code == 200

        client.cookies.clear()
        owner_auth = client.get("/auth/session").json()
        owner_session = client.cookies.get(config.settings.session_cookie_name)
        assert owner_session
        app.state.auth.save_token(
            BangumiToken(auth_session_id=owner_session, access_token="token", username="alice")
        )

        deleted = client.delete(
            f"/community/comments/{comment['id']}",
            headers={"x-otomo-csrf": owner_auth["csrf_token"]},
        )
        assert deleted.status_code == 200
