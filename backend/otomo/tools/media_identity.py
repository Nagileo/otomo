"""Shared anime identity and installment-scope checks.

External catalogues describe the same work with very different titles.  This
module deliberately keeps the decision small and explainable: title aliases,
installment markers and media format are evaluated once, then reused by video
and release discovery.
"""
from __future__ import annotations

import html
import re
from typing import Any, Literal

from pydantic import BaseModel, Field


MediaKind = Literal["tv", "web", "movie", "ova", "unknown"]
ScopeStatus = Literal["exact", "compatible", "bundle", "conflict", "unknown"]


class MediaIdentity(BaseModel):
    subject_id: int | None = None
    title: str
    aliases: list[str] = Field(default_factory=list)
    platform: str = ""
    air_date: str = ""
    end_date: str = ""
    year: int | None = None
    installment: int | None = None
    media_kind: MediaKind = "unknown"
    episode_count: int | None = None
    version_markers: list[str] = Field(default_factory=list)


class MediaScopeAssessment(BaseModel):
    status: ScopeStatus
    reason: str
    title_matched: bool = False
    candidate_installments: list[int] = Field(default_factory=list)
    candidate_kind: MediaKind = "unknown"


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_MOVIE_TOKENS = ("剧场版", "劇場版", "电影版", "電影版", "the movie", "feature film")
_OVA_TOKENS = ("ova", "oad")
_BUNDLE_TOKENS = (
    "全季", "全系列", "系列合集", "季度合集", "complete series", "complete collection",
    "season collection", "s1+s2", "s1 s2", "1-2季", "一二季", "两季合集", "兩季合集",
)
_ANCILLARY_CJK_TOKENS = (
    "演唱会", "演唱會", "音乐会", "音樂會", "ライブ", "原声集", "原聲集", "角色歌",
    "广播剧", "廣播劇", "画集", "畫集", "设定集", "設定集", "扫图", "掃圖", "游戏", "遊戲",
)
_ANCILLARY_ASCII_TOKENS = (
    "live event", "concert", "live cd", "music history", "soundtrack", "original soundtrack",
    "ost", "drama cd", "booklet", "scan", "game soundtrack", "game",
)


def _infobox_value(raw: dict[str, Any], keys: set[str]) -> str:
    for row in raw.get("infobox") or []:
        if not isinstance(row, dict) or str(row.get("key") or "").strip() not in keys:
            continue
        value = row.get("value")
        if isinstance(value, list):
            values = [str(item.get("v") if isinstance(item, dict) else item).strip() for item in value]
            return " / ".join(item for item in values if item)
        return str(value or "").strip()
    return ""


def _year(value: str) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    return int(match.group(0)) if match else None


def _version_markers(values: list[str], year: int | None) -> list[str]:
    text = " ".join(values).lower()
    markers: list[str] = []
    if any(token in text for token in ("重制", "リメイク", "remake", "新版", "新作版")):
        markers.append("remake")
    if any(token in text for token in ("总集篇", "總集篇", "総集編", "recap")):
        markers.append("recap")
    if any(token in text for token in ("part 2", "part2", "后篇", "後篇", "下篇")):
        markers.append("part_2")
    if year:
        markers.append(str(year))
    return list(dict.fromkeys(markers))


