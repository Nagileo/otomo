from __future__ import annotations

import asyncio
from datetime import datetime
import sqlite3

from otomo import config
from otomo.auth import AuthStore
from otomo.factory import build_registry
from otomo.memory import LongTermMemory
from otomo.share import CreateShareSnapshotRequest, ShareSnapshotStore, ShareRedaction, redact_share_payload
from otomo.subscriptions import (
    CreateSubscriptionRuleRequest,
    SubscriptionSchedule,
    SubscriptionService,
    SubscriptionStore,
    WebPushKeys,
    WebPushSubscriptionRequest,
    due_hit_key,
    is_rule_due,
)
from otomo.tools.bangumi.client import BangumiClient
from otomo.tools.source_router.tool import RouteSubjectSourcesTool, SourceRouterArgs


def test_share_redaction_removes_tokens_webhooks_comments_and_url_keys():
    redaction = ShareRedaction()
    payload = redact_share_payload(
        {
            "subject": {"name": "摇曳露营"},
            "access_token": "secret",
            "webhook_url": "https://example.com/hook?token=abc&ok=1",
            "email": "user@example.com",
            "comment": "private note",
            "public_url": "https://example.com/a?token=abc&x=1",
            "nested": {"data_url": "data:image/png;base64,xxx"},
        },
        redaction,
    )
    assert payload["access_token"] == "[redacted]"
    assert payload["webhook_url"] == "[redacted]"
    assert payload["email"] == "[redacted]"
    assert payload["comment"] == "[redacted private comment]"
    assert payload["public_url"] == "https://example.com/a?x=1"
    assert redaction.token_fields_removed
    assert redaction.webhook_fields_removed
    assert redaction.email_fields_removed
    assert redaction.comment_fields_removed
    assert redaction.local_reference_removed
    assert redaction.url_tokens_removed


def test_share_store_create_get_revoke(tmp_path):
    store = ShareSnapshotStore(str(tmp_path / "share.sqlite3"))
    snap = store.create(
        CreateShareSnapshotRequest(
            type="subject_dossier",
            title="摇曳露营 作品档案",
            payload={"subject": {"name": "摇曳露营"}, "token": "bad"},
        ),
        owner_key="user:nagi",
        created_by="nagi",
    )
    assert snap.id.startswith("share_")
    assert snap.payload["token"] == "[redacted]"
    loaded = store.get(snap.id)
    assert loaded and loaded.title == "摇曳露营 作品档案"
    assert len(store.list_mine("user:nagi")) == 1
    assert store.revoke(snap.id, "user:nagi")
    assert store.get(snap.id) is None


def test_source_router_registered_and_returns_layers():
    registry = build_registry(BangumiClient())
    assert "route_subject_sources" in registry._tools
    tool = RouteSubjectSourcesTool(BangumiClient())
    import asyncio

    res = asyncio.run(tool.run(SourceRouterArgs(subject_type="game", intent="review")))
    assert res.ok and res.data
    assert "canonical" in res.data.source_layers
    assert any(x.name == "批判空间 / ErogameScape" for x in res.data.source_layers["reputation"])
    assert "review_subject" in res.data.recommended_tools


