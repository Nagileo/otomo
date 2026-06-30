"""Outbound notification helpers for scheduled digests.

The channel layer is deliberately small and dependency-light:
- inbox is handled by the caller because it writes local memory.
- webhook sends a JSON payload to a user-configured endpoint.
- email uses stdlib SMTP so production can point it at any relay.
"""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .config import settings
from .memory.consolidate import now_iso
from .memory.models import InboxItem, WeeklyDigestSubscription


def digest_text(item: InboxItem) -> str:
    payload = item.payload or {}
    lines = [item.title]
    for section in payload.get("sections") or []:
        title = section.get("title") or "Section"
        lines.append(f"\n## {title}")
        for row in (section.get("items") or [])[:8]:
            name = row.get("name") or row.get("title") or row.get("subject_name") or "未命名条目"
            reason = row.get("reason") or row.get("note") or ""
            lines.append(f"- {name}" + (f"：{reason}" if reason else ""))
        for note in (section.get("notes") or [])[:3]:
            lines.append(f"  - {note}")
    next_actions = payload.get("next_actions") or []
    if next_actions:
        lines.append("\n## Next")
        lines.extend(f"- {x}" for x in next_actions[:6])
    return "\n".join(lines).strip()


async def _send_webhook(username: str, sub: WeeklyDigestSubscription, item: InboxItem) -> dict[str, Any]:
    if not sub.webhook_url:
        return {"channel": "webhook", "ok": False, "error": "webhook_url empty", "ts": now_iso()}
    payload = {
        "source": "otomo",
        "kind": item.kind,
        "username": username,
        "title": item.title,
        "text": digest_text(item),
        "payload": item.payload,
        "created_at": item.created_at,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.weekly_webhook_timeout) as client:
            resp = await client.post(sub.webhook_url, json=payload)
            resp.raise_for_status()
        return {"channel": "webhook", "ok": True, "status_code": resp.status_code, "ts": now_iso()}
    except Exception as e:  # noqa: BLE001
        return {"channel": "webhook", "ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}", "ts": now_iso()}


def _send_email_sync(username: str, sub: WeeklyDigestSubscription, item: InboxItem) -> dict[str, Any]:
    if not settings.notification_email_enabled:
        return {"channel": "email", "ok": False, "error": "email disabled", "ts": now_iso()}
    if not sub.email:
        return {"channel": "email", "ok": False, "error": "email empty", "ts": now_iso()}
    if not settings.smtp_host or not settings.smtp_from:
        return {"channel": "email", "ok": False, "error": "smtp not configured", "ts": now_iso()}
    msg = EmailMessage()
    msg["Subject"] = item.title
    msg["From"] = settings.smtp_from
    msg["To"] = sub.email
    msg.set_content(f"Hi {username},\n\n{digest_text(item)}\n\n-- Otomo")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        return {"channel": "email", "ok": True, "to": sub.email, "ts": now_iso()}
    except Exception as e:  # noqa: BLE001
        return {"channel": "email", "ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}", "ts": now_iso()}


async def _send_email(username: str, sub: WeeklyDigestSubscription, item: InboxItem) -> dict[str, Any]:
    return await asyncio.to_thread(_send_email_sync, username, sub, item)


async def dispatch_weekly_digest_notifications(
    username: str,
    sub: WeeklyDigestSubscription,
    item: InboxItem,
) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    channels = list(dict.fromkeys(sub.channels or ["inbox"]))
    if "inbox" in channels:
        deliveries.append({"channel": "inbox", "ok": True, "ts": now_iso()})
    tasks = []
    if "webhook" in channels:
        tasks.append(_send_webhook(username, sub, item))
    if "email" in channels:
        tasks.append(_send_email(username, sub, item))
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                deliveries.append({"channel": "unknown", "ok": False, "error": str(result), "ts": now_iso()})
            else:
                deliveries.append(result)
    return deliveries
