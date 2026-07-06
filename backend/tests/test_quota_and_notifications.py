import time

import pytest
from fastapi import HTTPException

from otomo.memory.models import InboxItem, WeeklyDigestSubscription
from otomo.notifications import _telegram_endpoint_and_payload, digest_text
from otomo.quota import RateLimiter, TokenQuotaStore, estimate_tokens


def test_rate_limiter_rejects_after_window_limit(monkeypatch):
    monkeypatch.setattr("otomo.config.settings.rate_limit_enabled", True)
    limiter = RateLimiter()
    limiter.check("chat:ip:test", limit=2, window_seconds=60)
    limiter.check("chat:ip:test", limit=2, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        limiter.check("chat:ip:test", limit=2, window_seconds=60)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_quota_store_rolls_daily_budget(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    monkeypatch.setattr("otomo.config.settings.daily_token_budget_user", 20)
    monkeypatch.setattr("otomo.config.settings.daily_token_budget_global", 100)
    store = TokenQuotaStore(str(path))
    store.record("user:nagi", 12)
    store.check("user:nagi")
    store.record("user:nagi", 10)
    with pytest.raises(HTTPException) as exc:
        store.check("user:nagi")
    assert exc.value.status_code == 429


def test_estimate_tokens_is_stable_for_chinese_text():
    assert estimate_tokens("摇曳露营适合日常治愈口味") >= 10
    assert estimate_tokens("") == 8


def test_digest_text_uses_action_and_why_fields():
    item = InboxItem(
        id="x",
        title="Otomo 周报",
        payload={
            "sections": [
                {
                    "title": "本周放送",
                    "items": [{"name": "摇曳露营", "action": "继续看第 5 集", "why": ["落后 2 集"]}],
                    "notes": ["日期以日本放送为准"],
                }
            ],
            "next_actions": ["把队列加入计划板"],
        },
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    text = digest_text(item)
    assert "继续看第 5 集" in text
    assert "日期以日本放送为准" in text
    assert "把队列加入计划板" in text


def test_digest_text_honors_push_grading_brief():
    item = InboxItem(
        id="x",
        title="每日提醒",
        payload={
            "push_grading": "brief",
            "sections": [
                {
                    "title": "追番",
                    "items": [{"name": f"番{i}", "action": f"看第 {i} 集"} for i in range(6)],
                    "notes": ["只应该保留第一条 note", "这条 brief 不展示"],
                }
            ],
        },
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    text = digest_text(item)
    assert "番0" in text and "番2" in text
    assert "番3" not in text
    assert "只应该保留第一条 note" in text
    assert "这条 brief 不展示" not in text


def test_telegram_webhook_url_can_carry_chat_id():
    sub = WeeklyDigestSubscription(webhook_format="telegram", webhook_url="https://api.telegram.org/botTOKEN/sendMessage?chat_id=42")
    endpoint, payload = _telegram_endpoint_and_payload(sub.webhook_url, "hello")
    assert endpoint == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload["chat_id"] == "42"
    assert payload["text"] == "hello"
