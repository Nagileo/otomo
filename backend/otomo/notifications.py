"""Outbound notification helpers for scheduled digests.

The channel layer is deliberately small and dependency-light:
- inbox is handled by the caller because it writes local memory.
- webhook sends a JSON payload to a user-configured endpoint.
- email uses stdlib SMTP so production can point it at any relay.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import smtplib
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso
from .memory.models import InboxItem


class NotificationTarget(BaseModel):
    """Delivery-only projection; scheduling state lives in SubscriptionRule."""

    channels: list[str] = Field(default_factory=lambda: ["inbox"])
    template: str = "normal"
    email: str = ""
    webhook_url: str = ""
    webhook_format: str = "generic"


_WEBHOOK_HOSTS = {
    "serverchan": {"sctapi.ftqq.com", "sc.ftqq.com"},
    "telegram": {"api.telegram.org"},
    "discord": {"discord.com", "discordapp.com"},
    "feishu": {"open.feishu.cn", "open.larksuite.com"},
}
_MAX_WEBHOOK_URL_LENGTH = 2048
_MAX_GENERIC_PAYLOAD_BYTES = 128 * 1024


async def validate_webhook_url(url: str, fmt: str = "generic") -> str:
    """Reject local/private destinations before an outbound webhook request."""
    raw = (url or "").strip()
    if len(raw) > _MAX_WEBHOOK_URL_LENGTH:
        raise ValueError(f"Webhook URL 不能超过 {_MAX_WEBHOOK_URL_LENGTH} 个字符")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Webhook 仅允许无账号信息的公网 HTTPS URL")
    host = parsed.hostname.rstrip(".").lower()
    allowed = _WEBHOOK_HOSTS.get(fmt)
    if allowed and host not in allowed:
        raise ValueError(f"{fmt} webhook 域名必须是：{', '.join(sorted(allowed))}")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM),
        )
        addresses = list({ipaddress.ip_address(info[4][0]) for info in infos})
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Webhook 不能指向 localhost、内网、链路本地或保留地址")
    return parsed.geturl()


def digest_text(item: InboxItem) -> str:
    payload = item.payload or {}
    lines = [item.title]
    grade = str(payload.get("push_grading") or "normal")
    item_limit = 3 if grade == "brief" else 8 if grade == "normal" else 12
    note_limit = 1 if grade == "brief" else 3 if grade == "normal" else 5
    for section in payload.get("sections") or []:
        title = section.get("title") or "Section"
        lines.append(f"\n## {title}")
        for row in (section.get("items") or [])[:item_limit]:
            name = row.get("name") or row.get("title") or row.get("subject_name") or "未命名条目"
            why = row.get("why") or row.get("reasons") or []
            reason = row.get("reason") or row.get("note") or row.get("action") or ""
            if not reason and why:
                reason = "；".join(str(x) for x in why[:2])
            lines.append(f"- {name}" + (f"：{reason}" if reason else ""))
        for note in (section.get("notes") or [])[:note_limit]:
            lines.append(f"  - {note}")
    next_actions = payload.get("next_actions") or []
    if next_actions:
        lines.append("\n## Next")
        lines.extend(f"- {x}" for x in next_actions[:6])
    return "\n".join(lines).strip()


def _esc(s: object) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def digest_html(username: str, item: InboxItem) -> str:
    """HTML 卡片版 digest(替代纯文本一坨字)。全部内联样式——邮件客户端不认 <style>。"""
    payload = item.payload or {}
    grade = str(payload.get("push_grading") or "normal")
    item_limit = 3 if grade == "brief" else 8 if grade == "normal" else 12
    card = (
        "background:#171a21;border:1px solid #272b36;border-radius:12px;"
        "padding:16px 18px;margin:0 0 14px;"
    )
    sections_html: list[str] = []
    for section in payload.get("sections") or []:
        rows: list[str] = []
        for row in (section.get("items") or [])[:item_limit]:
            name = row.get("name") or row.get("title") or row.get("subject_name") or "未命名条目"
            reason = row.get("summary") or row.get("reason") or row.get("note") or row.get("action") or ""
            if not reason and (why := row.get("why") or row.get("reasons")):
                reason = "；".join(str(x) for x in why[:2])
            url = row.get("url") or (f"https://bgm.tv/subject/{row['id']}" if row.get("id") else "")
            name_html = (
                f'<a href="{_esc(url)}" style="color:#7aa2f7;text-decoration:none;font-weight:600">{_esc(name)}</a>'
                if url else f'<span style="color:#e6e8ee;font-weight:600">{_esc(name)}</span>'
            )
            reason_html = f'<div style="color:#8a90a2;font-size:13px;margin-top:2px">{_esc(reason)}</div>' if reason else ""
            rows.append(
                f'<div style="padding:9px 0;border-bottom:1px solid #23262f">{name_html}{reason_html}</div>'
            )
        if not rows:
            continue
        sections_html.append(
            f'<div style="{card}">'
            f'<div style="color:#7aa2f7;font-size:12px;font-weight:700;letter-spacing:.06em;'
            f'text-transform:uppercase;margin-bottom:6px">{_esc(section.get("title") or "Section")}</div>'
            + "".join(rows) + "</div>"
        )
    next_actions = payload.get("next_actions") or []
    if next_actions:
        items_li = "".join(
            f'<li style="margin:4px 0;color:#cbd0dc">{_esc(x)}</li>' for x in next_actions[:6]
        )
        sections_html.append(
            f'<div style="{card}"><div style="color:#9ece6a;font-size:12px;font-weight:700;'
            f'margin-bottom:6px">NEXT</div><ul style="margin:0;padding-left:18px">{items_li}</ul></div>'
        )
    return (
        '<div style="background:#0f1116;padding:28px 16px;font-family:ui-sans-serif,system-ui,'
        "'Segoe UI','Microsoft YaHei',sans-serif\">"
        '<div style="max-width:560px;margin:0 auto">'
        f'<div style="font-size:19px;font-weight:800;color:#e6e8ee;margin-bottom:2px">{_esc(item.title)}</div>'
        f'<div style="color:#8a90a2;font-size:12px;margin-bottom:16px">Hi {_esc(username)}，这是 Otomo 为你整理的更新</div>'
        + "".join(sections_html)
        + '<div style="color:#5a5f6e;font-size:11px;margin-top:18px">来自 Otomo · 番组搭子 — 可在订阅中心调整推送</div>'
        "</div></div>"
    )


def _telegram_endpoint_and_payload(url: str, text: str) -> tuple[str, dict[str, Any]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    chat_id = (query.get("chat_id") or [""])[0]
    endpoint = urlunparse(parsed._replace(query=""))
    payload: dict[str, Any] = {"text": text[:3900], "disable_web_page_preview": True}
    if chat_id:
        payload["chat_id"] = chat_id
    return endpoint, payload


def _chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in text.splitlines():
        part_len = len(line) + 1
        if cur and cur_len + part_len > limit:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = part_len
        else:
            cur.append(line)
            cur_len += part_len
    if cur:
        chunks.append("\n".join(cur))
    return chunks


async def _send_webhook(username: str, sub: NotificationTarget, item: InboxItem) -> dict[str, Any]:
    if not sub.webhook_url:
        return {"channel": "webhook", "ok": False, "error": "webhook_url empty", "ts": now_iso()}
    text = digest_text(item)
    fmt = sub.webhook_format or "generic"
    try:
        webhook_url = await validate_webhook_url(sub.webhook_url, fmt)
        async with httpx.AsyncClient(
            timeout=settings.weekly_webhook_timeout,
            follow_redirects=False,
        ) as client:
            if fmt == "serverchan":
                # Server酱 标题上限 32 字，超了会 400；desp 不能为空
                resp = await client.post(
                    webhook_url,
                    data={"title": (item.title or "Otomo 推送")[:32], "desp": text or item.title or "（无内容）"},
                )
            elif fmt == "telegram":
                endpoint, payload = _telegram_endpoint_and_payload(webhook_url, text)
                resp = await client.post(endpoint, json=payload)
            elif fmt == "discord":
                # Discord webhook content limit is 2000 chars; split instead of silently truncating.
                responses = []
                for chunk in _chunks(text, 1900)[:5]:
                    responses.append(await client.post(webhook_url, json={"content": chunk}))
                for r in responses:
                    r.raise_for_status()
                resp = responses[-1]
            elif fmt == "feishu":
                resp = await client.post(
                    webhook_url,
                    json={"msg_type": "text", "content": {"text": text[:16000]}},
                )
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
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if len(encoded) > _MAX_GENERIC_PAYLOAD_BYTES:
                    raise ValueError("Webhook 载荷超过 128 KiB，请缩小订阅过滤范围或模板")
                resp = await client.post(webhook_url, json=payload)
            # 不用 raise_for_status（它吞掉响应体）——把第三方的真实错误原因带出来，
            # 否则只看到"400 Bad Request"排不了障（Server酱/飞书的具体原因都在 body 里）。
            if resp.status_code >= 400:
                raise RuntimeError(f"{fmt} {resp.status_code}: {resp.text[:300]}")
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


def _send_email_sync(username: str, sub: NotificationTarget, item: InboxItem) -> dict[str, Any]:
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
    # multipart: 纯文本兜底 + HTML 卡片(邮件客户端只认内联样式)
    msg.set_content(f"Hi {username},\n\n{digest_text(item)}\n\n-- Otomo")
    msg.add_alternative(digest_html(username, item), subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        return {"channel": "email", "ok": True, "to": sub.email, "ts": now_iso()}
    except Exception as e:  # noqa: BLE001
        return {"channel": "email", "ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}", "ts": now_iso()}


async def _send_email(username: str, sub: NotificationTarget, item: InboxItem) -> dict[str, Any]:
    return await asyncio.to_thread(_send_email_sync, username, sub, item)


async def _send_discord_dm(username: str, item: InboxItem) -> dict[str, Any]:
    """用 bot token 私信已绑定 Bangumi 账号的 Discord 用户(无需 webhook_url,
    按 username 反查 discord_user_id)。Discord 是最佳推送渠道:无条数限制、排版好。"""
    token = settings.discord_bot_token
    if not token:
        return {"channel": "discord_dm", "ok": False, "error": "未配置 DISCORD_BOT_TOKEN", "ts": now_iso()}
    from .auth import AuthStore  # 延迟导入避免循环
    discord_id = AuthStore().discord_for_username(username)
    if not discord_id:
        return {"channel": "discord_dm", "ok": False, "error": f"{username} 未绑定 Discord", "ts": now_iso()}
    text = digest_text(item)
    headers = {"Authorization": f"Bot {token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.weekly_webhook_timeout) as client:
            # 开私信频道
            r = await client.post(
                "https://discord.com/api/v10/users/@me/channels",
                json={"recipient_id": str(discord_id)}, headers=headers,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"open DM {r.status_code}: {r.text[:200]}")
            channel_id = r.json()["id"]
            for chunk in _chunks(f"**{item.title}**\n{text}", 1900)[:5]:
                m = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    json={"content": chunk}, headers=headers,
                )
                if m.status_code >= 400:
                    raise RuntimeError(f"send DM {m.status_code}: {m.text[:200]}")
        return {"channel": "discord_dm", "ok": True, "ts": now_iso()}
    except Exception as e:  # noqa: BLE001
        return {"channel": "discord_dm", "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "ts": now_iso()}


async def dispatch_notifications(
    username: str,
    sub: NotificationTarget,
    item: InboxItem,
) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    channels = list(dict.fromkeys(sub.channels or ["inbox"]))
    tasks = []
    if "webhook" in channels:
        tasks.append(_send_webhook(username, sub, item))
    if "email" in channels:
        tasks.append(_send_email(username, sub, item))
    if "discord_dm" in channels:
        tasks.append(_send_discord_dm(username, item))
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                deliveries.append({"channel": "unknown", "ok": False, "error": str(result), "ts": now_iso()})
            else:
                deliveries.append(result)
    return deliveries
