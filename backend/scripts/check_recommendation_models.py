"""Report recommendation model coverage and freshness for CI/deploy logs."""
from __future__ import annotations

import sys

from otomo.recsys_registry import cf_model_registry


def main() -> int:
    statuses = cf_model_registry.statuses()
    anime = next(status for status in statuses if status.subject_type == "anime")
    for status in statuses:
        state = "missing" if not status.available else "stale" if status.stale else "ready"
        age = f", age={status.age_days}d" if status.age_days is not None else ""
        print(
            f"{status.subject_type}: {state}, users={status.n_users}, "
            f"items={status.n_items}, interactions={status.n_interactions}{age}"
        )
        for warning in status.warnings:
            print(f"  warning: {warning}")
    if not anime.available:
        print("error: anime collaborative model is missing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
