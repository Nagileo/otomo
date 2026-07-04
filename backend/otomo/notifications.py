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
from urllib.parse import parse_qs, urlparse, urlunparse

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
            why = row.get("why") or row.get("reasons") or []
            reason = row.get("reason") or row.get("note") or row.get("action") or ""
            if not reason and why:
                reason = "；".join(str(x) for x in why[:2])
            lines.append(f"- {name}" + (f"：{reason}" if reason else ""))
        for note in (section.get("notes") or [])[:3]:
            lines.append(f"  - {note}")
    next_actions = payload.get("next_actions") or []
    if next_actions:
        lines.append("\n## Next")
        lines.extend(f"- {x}" for x in next_actions[:6])
    return "\n".join(lines).strip()


def _telegram_endpoint_and_payload(url: str, text: str) -> tuple[str, dict[str, Any]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    chat_id = (query.get("chat_id") or [""])[0]
    endpoint = urlunparse(parsed._replace(query=""))
    payload: dict[str, Any] = {"text": text[:3900], "disable_web_page_preview": True}
    if chat_id:
        payload["chat_id"] = chat_id
    return endpoint, payload


async def _send_webhook(username: str, sub: WeeklyDigestSubscription, item: InboxItem) -> dict[str, Any]:
    if not sub.webhook_url:
        return {"channel": "webhook", "ok": False, "error": "webhook_url empty", "ts": now_iso()}
    text = digest_text(item)
    fmt = sub.webhook_format or "generic"
    try:
        async with httpx.AsyncClient(timeout=settings.weekly_webhook_timeout) as client:
            if fmt == "serverchan":
                resp = await client.post(sub.webhook_url, data={"title": item.title, "desp": text})
            elif fmt == "telegram":
                endpoint, payload = _telegram_endpoint_and_payload(sub.webhook_url, text)
                resp = await client.post(endpoint, json=payload)
            else:
                payload = {
                    "source": "otomo",
                    "kind": item.kind,
                    "username": username,
                    "title": item.title,
                    "text": text,
                    "payload": item.payload,
                    "created_at": item.created_at,
                }
                resp = await client.post(sub.webhook_url, json=payload)
            resp.raise_for_status()
        return {
            "channel": "webhook",
            "format": fmt,
            "ok": True,
            "status_code": resp.status_code,
            "ts": now_iso(),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "channel": "webhook",
            "format": fmt,
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:180]}",
            "ts": now_iso(),
        }


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
