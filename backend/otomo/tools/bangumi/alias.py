"""High-precision subject nickname resolution for Bangumi search.

Bangumi's full-text search does not reliably understand community shorthand such
as ``恋死`` or ``绘死``.  This module only promotes a nickname when every
meaningful character can be anchored to a canonical Chinese/Japanese title and
the candidate is supported by the user's collection or the current season.
Ambiguous candidates stay ambiguous; callers must never pick the first result.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal


ResolutionStatus = Literal["search_only", "exact", "confident_alias", "ambiguous"]


@dataclass(frozen=True)
class AliasCandidate:
    subject: dict[str, Any]
    confidence: float | None = None
    matched_by: str | None = None
    match_note: str | None = None


@dataclass(frozen=True)
class AliasResolution:
    candidates: list[AliasCandidate]
    status: ResolutionStatus = "search_only"
    resolved_subject_id: int | None = None
    note: str = ""


# Characters commonly substituted when Chinese fandom abbreviates Japanese or
# traditional titles.  This is deliberately small: broad semantic synonym sets
# create dangerous false positives for write-back actions.
_CHAR_CANON = str.maketrans({
    "繪": "画",
    "绘": "画",
    "畫": "画",
    "描": "画",
    "戀": "恋",
    "輕": "轻",
    "與": "与",
    "盡": "尽",
    "劇": "剧",
    "動": "动",
})
_KEEP_RE = re.compile(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]")
_EPISODE_SUFFIX_RE = re.compile(r"第?\s*\d+(?:\.\d+)?\s*(?:集|话|話|episode|ep)\s*(?:已看|看完)?", re.I)
_DATE_PREFIX_RE = re.compile(r"20\d{2}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*的)?")
_GENERIC = {"动画", "动漫", "番剧", "新番", "推荐", "评价", "打卡", "标记"}
_SOURCE_BOOST = {"watching": 0.14, "wishlist": 0.08, "season": 0.10, "search": 0.0}


def _today() -> date:
    return date.today()


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().translate(_CHAR_CANON)
    return "".join(ch for ch in text if _KEEP_RE.fullmatch(ch))


def _nickname_key(query: str) -> str:
    text = _DATE_PREFIX_RE.sub("", query.strip())
    text = _EPISODE_SUFFIX_RE.sub("", text)
    for suffix in ("已看", "看完", "标记", "打卡", "动画", "动漫", "番剧"):
        text = text.removesuffix(suffix)
    return _normalize(text)


def _eligible(query: str) -> bool:
    key = _nickname_key(query)
    cjk_count = sum("\u3400" <= ch <= "\u9fff" for ch in key)
    return 2 <= len(key) <= 8 and cjk_count >= 2 and key not in _GENERIC


def _title_match(query_key: str, subject: dict[str, Any]) -> tuple[float, str] | None:
    titles = [_normalize(subject.get("name")), _normalize(subject.get("name_cn"))]
    titles = [title for title in titles if title]
    if not titles:
        return None
    if query_key in titles:
        return 0.99, "exact_title"
    if any(query_key in title for title in titles):
        return 0.96, "title_substring"

    # Numeric/version differences are hard conflicts: Rance 10 must not map to
    # Rance 9.  The remaining characters may match in any order because fandom
    # nicknames often reverse title order (恋死 vs 死…恋…).
    query_numbers = re.findall(r"\d+", query_key)
    if query_numbers and not any(all(number in title for number in query_numbers) for title in titles):
        return None
    meaningful = list(dict.fromkeys(ch for ch in query_key if ch not in {"的", "之", "の", "と"}))
    if len(meaningful) < 2:
        return None
    joined = "".join(titles)
    if not all(ch in joined for ch in meaningful):
        return None
    return min(0.90, 0.76 + 0.04 * len(meaningful)), "title_character_set"


def _quarter_bounds(today: date) -> tuple[str, str]:
    month = ((today.month - 1) // 3) * 3 + 1
    start = date(today.year, month, 1)
    if month == 10:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, month + 3, 1)
    return start.isoformat(), end.isoformat()


async def _collection_candidates(client: Any, subject_type: int) -> list[tuple[dict[str, Any], str]]:
    try:
        me = await client.get_me()
        username = str((me or {}).get("username") or "")
        if not username:
            return []
        watching, wishlist = await asyncio.gather(
            client.get_all_user_collections(username, subject_type, 3, max_items=200),
            client.get_all_user_collections(username, subject_type, 1, max_items=200),
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001 - guest/public search simply has no personal prior
        return []
    out: list[tuple[dict[str, Any], str]] = []
    for rows, source in ((watching, "watching"), (wishlist, "wishlist")):
        if isinstance(rows, BaseException):
            continue
        for row in rows or []:
            subject = row.get("subject") if isinstance(row, dict) else None
            if isinstance(subject, dict) and subject.get("id"):
                out.append((subject, source))
    return out


async def _season_candidates(client: Any) -> list[tuple[dict[str, Any], str]]:
    start, end = _quarter_bounds(_today())
    try:
        pages = await asyncio.gather(
            client.search_subjects("", 2, sort="heat", limit=50, offset=0, air_date=[f">={start}", f"<{end}"]),
            client.search_subjects("", 2, sort="heat", limit=50, offset=50, air_date=[f">={start}", f"<{end}"]),
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001 - ordinary search remains available
        return []
    out: list[tuple[dict[str, Any], str]] = []
    for page in pages:
        if isinstance(page, BaseException) or not isinstance(page, dict):
            continue
        out.extend((subject, "season") for subject in page.get("data") or [] if subject.get("id"))
    return out


async def resolve_subject_alias(
    client: Any,
    query: str,
    direct_subjects: list[dict[str, Any]],
    *,
    subject_type: int | None,
    limit: int,
) -> AliasResolution:
    """Merge deterministic nickname candidates into ordinary Bangumi search."""
    direct = [subject for subject in direct_subjects if isinstance(subject, dict) and subject.get("id")]
    direct_candidates = [AliasCandidate(subject=subject) for subject in direct[:limit]]
    if not _eligible(query):
        return AliasResolution(candidates=direct_candidates)

    key = _nickname_key(query)
    # An exact canonical title is stronger than any nickname heuristic.
    exact = [subject for subject in direct if _title_match(key, subject) == (0.99, "exact_title")]
    if len(exact) == 1:
        top = AliasCandidate(exact[0], 0.99, "bangumi_search+exact_title", "简称与 canonical 标题精确一致")
        rest = [AliasCandidate(subject) for subject in direct if subject.get("id") != exact[0].get("id")]
        return AliasResolution([top, *rest][:limit], "exact", int(exact[0]["id"]), top.match_note or "")

    pool_results = await asyncio.gather(
        _collection_candidates(client, subject_type or 2),
        _season_candidates(client) if subject_type in (None, 2) else asyncio.sleep(0, result=[]),
        return_exceptions=True,
    )
    pooled: list[tuple[dict[str, Any], str]] = [(subject, "search") for subject in direct]
    for rows in pool_results:
        if not isinstance(rows, BaseException):
            pooled.extend(rows)

    merged: dict[int, dict[str, Any]] = {}
    for subject, source in pooled:
        sid = int(subject.get("id") or 0)
        matched = _title_match(key, subject)
        if not sid or matched is None:
            continue
        base, method = matched
        confidence = min(0.99, base + _SOURCE_BOOST[source])
        current = merged.get(sid)
        if current is None:
            merged[sid] = {
                "subject": subject,
                "confidence": confidence,
                "method": method,
                "sources": {source},
            }
        else:
            current["sources"].add(source)
            if confidence > current["confidence"]:
                current.update(subject=subject, confidence=confidence, method=method)

    ranked = sorted(merged.values(), key=lambda item: (-item["confidence"], int(item["subject"]["id"])))
    strong = [item for item in ranked if item["confidence"] >= 0.86]
    if not strong:
        return AliasResolution(candidates=direct_candidates)

    ambiguous = len(strong) > 1 and strong[0]["confidence"] - strong[1]["confidence"] < 0.05
    status: ResolutionStatus = "ambiguous" if ambiguous else "confident_alias"
    resolved_id = None if ambiguous else int(strong[0]["subject"]["id"])
    enriched: list[AliasCandidate] = []
    seen: set[int] = set()
    for item in strong:
        sid = int(item["subject"]["id"])
        seen.add(sid)
        sources = "+".join(sorted(item["sources"], key=lambda x: -_SOURCE_BOOST[x]))
        note = (
            f"简称“{query}”的关键字均命中日文原名/中文名；候选来源：{sources}"
            if not ambiguous
            else f"简称“{query}”存在接近候选，写回前必须让用户选择；候选来源：{sources}"
        )
        enriched.append(AliasCandidate(
            subject=item["subject"],
            confidence=round(float(item["confidence"]), 3),
            matched_by=f"{sources}+{item['method']}",
            match_note=note,
        ))
    enriched.extend(AliasCandidate(subject) for subject in direct if int(subject["id"]) not in seen)
    names = [str(item["subject"].get("name_cn") or item["subject"].get("name") or item["subject"]["id"]) for item in strong[:3]]
    note = (
        f"已将简称“{query}”确定映射到《{names[0]}》。"
        if resolved_id is not None
        else f"简称“{query}”可能指：{'、'.join(names)}；不可直接写回，请让用户确认。"
    )
    return AliasResolution(enriched[:limit], status, resolved_id, note)