def _chinese_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "百" in value:
        left, right = value.split("百", 1)
        tail = _chinese_number(right) if right else 0
        return _CN_DIGITS.get(left, 1) * 100 + (tail or 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return _CN_DIGITS.get(left, 1) * 10 + _CN_DIGITS.get(right, 0)
    return _CN_DIGITS.get(value)


def normalize_media_title(value: str) -> str:
    return "".join(ch.lower() for ch in html.unescape(value or "") if ch.isalnum())


def _base_title_key(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"第\s*[0-9一二三四五六七八九十百]+\s*[季期]", " ", text, flags=re.I)
    text = re.sub(r"(?:season|series)\s*0*[0-9]+", " ", text, flags=re.I)
    text = re.sub(r"(?<![a-z0-9])s\s*0*[0-9]+(?![a-z0-9])", " ", text, flags=re.I)
    text = re.sub(r"剧场版|劇場版|电影版|電影版|the movie|ova|oad", " ", text, flags=re.I)
    return normalize_media_title(text)


def _installments(value: str) -> list[int]:
    text = html.unescape(value or "")
    found: set[int] = set()
    for match in re.finditer(r"第\s*([0-9一二三四五六七八九十百]+)\s*[季期]", text, re.I):
        if (number := _chinese_number(match.group(1))) is not None:
            found.add(number)
    for match in re.finditer(r"(?:season|series)\s*0*([0-9]+)", text, re.I):
        found.add(int(match.group(1)))
    for match in re.finditer(r"(?<![a-z0-9])s\s*0*([0-9]+)(?![a-z0-9])", text, re.I):
        found.add(int(match.group(1)))
    for match in re.finditer(
        r"(?:s(?:eason)?\s*)?0*([0-9]+)\s*[-~–—+]\s*(?:s(?:eason)?\s*)?0*([0-9]+)\s*(?:季|season)?",
        text,
        re.I,
    ):
        start, end = int(match.group(1)), int(match.group(2))
        if 0 < start <= end <= 20:
            found.update(range(start, end + 1))
    return sorted(number for number in found if 0 < number <= 100)


def _media_kind(value: str, platform: str = "") -> MediaKind:
    lower = f"{value} {platform}".lower()
    if (
        any(token in lower for token in _MOVIE_TOKENS)
        or bool(re.search(r"(?<![a-z])movie(?![a-z])", lower))
        or platform.lower() in {"movie", "电影", "電影", "剧场版", "劇場版"}
    ):
        return "movie"
    if any(re.search(rf"(?<![a-z]){token}(?![a-z])", lower) for token in _OVA_TOKENS):
        return "ova"
    platform_lower = platform.lower().strip()
    if platform_lower == "web":
        return "web"
    if platform_lower in {"tv", "テレビ"}:
        return "tv"
    return "unknown"


def _alias_values(raw: dict[str, Any]) -> list[str]:
    values = [str(raw.get("name_cn") or "").strip(), str(raw.get("name") or "").strip()]
    for row in raw.get("infobox") or []:
        if not isinstance(row, dict) or str(row.get("key") or "") not in {"别名", "別名", "英文名", "日文名", "中文名"}:
            continue
        value = row.get("value")
        if isinstance(value, list):
            for item in value:
                values.append(str(item.get("v") if isinstance(item, dict) else item).strip())
        else:
            values.extend(x.strip() for x in re.split(r"[/、,，\n]", str(value or "")) if x.strip())
    return list(dict.fromkeys(value for value in values if value))[:16]


def media_identity_from_subject(raw: dict[str, Any], *, fallback_title: str = "") -> MediaIdentity:
    aliases = _alias_values(raw)
    title = str(raw.get("name_cn") or raw.get("name") or fallback_title or raw.get("id") or "").strip()
    if title and title not in aliases:
        aliases.insert(0, title)
    platform = str(raw.get("platform") or "")
    air_date = str(raw.get("date") or "")
    end_date = _infobox_value(raw, {"播放结束", "放送结束", "上映结束", "发售日", "発売日"})
    year = _year(air_date)
    installments = _installments(" ".join([*aliases, platform]))
    try:
        episode_count = int(raw.get("eps") or 0) or None
    except (TypeError, ValueError):
        episode_count = None
    return MediaIdentity(
        subject_id=int(raw["id"]) if raw.get("id") else None,
        title=title,
        aliases=aliases,
        platform=platform,
        air_date=air_date,
        end_date=end_date,
        year=year,
        installment=installments[0] if len(installments) == 1 else None,
        media_kind=_media_kind(" ".join(aliases), platform),
        episode_count=episode_count,
        version_markers=_version_markers([*aliases, platform], year),
    )


def build_media_identity(
    *,
    title: str,
    aliases: list[str] | None = None,
    subject_id: int | None = None,
    platform: str = "",
    air_date: str = "",
    end_date: str = "",
    episode_count: int | None = None,
) -> MediaIdentity:
    values = list(dict.fromkeys(x.strip() for x in [title, *(aliases or [])] if x.strip()))
    installments = _installments(" ".join([*values, platform]))
    return MediaIdentity(
        subject_id=subject_id,
        title=title,
        aliases=values,
        platform=platform,
        air_date=air_date,
        end_date=end_date,
        year=_year(air_date),
        installment=installments[0] if len(installments) == 1 else None,
        media_kind=_media_kind(" ".join(values), platform),
        episode_count=episode_count,
        version_markers=_version_markers([*values, platform], _year(air_date)),
    )


def assess_media_scope(
    identity: MediaIdentity,
    candidate_title: str,
    page_titles: list[str] | None = None,
) -> MediaScopeAssessment:
    """Explain whether an external item belongs to the selected installment."""
    candidate = " ".join([candidate_title, *(page_titles or [])]).strip()
    normalized_candidate = normalize_media_title(candidate)
    alias_keys = [normalize_media_title(alias) for alias in identity.aliases if len(normalize_media_title(alias)) >= 3]
    alias_base_keys = [_base_title_key(alias) for alias in identity.aliases if len(_base_title_key(alias)) >= 3]
    candidate_base = _base_title_key(candidate)
    matched = any(key in normalized_candidate or normalized_candidate in key for key in alias_keys) or any(
        key in candidate_base or candidate_base in key for key in alias_base_keys
    )
    installments = _installments(candidate)
    kind = _media_kind(candidate)
    candidate_year = _year(candidate)
    lower = candidate.lower()
    has_bundle_token = any(token in lower for token in _BUNDLE_TOKENS)
    collection_word = any(token in lower for token in ("合集", "合輯", "collection"))
    format_mix = (
        any(token in lower for token in ("剧场版", "劇場版", "movie", "电影版", "電影版"))
        and any(re.search(rf"(?<![a-z]){token}(?![a-z])", lower) for token in _OVA_TOKENS)
    )
    ancillary_hits = [token for token in _ANCILLARY_CJK_TOKENS if token in lower]
    ancillary_hits.extend(
        token for token in _ANCILLARY_ASCII_TOKENS
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", lower)
    )
    if ancillary_hits:
        return MediaScopeAssessment(
            status="conflict",
            reason="候选是演唱会、音乐、画集/设定集或游戏等衍生内容，不是当前动画正片资源",
            title_matched=matched,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    cross_format_collection = collection_word and (
        kind in {"movie", "ova"}
        or "+" in candidate
        or bool(re.search(r"\d+\s*\+\s*\d+", candidate))
    )
    if len(installments) > 1 or has_bundle_token or format_mix or cross_format_collection:
        return MediaScopeAssessment(
            status="bundle",
            reason="候选包含多季、多个篇章或系列合集；需打开源站确认后再选择",
            title_matched=matched,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if not matched:
        return MediaScopeAssessment(
            status="unknown",
            reason="标题未与当前条目的任一别名可靠对齐",
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if identity.year and candidate_year and identity.year != candidate_year:
        return MediaScopeAssessment(
            status="conflict",
            reason=f"候选标注 {candidate_year} 年，与当前 {identity.year} 年版本不一致",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    current_installment = identity.installment
    candidate_installment = installments[0] if installments else None
    if current_installment and candidate_installment and current_installment != candidate_installment:
        return MediaScopeAssessment(
            status="conflict",
            reason=f"候选明确标注第 {candidate_installment} 季，与当前第 {current_installment} 季冲突",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if current_installment is None and candidate_installment and candidate_installment >= 2:
        return MediaScopeAssessment(
            status="conflict",
            reason=f"当前条目未标续作编号，候选却明确标注第 {candidate_installment} 季",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if identity.media_kind in {"tv", "web"} and kind in {"movie", "ova"}:
        label = "剧场版/电影" if kind == "movie" else "OVA/OAD"
        return MediaScopeAssessment(
            status="conflict",
            reason=f"候选明确标注为{label}，与当前 {identity.platform or identity.media_kind.upper()} 条目冲突",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if identity.media_kind in {"movie", "ova"} and kind not in {"unknown", identity.media_kind}:
        return MediaScopeAssessment(
            status="conflict",
            reason="候选媒介形态与当前电影/OVA篇章不一致",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    if (current_installment and candidate_installment == current_installment) or (
        identity.media_kind != "unknown" and kind == identity.media_kind
    ):
        return MediaScopeAssessment(
            status="exact",
            reason="标题、季数或媒介形态与当前条目明确一致",
            title_matched=True,
            candidate_installments=installments,
            candidate_kind=kind,
        )
    return MediaScopeAssessment(
        status="compatible",
        reason="标题别名一致，且未发现跨季、剧场版或 OVA 冲突",
        title_matched=True,
        candidate_installments=installments,
        candidate_kind=kind,
    )
