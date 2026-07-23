"""Shared runtime memory hydration for every interactive surface."""
from __future__ import annotations

from typing import Any

from ..agent.contracts import AgentState
from .models import memory_summary
from .store import LongTermMemory


async def attach_memory_state(
    state: AgentState | None,
    client: Any,
    ltm: LongTermMemory,
    *,
    username: str | None = None,
) -> str | None:
    """Hydrate the same LTM summary into Web, Discord, and future clients.

    A caller that already resolved the authenticated username should pass it to
    avoid an additional ``/v0/me`` request. The surrounding tenant scope remains
    the authorization boundary.
    """
    if state is None:
        return None
    resolved = (username or "").strip()
    if not resolved:
        try:
            me = await client.get_me()
        except Exception:  # noqa: BLE001 - memory is optional enrichment
            return None
        resolved = str(me.get("username") or me.get("id") or "").strip()
    if not resolved:
        return None

    mem = ltm.load_user(resolved)
    state.short_term["memory"] = memory_summary(mem).model_dump(mode="json", exclude_none=True)
    spoiler = dict(state.short_term.get("spoiler") or {"mode": "none"})
    spoiler.setdefault("mode", "none")
    if mem.spoiler_default:
        # A long-term preference is context, not permission for this turn.
        spoiler["memory_default"] = mem.spoiler_default
    else:
        spoiler.pop("memory_default", None)
    state.short_term["spoiler"] = spoiler
    return resolved


__all__ = ["attach_memory_state"]
