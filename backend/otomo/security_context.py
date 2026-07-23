"""Request-scoped tenant and write authorization guards.

The browser session cookie is resolved by the API. Tool arguments are model
output and therefore must never be treated as an authorization boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import re
from typing import Iterator


class TenantAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class RuntimePrincipal:
    username: str
    authenticated: bool


_PRINCIPAL: contextvars.ContextVar[RuntimePrincipal | None] = contextvars.ContextVar(
    "otomo_runtime_principal", default=None
)


@contextmanager
def tenant_scope(username: str | None, *, authenticated: bool) -> Iterator[None]:
    token = _PRINCIPAL.set(RuntimePrincipal((username or "").strip(), authenticated))
    try:
        yield
    finally:
        _PRINCIPAL.reset(token)


def current_principal() -> RuntimePrincipal | None:
    return _PRINCIPAL.get()


def assert_private_user(username: str) -> None:
    """Reject model-selected access to another user's private Otomo data.

    No principal means a trusted offline/background caller. HTTP and Discord
    agent runs always install a principal before tools execute.
    """
    principal = current_principal()
    if principal is None:
        return
    requested = (username or "").strip()
    if not principal.authenticated or not principal.username:
        raise TenantAccessError("需要先登录，才能访问私有记忆或执行账号操作")
    if requested.casefold() != principal.username.casefold():
        raise TenantAccessError("不能访问或修改其他用户的私有数据")


def can_access_private_user(username: str) -> bool:
    """Whether the current caller may use this user's local private state."""
    principal = current_principal()
    if principal is None:
        return True
    requested = (username or "").strip()
    return bool(
        principal.authenticated
        and principal.username
        and requested.casefold() == principal.username.casefold()
    )


def authorized_write_tools(user_input: str) -> set[str]:
    """Derive write permission from the raw user turn, never from model args."""
    text = re.sub(r"\s+", "", user_input or "").lower()
    allowed: set[str] = set()
    negated = re.search(r"(?:不要|别|不|尚未|还没|没有)(?:确认|执行|写回|同步|推送|撤销|回滚)", text)
    undo = None if negated else re.search(
        r"(?:撤销|回滚|undo)(?:吧|刚才|上次|这个|操作|写回|修改)?",
        text,
    )
    execute = None if negated else re.search(
        r"(?:确认(?:执行|写回|添加|加入|同步|推送)|"
        r"直接(?:确认)?(?:执行|添加|加入|写回|同步|推送)|"
        r"(?:执行|写回|同步|推送)(?:吧|它|这个操作)|"
        r"同意执行|可以执行|就这么办|^确认$|^同意$)",
        text,
    )
    if undo:
        allowed.add("undo_bangumi_write_action")
    if execute:
        allowed.add("execute_bangumi_write_action")
    return allowed
