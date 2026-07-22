"""Bangumi OAuth session store.

This is a local-file implementation for development. The interface is narrow
enough to replace with Postgres/Redis and encrypted token storage later.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso
from .memory.store import _safe_key
from .tools.bangumi.client import BangumiClient

# 存储路径一律按 CWD 相对解析(和 session/subscription store 一致)——
# 容器 WORKDIR=/app,cache/ 正是挂载卷;绝不能用 parents[N]（容器里 /app/otomo 层级
# 比本地 backend/otomo 浅一层,parents[2] 会算成文件系统根 /,落到没挂载的 /cache,
# 导致 bot 与 backend 读写不同 sqlite + 登录态每次重建丢失）。
_DEFAULT_DIR = Path("cache") / "auth"
_OAUTH_BASE = "https://bgm.tv/oauth"
_STATE_TTL_SECONDS = 600


class OAuthState(BaseModel):
    state: str
    auth_session_id: str
    discord_user_id: str = ""   # 非空=这是一次 Discord 绑定登录，回调成功后绑定
    created_at: float = Field(default_factory=time.time)


class BangumiToken(BaseModel):
    auth_session_id: str
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    user_id: int | None = None
    username: str = ""
    status: str = "active"
    last_error: str = ""
    updated_at: str = ""

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at - 120


class AuthIdentity(BaseModel):
    auth_session_id: str
    authenticated: bool = False
    username: str = ""
    user_id: int | None = None
    token_status: str = ""
    auth_error: str = ""


class AuthSession(BaseModel):
    auth_session_id: str
    csrf_token: str
    created_at: float = Field(default_factory=time.time)
    expires_at: float = 0.0
    updated_at: str = ""

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at


class _TokenCipher:
    def __init__(self, base_dir: Path) -> None:
        key = settings.auth_encryption_key.strip()
        if not key:
            key_path = base_dir / ".fernet_key"
            if key_path.exists():
                key = key_path.read_text(encoding="utf-8").strip()
            else:
                key = Fernet.generate_key().decode("ascii")
                key_path.write_text(key, encoding="utf-8")
        self.fernet = Fernet(key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        token = self.fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"fernet:{token}"

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith("fernet:"):
            # Legacy plaintext cache entry; caller will re-save encrypted.
            return value
        try:
            return self.fernet.decrypt(value.removeprefix("fernet:").encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("auth token decrypt failed; check AUTH_ENCRYPTION_KEY") from e


class AuthStore:
    def __init__(self, base_dir: Path | None = None, *, backend: str | None = None) -> None:
        self._explicit_base = base_dir is not None
        self.base = base_dir or _DEFAULT_DIR
        self.base.mkdir(parents=True, exist_ok=True)
        self.backend = (backend or settings.auth_store_backend or "sqlite").lower()
        self.cipher = _TokenCipher(self.base)
        self.sqlite_path = self._resolve_sqlite_path()
        if self.backend == "sqlite":
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()

    def _path(self, namespace: str, key: str) -> Path:
        return self.base / f"{_safe_key(namespace)}__{_safe_key(key)}.json"

    def _resolve_sqlite_path(self) -> Path:
        if self._explicit_base:
            return self.base / "auth.sqlite3"
        raw = Path(settings.auth_store_path or "cache/auth/auth.sqlite3")
        return raw  # 相对=按 CWD(容器=/app 挂载卷),绝对=原样;不再拼 _ROOT

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.sqlite_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_sqlite(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_kv (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(namespace, key)
                )
                """
            )

    def _write(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        if self.backend == "sqlite":
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO auth_kv(namespace, key, payload, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        payload=excluded.payload,
                        updated_at=excluded.updated_at
                    """,
                    (namespace, key, payload, time.time()),
                )
            return
        self._path(namespace, key).write_text(
            payload, encoding="utf-8"
        )

    def _read(self, namespace: str, key: str) -> dict[str, Any] | None:
        if self.backend == "sqlite":
            with self._connect() as con:
                row = con.execute(
                    "SELECT payload FROM auth_kv WHERE namespace=? AND key=?",
                    (namespace, key),
                ).fetchone()
            if not row:
                # Allow reading legacy JSON cache after switching to sqlite.
                return self._read_file(namespace, key)
            try:
                return json.loads(str(row[0]))
            except json.JSONDecodeError:
                return None
        return self._read_file(namespace, key)

    def _read_file(self, namespace: str, key: str) -> dict[str, Any] | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _delete(self, namespace: str, key: str) -> None:
        if self.backend == "sqlite":
            with self._connect() as con:
                con.execute("DELETE FROM auth_kv WHERE namespace=? AND key=?", (namespace, key))
        try:
            self._path(namespace, key).unlink(missing_ok=True)
        except OSError:
            pass

    def _iter_namespace(self, namespace: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.backend == "sqlite":
            with self._connect() as con:
                raw_rows = con.execute(
                    "SELECT payload FROM auth_kv WHERE namespace=? ORDER BY updated_at DESC",
                    (namespace,),
                ).fetchall()
            for row in raw_rows:
                try:
                    rows.append(json.loads(str(row[0])))
                except json.JSONDecodeError:
                    continue
        for path in self.base.glob(f"{_safe_key(namespace)}__*.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
        return rows

    def _dump_token(self, token: BangumiToken) -> dict[str, Any]:
        raw = token.model_dump(mode="json")
        raw["access_token_enc"] = self.cipher.encrypt(raw.pop("access_token", ""))
        raw["refresh_token_enc"] = self.cipher.encrypt(raw.pop("refresh_token", ""))
        raw["encrypted"] = True
        return raw

    def _load_token(self, raw: dict[str, Any]) -> BangumiToken:
        migrated = False
        if "access_token_enc" in raw:
            raw = dict(raw)
            raw["access_token"] = self.cipher.decrypt(str(raw.pop("access_token_enc") or ""))
            raw["refresh_token"] = self.cipher.decrypt(str(raw.pop("refresh_token_enc", "") or ""))
        elif raw.get("access_token"):
            migrated = True
        token = BangumiToken.model_validate(raw)
        if migrated:
            self.save_token(token)
        return token

    def create_oauth_state(self, auth_session_id: str, discord_user_id: str = "") -> OAuthState:
        state = OAuthState(
            state=secrets.token_urlsafe(24),
            auth_session_id=auth_session_id,
            discord_user_id=discord_user_id,
        )
        self._write("oauth_state", state.state, state.model_dump(mode="json"))
        return state

    # —— Discord 账号绑定 ——
    # 绑定令牌用 Fernet 签名(bot 和 backend 共享 AUTH_ENCRYPTION_KEY),防止伪造 discord_id;
    # 映射 discord_user_id ↔ Bangumi username,bot 据此取该用户 token 做个人化。
    def encode_discord_link(self, discord_user_id: str) -> str:
        import json as _json
        return self.cipher.encrypt(_json.dumps({"d": discord_user_id, "t": time.time()}))

    def decode_discord_link(self, token: str, ttl: float = 900) -> str | None:
        import json as _json
        try:
            payload = _json.loads(self.cipher.decrypt(token))
        except Exception as e:  # noqa: BLE001
            logging.getLogger("otomo.auth").warning(
                "decode_discord_link 解密失败(多半 key 不一致):%s", type(e).__name__)
            return None
        age = time.time() - float(payload.get("t") or 0)
        if age > ttl:
            logging.getLogger("otomo.auth").warning("decode_discord_link 过期:%.0fs", age)
            return None
        return str(payload.get("d") or "") or None

    def create_discord_link_code(self, discord_user_id: str) -> str:
        """生成短码(8 位 hex,URL 无特殊字符)存服务器,替代把加密串塞进 URL——
        后者经 Discord/浏览器/Caddy 一路可能被改坏,短码方案零 URL 编码风险。"""
        code = secrets.token_hex(4)
        self._write("discord_link_code", code, {"discord_user_id": discord_user_id, "created_at": time.time()})
        return code

    def resolve_discord_link_code(self, code: str, ttl: float = 900) -> str | None:
        raw = self._read("discord_link_code", code)
        if not raw or time.time() - float(raw.get("created_at") or 0) > ttl:
            return None
        return str(raw.get("discord_user_id") or "") or None

    def set_discord_link(self, discord_user_id: str, username: str) -> None:
        self._write("discord_link", discord_user_id,
                    {"discord_user_id": discord_user_id, "username": username, "created_at": now_iso()})

    def username_for_discord(self, discord_user_id: str) -> str | None:
        raw = self._read("discord_link", discord_user_id)
        return str(raw["username"]) if raw and raw.get("username") else None

    def discord_for_username(self, username: str) -> str | None:
        for raw in self._iter_namespace("discord_link"):
            if raw.get("username") == username:
                return str(raw.get("discord_user_id") or "") or None
        return None

    def unlink_discord(self, discord_user_id: str) -> None:
        self._delete("discord_link", discord_user_id)

    def consume_oauth_state(self, state_value: str) -> OAuthState:
        raw = self._read("oauth_state", state_value)
        # 一次性：无论有效或过期都立即删除，避免过期 state 文件在磁盘堆积
        self._delete("oauth_state", state_value)
        if raw is None:
            raise ValueError("OAuth state 不存在或已过期")
        state = OAuthState.model_validate(raw)
        if time.time() - state.created_at > _STATE_TTL_SECONDS:
            raise ValueError("OAuth state 已过期")
        return state

    def save_token(self, token: BangumiToken) -> None:
        token.updated_at = now_iso()
        self._write("bangumi_token", token.auth_session_id, self._dump_token(token))

    def load_token(self, auth_session_id: str) -> BangumiToken | None:
        raw = self._read("bangumi_token", auth_session_id)
        if raw is None:
            return None
        try:
            return self._load_token(raw)
        except Exception:  # noqa: BLE001
            return None

    def delete_token(self, auth_session_id: str) -> None:
        self._delete("bangumi_token", auth_session_id)

    def list_tokens(self) -> list[BangumiToken]:
        tokens: list[BangumiToken] = []
        for raw in self._iter_namespace("bangumi_token"):
            try:
                tokens.append(self._load_token(raw))
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
        if token.status != "active":
            return AuthIdentity(
                auth_session_id=auth_session_id,
                token_status=token.status,
                auth_error=token.last_error,
            )
        return AuthIdentity(
            auth_session_id=auth_session_id,
            authenticated=True,
            username=token.username,
            user_id=token.user_id,
            token_status=token.status,
            auth_error=token.last_error,
        )

    def get_or_create_session(self, auth_session_id: str | None = None) -> AuthSession:
        session = self.load_session(auth_session_id or "")
        if session and not session.expired:
            return session
        session = AuthSession(
            auth_session_id=auth_session_id or secrets.token_urlsafe(24),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=time.time() + settings.session_ttl_seconds,
            updated_at=now_iso(),
        )
        self.save_session(session)
        return session

    def load_session(self, auth_session_id: str) -> AuthSession | None:
        if not auth_session_id:
            return None
        raw = self._read("auth_session", auth_session_id)
        if raw is None:
            return None
        try:
            session = AuthSession.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
        if session.expired:
            self.delete_session(auth_session_id)
            return None
        return session

    def save_session(self, session: AuthSession) -> None:
        session.updated_at = now_iso()
        self._write("auth_session", session.auth_session_id, session.model_dump(mode="json"))

    def delete_session(self, auth_session_id: str) -> None:
        self._delete("auth_session", auth_session_id)
        self.delete_token(auth_session_id)


def build_authorization_url(auth_store: AuthStore, auth_session_id: str, discord_user_id: str = "") -> str:
    if not settings.bangumi_oauth_client_id:
        raise RuntimeError("未配置 BANGUMI_OAUTH_CLIENT_ID")
    state = auth_store.create_oauth_state(auth_session_id, discord_user_id)
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
    if state.discord_user_id and token.username:  # 这次是 Discord 绑定登录
        auth_store.set_discord_link(state.discord_user_id, token.username)
        logging.getLogger("otomo.auth").warning(  # warning 级=确保在 uvicorn 默认日志里可见
            "discord 绑定成功: discord=%s ↔ bangumi=%s", state.discord_user_id, token.username)
    return token


async def refresh_oauth_token(auth_store: AuthStore, token: BangumiToken) -> BangumiToken:
    if not token.refresh_token:
        return token
    try:
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
    except Exception as e:  # noqa: BLE001
        token.status = "refresh_failed"
        token.last_error = f"{type(e).__name__}: {str(e)[:180]}"
        auth_store.save_token(token)
        raise
    token.access_token = str(payload.get("access_token") or token.access_token)
    token.refresh_token = str(payload.get("refresh_token") or token.refresh_token)
    token.expires_at = time.time() + float(payload.get("expires_in") or 0)
    token.status = "active"
    token.last_error = ""
    auth_store.save_token(token)
    return token


async def token_for_session(auth_store: AuthStore, auth_session_id: str | None) -> BangumiToken | None:
    if not auth_session_id:
        return None
    token = auth_store.load_token(auth_session_id)
    if token and token.status != "active":
        return None
    if token and token.expired and settings.bangumi_oauth_client_id and settings.bangumi_oauth_client_secret:
        try:
            token = await refresh_oauth_token(auth_store, token)
        except Exception:  # noqa: BLE001
            return None
    return token
