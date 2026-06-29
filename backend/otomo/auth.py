"""Bangumi OAuth session store.

This is a local-file implementation for development. The interface is narrow
enough to replace with Postgres/Redis and encrypted token storage later.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso
from .memory.store import _safe_key
from .tools.bangumi.client import BangumiClient

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "cache" / "auth"
_OAUTH_BASE = "https://bgm.tv/oauth"
_STATE_TTL_SECONDS = 600


class OAuthState(BaseModel):
    state: str
    auth_session_id: str
    created_at: float = Field(default_factory=time.time)


class BangumiToken(BaseModel):
    auth_session_id: str
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    user_id: int | None = None
    username: str = ""
    updated_at: str = ""

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at - 120


class AuthIdentity(BaseModel):
    auth_session_id: str
    authenticated: bool = False
    username: str = ""
    user_id: int | None = None


class AuthStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or _DEFAULT_DIR
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        return self.base / f"{_safe_key(namespace)}__{_safe_key(key)}.json"

    def _write(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        self._path(namespace, key).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read(self, namespace: str, key: str) -> dict[str, Any] | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def create_oauth_state(self, auth_session_id: str) -> OAuthState:
        state = OAuthState(state=secrets.token_urlsafe(24), auth_session_id=auth_session_id)
        self._write("oauth_state", state.state, state.model_dump(mode="json"))
        return state

    def consume_oauth_state(self, state_value: str) -> OAuthState:
        raw = self._read("oauth_state", state_value)
        # 一次性：无论有效或过期都立即删除，避免过期 state 文件在磁盘堆积
        try:
            self._path("oauth_state", state_value).unlink(missing_ok=True)
        except OSError:
            pass
        if raw is None:
            raise ValueError("OAuth state 不存在或已过期")
        state = OAuthState.model_validate(raw)
        if time.time() - state.created_at > _STATE_TTL_SECONDS:
            raise ValueError("OAuth state 已过期")
        return state

    def save_token(self, token: BangumiToken) -> None:
        token.updated_at = now_iso()
        self._write("bangumi_token", token.auth_session_id, token.model_dump(mode="json"))

    def load_token(self, auth_session_id: str) -> BangumiToken | None:
        raw = self._read("bangumi_token", auth_session_id)
        if raw is None:
            return None
        try:
            return BangumiToken.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    def delete_token(self, auth_session_id: str) -> None:
        try:
            self._path("bangumi_token", auth_session_id).unlink(missing_ok=True)
        except OSError:
            pass

    def list_tokens(self) -> list[BangumiToken]:
        tokens: list[BangumiToken] = []
        for path in self.base.glob("bangumi_token__*.json"):
            try:
                tokens.append(BangumiToken.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                continue
        return tokens

    def token_for_username(self, username: str) -> BangumiToken | None:
        for token in self.list_tokens():
            if token.username == username:
                return token
        return None

    def identity(self, auth_session_id: str) -> AuthIdentity:
        token = self.load_token(auth_session_id)
        if not token:
            return AuthIdentity(auth_session_id=auth_session_id)
        return AuthIdentity(
            auth_session_id=auth_session_id,
            authenticated=True,
            username=token.username,
            user_id=token.user_id,
        )


def build_authorization_url(auth_store: AuthStore, auth_session_id: str) -> str:
    if not settings.bangumi_oauth_client_id:
        raise RuntimeError("未配置 BANGUMI_OAUTH_CLIENT_ID")
    state = auth_store.create_oauth_state(auth_session_id)
    query = {
        "client_id": settings.bangumi_oauth_client_id,
        "response_type": "code",
        "redirect_uri": settings.bangumi_oauth_redirect_uri,
        "state": state.state,
    }
    return f"{_OAUTH_BASE}/authorize?{urlencode(query)}"


async def exchange_oauth_code(auth_store: AuthStore, code: str, state_value: str) -> BangumiToken:
    if not settings.bangumi_oauth_client_id or not settings.bangumi_oauth_client_secret:
        raise RuntimeError("未配置 Bangumi OAuth client id/secret")
    state = auth_store.consume_oauth_state(state_value)
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.post(
            f"{_OAUTH_BASE}/access_token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.bangumi_oauth_client_id,
                "client_secret": settings.bangumi_oauth_client_secret,
                "code": code,
                "redirect_uri": settings.bangumi_oauth_redirect_uri,
                "state": state_value,
            },
            headers={"User-Agent": settings.bangumi_user_agent},
        )
        resp.raise_for_status()
        payload = resp.json()
    token = BangumiToken(
        auth_session_id=state.auth_session_id,
        access_token=str(payload.get("access_token") or ""),
        refresh_token=str(payload.get("refresh_token") or ""),
        token_type=str(payload.get("token_type") or "Bearer"),
        expires_at=time.time() + float(payload.get("expires_in") or 0),
        user_id=payload.get("user_id"),
    )
    async with BangumiClient(token=token.access_token) as bgm:
        me = await bgm.get_me()
    token.username = str(me.get("username") or token.user_id or "")
    if me.get("id") is not None:
        token.user_id = int(me["id"])
    auth_store.save_token(token)
    return token


async def refresh_oauth_token(auth_store: AuthStore, token: BangumiToken) -> BangumiToken:
    if not token.refresh_token:
        return token
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.post(
            f"{_OAUTH_BASE}/access_token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.bangumi_oauth_client_id,
                "client_secret": settings.bangumi_oauth_client_secret,
                "refresh_token": token.refresh_token,
                "redirect_uri": settings.bangumi_oauth_redirect_uri,
            },
            headers={"User-Agent": settings.bangumi_user_agent},
        )
        resp.raise_for_status()
        payload = resp.json()
    token.access_token = str(payload.get("access_token") or token.access_token)
    token.refresh_token = str(payload.get("refresh_token") or token.refresh_token)
    token.expires_at = time.time() + float(payload.get("expires_in") or 0)
    auth_store.save_token(token)
    return token


async def token_for_session(auth_store: AuthStore, auth_session_id: str | None) -> BangumiToken | None:
    if not auth_session_id:
        return None
    token = auth_store.load_token(auth_session_id)
    if token and token.expired and settings.bangumi_oauth_client_id and settings.bangumi_oauth_client_secret:
        try:
            token = await refresh_oauth_token(auth_store, token)
        except Exception:  # noqa: BLE001
            return token
    return token
