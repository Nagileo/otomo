"""Read-only, redacted projection of the unified subscription store."""
from __future__ import annotations

from typing import Any


def public_subscription_summary(username: str) -> dict[str, Any]:
    """Return UI-safe subscription status without delivery credentials.

    The runtime import avoids a module cycle: the subscription materializer
    imports product tools, while product tools only need this read projection.
    """
    if not username:
        return {"enabled_count": 0, "total_count": 0, "rules": []}
    from .subscriptions import SubscriptionStore

    rules = SubscriptionStore().list_rules(f"user:{username}", limit=100)
    public_rules = [
        {
            "id": rule.id,
            "kind": rule.kind,
            "title": rule.title,
            "enabled": rule.enabled,
            "schedule": rule.schedule.model_dump(mode="json", exclude_none=True),
            "channels": rule.channels,
            "template": rule.template,
            "quiet_hours": rule.quiet_hours.model_dump(mode="json", exclude_none=True),
            "last_run_at": rule.last_run_at,
            "updated_at": rule.updated_at,
        }
        for rule in rules
    ]
    return {
        "enabled_count": sum(1 for rule in rules if rule.enabled),
        "total_count": len(rules),
        "rules": public_rules,
    }
