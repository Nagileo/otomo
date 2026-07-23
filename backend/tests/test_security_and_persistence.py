from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from otomo.auth import AuthStore
from otomo.claim_verifier import verify_answer_claims
from otomo.memory import LongTermMemory
from otomo.memory.models import InboxItem, MemoryItem
from otomo.notifications import validate_webhook_url
from otomo.security_context import TenantAccessError, tenant_scope
from otomo.subscriptions import (
    CreateSubscriptionRuleRequest,
    SubscriptionSchedule,
    SubscriptionService,
    SubscriptionStore,
    is_rule_due,
)


def test_private_memory_is_bound_to_runtime_principal(tmp_path):
    store = LongTermMemory(tmp_path)
    with tenant_scope("alice", authenticated=True):
        store.load_user("alice")
        with pytest.raises(TenantAccessError):
            store.load_user("bob")
    with tenant_scope(None, authenticated=False):
        with pytest.raises(TenantAccessError):
            store.load_user("alice")


def test_ltm_three_way_merge_preserves_concurrent_appends_and_deletions(tmp_path):
    store_a = LongTermMemory(tmp_path)
    store_b = LongTermMemory(tmp_path)
    seed = store_a.load_user("alice")
    seed.inbox.append(InboxItem(id="old", title="old"))
    seed.likes.append(MemoryItem(value="日常"))
    store_a.save_user(seed)

    local = store_a.load_user("alice")
    remote = store_b.load_user("alice")
    local.inbox = []
    remote.inbox.append(InboxItem(id="new", title="new"))
    remote.likes.append(MemoryItem(value="百合"))
    store_b.save_user(remote)
    store_a.save_user(local)

    merged = LongTermMemory(tmp_path).load_user("alice")
    assert [item.id for item in merged.inbox] == ["new"]
    assert {item.value for item in merged.likes} == {"日常", "百合"}


def test_weekly_schedule_does_not_backfill_previous_week(tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    rule = store.create(
        CreateSubscriptionRuleRequest(
            kind="weekly_digest",
            schedule=SubscriptionSchedule(
                timezone="Asia/Shanghai",
                weekday=4,
                hour=9,
                minute=30,
            ),
        ),
        owner_key="user:alice",
        username="alice",
    )
    assert not is_rule_due(rule, now=datetime(2026, 7, 6, 12, 0))  # Monday before Friday
    assert is_rule_due(rule, now=datetime(2026, 7, 10, 9, 31))


def test_failed_subscription_delivery_does_not_advance_cursor(monkeypatch, tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    rule = store.create(
        CreateSubscriptionRuleRequest(
            kind="birthday",
            channels=["webhook"],
            webhook_url="https://example.com/hook",
        ),
        owner_key="user:alice",
        username="alice",
    )
    service = SubscriptionService(store, LongTermMemory(tmp_path / "ltm"), AuthStore(tmp_path / "auth"))

    async def materialize(_rule, *, test=False):
        return {"sections": [{"title": "x", "items": [{"name": "item"}]}]}

    async def fail_dispatch(*_args, **_kwargs):
        return [{"channel": "webhook", "ok": False, "error": "down"}]

    monkeypatch.setattr(service, "_materialize", materialize)
    monkeypatch.setattr("otomo.subscriptions.dispatch_notifications", fail_dispatch)
    record = asyncio.run(service.run_rule(rule, now=datetime(2026, 7, 10, 12, 0)))
    stored = store.get(rule.id, rule.owner_key)
    assert record.status == "failed"
    assert stored is not None and stored.last_run_at == "" and stored.last_hit_key == ""


def test_inbox_delivery_is_recorded_only_after_persisting(monkeypatch, tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.sqlite3"))
    rule = store.create(
        CreateSubscriptionRuleRequest(kind="birthday", channels=["inbox"]),
        owner_key="user:alice",
        username="alice",
    )
    ltm = LongTermMemory(tmp_path / "ltm")
    service = SubscriptionService(store, ltm, AuthStore(tmp_path / "auth"))

    async def materialize(_rule, *, test=False):
        return {"sections": [{"title": "x", "items": [{"name": "item"}]}]}

    monkeypatch.setattr(service, "_materialize", materialize)
    monkeypatch.setattr(ltm, "save_user", lambda _mem: (_ for _ in ()).throw(OSError("disk full")))

    record = asyncio.run(service.run_rule(rule, now=datetime(2026, 7, 10, 12, 0)))
    stored = store.get(rule.id, rule.owner_key)
    assert record.status == "failed"
    assert record.deliveries[0]["channel"] == "inbox"
    assert record.deliveries[0]["ok"] is False
    assert stored is not None and stored.last_hit_key == ""


def test_webhook_validation_rejects_private_and_provider_mismatch(monkeypatch):
    with pytest.raises(ValueError):
        asyncio.run(validate_webhook_url("https://127.0.0.1/admin"))
    with pytest.raises(ValueError):
        asyncio.run(validate_webhook_url("https://example.com/hook", "discord"))

    monkeypatch.setattr(
        "otomo.notifications.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert asyncio.run(validate_webhook_url("https://example.com/hook")) == "https://example.com/hook"


def test_claim_verifier_does_not_cross_bind_similar_titles():
    result = verify_answer_claims(
        "《兰斯10》由 Alice Studio 制作。",
        [
            {
                "name": "get_subject_persons",
                "summary": "Alice Studio relation=动画制作",
                "data": {
                    "items": [
                        {"title": "兰斯9", "staff": "Alice Studio", "relation": "动画制作"},
                        {"title": "兰斯10", "staff": "Bob Studio", "relation": "动画制作"},
                    ]
                },
            }
        ],
    )
    claim = next(item for item in result.claims if item.kind == "canonical_fact")
    assert not claim.supported
    assert result.needs_revision


def test_claim_verifier_requires_rating_value_on_same_subject():
    result = verify_answer_claims(
        "《樱之刻》Bangumi 评分 9.2。",
        [
            {
                "name": "get_subject",
                "summary": "樱之诗 score=9.2; 樱之刻 score=8.1",
                "data": {
                    "items": [
                        {"title": "樱之诗", "score": 9.2},
                        {"title": "樱之刻", "score": 8.1},
                    ]
                },
            }
        ],
    )
    claim = next(item for item in result.claims if item.kind == "canonical_fact")
    assert not claim.supported