def test_subscription_store_crud_and_due_logic(tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    rule = store.create(
        CreateSubscriptionRuleRequest(
            kind="weekly_digest",
            title="周报",
            schedule=SubscriptionSchedule(timezone="Asia/Shanghai", hour=9, minute=30, weekday=0),
            channels=["inbox", "webhook"],
        ),
        owner_key="user:nagi",
        username="nagi",
    )
    assert rule.id.startswith("sub_")
    assert store.get(rule.id, "user:nagi")
    assert len(store.list_rules("user:nagi")) == 1
    assert not is_rule_due(rule, now=datetime(2026, 7, 6, 9, 29))  # Monday, before scheduled minute
    assert is_rule_due(rule, now=datetime(2026, 7, 6, 9, 30))
    assert is_rule_due(rule, now=datetime(2026, 7, 6, 9, 45))  # missed exact minute still runs once
    store.touch_run(rule, due_hit_key(rule, datetime(2026, 7, 6, 9, 45)))
    assert not is_rule_due(rule, now=datetime(2026, 7, 6, 9, 50))
    assert not is_rule_due(rule, now=datetime(2026, 7, 7, 9, 30))
    assert store.delete(rule.id, "user:nagi")


def test_webpush_devices_are_tenant_scoped_and_expired_targets_can_be_removed(tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    req = WebPushSubscriptionRequest(
        endpoint="https://push.example.test/subscription/device-a",
        keys=WebPushKeys(p256dh="p" * 65, auth="a" * 24),
    )
    device = store.upsert_webpush("user:alice", req, user_agent="Test Browser")
    repeated = store.upsert_webpush("user:alice", req, user_agent="Updated Browser")
    assert device.id.startswith("push_")
    assert repeated.id == device.id
    assert repeated.user_agent == "Updated Browser"
    assert store.list_webpush_devices("user:bob") == []
    assert len(store.list_webpush_devices("user:alice")) == 1
    targets = store.webpush_targets("user:alice")
    assert targets[0]["endpoint"] == req.endpoint
    assert targets[0]["keys"]["auth"] == "a" * 24
    with sqlite3.connect(tmp_path / "subs.sqlite3") as conn:
        encrypted = conn.execute(
            "SELECT endpoint,p256dh,auth FROM webpush_subscriptions WHERE id=?", (device.id,)
        ).fetchone()
    assert encrypted is not None
    assert all(str(value).startswith("fernet:") for value in encrypted)
    assert req.endpoint not in "".join(str(value) for value in encrypted)
    assert store.delete_webpush_ids("user:bob", [device.id]) == 0
    assert store.delete_webpush_ids("user:alice", [device.id]) == 1
    assert store.webpush_targets("user:alice") == []


def test_webpush_delivery_keeps_success_and_reports_expired_devices(monkeypatch):
    from otomo import notifications
    from otomo.memory.models import InboxItem

    class Gone(Exception):
        response = type("Response", (), {"status_code": 410})()

    def fake_webpush(*, subscription_info, **_kwargs):
        if subscription_info["endpoint"].endswith("gone"):
            raise Gone("expired")

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)
    monkeypatch.setattr(config.settings, "webpush_enabled", True)
    monkeypatch.setattr(config.settings, "webpush_vapid_public_key", "public")
    monkeypatch.setattr(config.settings, "webpush_vapid_private_key", "private")
    target = notifications.NotificationTarget(
        channels=["webpush"],
        webpush_subscriptions=[
            {"id": "push_ok", "endpoint": "https://push.example.test/ok", "keys": {"p256dh": "p", "auth": "a"}},
            {"id": "push_gone", "endpoint": "https://push.example.test/gone", "keys": {"p256dh": "p", "auth": "a"}},
        ],
    )
    rows = asyncio.run(notifications.dispatch_notifications(
        "alice",
        target,
        InboxItem(id="notice", title="测试推送"),
    ))
    assert rows[0]["ok"] is True
    assert rows[0]["sent"] == 1
    assert rows[0]["failed"] == 1
    assert rows[0]["expired_device_ids"] == ["push_gone"]


def test_subscription_interval_and_test_push_have_no_state_side_effects(monkeypatch, tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    ltm = LongTermMemory(tmp_path)
    rule = store.create(
        CreateSubscriptionRuleRequest(
            kind="daily_airing",
            title="测试每日追番",
            schedule=SubscriptionSchedule(timezone="Asia/Shanghai", interval_minutes=15),
            channels=["inbox"],
        ),
        owner_key="user:alice",
        username="alice",
    )
    assert is_rule_due(rule, now=datetime(2026, 7, 6, 9, 0))
    store.touch_run(rule, due_hit_key(rule, datetime(2026, 7, 6, 9, 0)))
    stored = store.get(rule.id, "user:alice")
    assert stored is not None
    assert not is_rule_due(stored, now=datetime(2026, 7, 6, 9, 5))

    service = SubscriptionService(store, ltm, AuthStore(tmp_path / "auth"))

    async def fake_materialize(_rule, *, test=False):
        return {"sections": [{"title": "测试", "items": [{"name": "ok"}]}]}

    monkeypatch.setattr(service, "_materialize", fake_materialize)
    before = store.get(rule.id, "user:alice")
    assert before is not None
    record = asyncio.run(service.run_rule(before, test=True))
    after = store.get(rule.id, "user:alice")
    assert record.status == "sent"
    assert record.payload["test"] is True
    assert after is not None
    assert after.last_run_at == before.last_run_at
    assert after.last_hit_key == before.last_hit_key
    assert ltm.load_user("alice").inbox == []
