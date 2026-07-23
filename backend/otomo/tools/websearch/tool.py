"""全网搜索工具（外部知识增强，**补充非主体**）。

定位（见 docs/04 外部知识增强）：graph+wiki 答不了（最新资讯、粉丝话语、跨源综述）时的兜底。
provider 可换（Tavily/Exa/Serper），无 key 时优雅报"未配置"。结果**标 web 来源、低置信、必挂链接**，
不与 Bangumi 可验证事实混淆。每次调用临时建 httpx，无需管理生命周期。
"""
from __future__ import annotations

import ipaddress
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

# 降级兜底顺序（免费/便宜优先）：首选引擎失败或配额满时按此顺序往下试
_FALLBACK_ORDER = ["tavily", "serper", "bocha", "exa"]


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="搜索词；查最新资讯/粉丝讨论/跨源信息时用")
    max_results: int = Field(5, ge=1, le=10)
    high_quality: bool = Field(
        False, description="需要更高质量/中文粉丝话语/深度综述时设 true，升级到更强引擎；普通查询留 false（免费引擎）"
    )


class WebHit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    url: str
    snippet: str = ""


class WebSearchResult(BaseModel):
    query: str
    provider: str
    hits: list[WebHit] = Field(default_factory=list)


class UrlSummaryArgs(BaseModel):
    url: str = Field(..., description="要按需读取的公开网页 URL")
    query: str | None = Field(None, description="可选关注点/关键词，用于优先挑相关片段")
    max_chars: int = Field(1800, ge=400, le=5000, description="最多返回多少字符的清洗正文")


class UrlSummaryResult(BaseModel):
    url: str
    title: str = ""
    source_role: str = "discourse"
    text: str = ""
    highlights: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class BrowserFetchArgs(BaseModel):
    url: str = Field(..., description="要读取的公开网页 URL")
    query: str | None = Field(None, description="可选关注点/关键词")
    max_chars: int = Field(2400, ge=500, le=8000)
    render: Literal["auto", "always", "never"] = Field("auto", description="auto=静态不足再浏览器渲染")
    scrolls: int = Field(2, ge=0, le=6, description="浏览器模式下有限滚动次数")


class BrowserFetchResult(BaseModel):
    url: str
    title: str = ""
    render_mode: Literal["static", "browser", "static_fallback"] = "static"
    source_role: str = "discourse"
    text: str = ""
    highlights: list[str] = Field(default_factory=list)
    links: list[WebHit] = Field(default_factory=list)
    status_code: int | None = None
    caveats: list[str] = Field(default_factory=list)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = False
        self.title_mode = False
        self.title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self.skip = True
        if tag == "title":
            self.title_mode = True
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self.skip = False
        if tag == "title":
            self.title_mode = False
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self.title_mode:
            self.title_parts.append(text)
        else:
            self.parts.append(text)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = re.sub(r"\s+", " ", raw)
        return raw.strip()


def _highlights(text: str, query: str | None, limit: int = 6) -> list[str]:
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if len(s.strip()) >= 8]
    if query:
        terms = [t for t in re.split(r"\s+", query) if t]
        sentences.sort(key=lambda s: 0 if any(t in s for t in terms) else 1)
    return [s[:220] for s in sentences[:limit]]


def _validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https URL")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL 缺少 host")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("不允许读取 localhost/local 地址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("不允许读取内网/保留 IP 地址")
    return url


async def _fetch_static_url(url: str, query: str | None, max_chars: int) -> tuple[UrlSummaryResult, int | None]:
    _validate_public_url(url)
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.bangumi_user_agent},
    ) as c:
        r = await c.get(url)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise ValueError(f"暂不摘要该 content-type：{content_type}")
        raw = r.text
    parser = _TextExtractor()
    parser.feed(raw)
    clean = parser.text[:max_chars]
    title = parser.title or str(r.url)
    return (
        UrlSummaryResult(
            url=str(r.url),
            title=title[:120],
            text=clean,
            highlights=_highlights(clean, query),
            caveats=[
                "按需 URL 摘要是网页话语源，不是 canonical 事实源。",
                "只读取单页公开内容，不做站点级爬取；登录墙/反爬/动态渲染页面可能缺失正文。",
            ],
        ),
        r.status_code,
    )


async def _browser_fetch(url: str, query: str | None, max_chars: int, scrolls: int) -> BrowserFetchResult:
    _validate_public_url(url)
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError("未安装 Playwright：pip install -e .[browser] && playwright install chromium") from e

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 900},
            user_agent=settings.bangumi_user_agent,
        )
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_fetch_timeout_ms)
            await page.wait_for_timeout(800)
            for _ in range(min(scrolls, settings.browser_fetch_max_scrolls)):
                await page.mouse.wheel(0, 1400)
                await page.wait_for_timeout(500)
            title = (await page.title())[:120]
            text = await page.locator("body").inner_text(timeout=settings.browser_fetch_timeout_ms)
            links_raw = await page.eval_on_selector_all(
                "a[href]",
                """els => els.slice(0, 40).map(a => ({
                    title: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                    url: a.href,
                    snippet: ''
                }))""",
            )
            clean = re.sub(r"\s+", " ", text).strip()[:max_chars]
            links = [
                WebHit(title=(x.get("title") or x.get("url") or "")[:80], url=x.get("url") or "", snippet="")
                for x in links_raw if isinstance(x, dict) and x.get("url")
            ][:12]
            return BrowserFetchResult(
                url=page.url,
                title=title or page.url,
                render_mode="browser",
                text=clean,
                highlights=_highlights(clean, query),
                links=links,
                status_code=resp.status if resp else None,
                caveats=[
                    "浏览器摘要读取的是当前可见公开页面，可能受登录墙、地区、反爬、懒加载影响。",
                    "该结果属于 discourse/web source，不参与 canonical 事实判断。",
                ],
            )
        finally:
            await browser.close()


async def _search(provider: str, api_key: str, query: str, n: int, timeout: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        if provider == "tavily":
            r = await c.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": n, "search_depth": "basic"},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("content") or "")[:300]}
                for x in (r.json().get("results") or [])
            ]
        if provider == "exa":
            r = await c.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key},
                json={"query": query, "numResults": n, "contents": {"text": {"maxCharacters": 300}}},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("text") or "")[:300]}
                for x in (r.json().get("results") or [])
            ]
        if provider == "serper":
            r = await c.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key},
                json={"q": query, "num": n},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in (r.json().get("organic") or [])
            ]
        if provider == "bocha":
            r = await c.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "summary": True, "count": n},
            )
            r.raise_for_status()
            pages = ((r.json().get("data") or {}).get("webPages") or {}).get("value") or []
            return [
                {"title": x.get("name", ""), "url": x.get("url", ""),
                 "snippet": (x.get("summary") or x.get("snippet") or "")[:300]}
                for x in pages
            ]
        raise ValueError(f"未知 websearch provider: {provider}")


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "全网搜索兜底：当 Bangumi 图谱与萌娘/维基都答不了（最新资讯、粉丝讨论/二创氛围、跨源综述）时用。"
        "结果是**网络来源、可能不准**——作答时必须挂链接、说明是网络信息、别与已验证事实混为一谈。"
    )
    args_model = WebSearchArgs
    result_model = WebSearchResult

    def __init__(self, provider: str | None = None) -> None:
        self.primary = (provider or settings.websearch_provider).lower()
        self.quality = settings.websearch_quality_provider.lower()

    def _chain(self, high_quality: bool) -> list[str]:
        """降级链：首选引擎在前，其余按兜底顺序在后，只保留已配 key 的。"""
        preferred = self.quality if high_quality else self.primary
        order = [preferred] + [p for p in _FALLBACK_ORDER if p != preferred]
        return [p for p in dict.fromkeys(order) if settings.websearch_key(p)]

    async def run(self, args: WebSearchArgs) -> ToolResult[WebSearchResult]:
        chain = self._chain(args.high_quality)
        if not chain:
            return ToolResult(ok=False, error="未配置任何搜索 key：在 .env 设 WEBSEARCH_<ENGINE>_KEY")
        last = ""
        for provider in chain:  # 逐个尝试；报错(含 403 配额满)或空结果就降级到下一个
            try:
                hits = await _search(provider, settings.websearch_key(provider), args.query, args.max_results, settings.http_timeout)
            except Exception as e:  # noqa: BLE001
                last = f"{provider}: {type(e).__name__}"
                continue
            if hits:
                return ToolResult(
                    ok=True,
                    data=WebSearchResult(query=args.query, provider=provider, hits=[WebHit(**h) for h in hits]),
                    sources=[Citation(title=(h["title"] or h["url"])[:60], url=h["url"], source="web") for h in hits if h.get("url")],
                )
        return ToolResult(ok=False, error=f"全网搜索均无结果或失败（{last}）")


class FetchUrlSummaryTool(Tool):
    name = "fetch_url_summary"
    description = (
        "按需读取单个公开网页 URL，返回标题、清洗正文片段和 highlights。"
        "用于用户给具体帖子/专栏/论坛楼/网页时做摘要；这是 discourse source，不是事实源。"
    )
    args_model = UrlSummaryArgs
    result_model = UrlSummaryResult

    async def run(self, args: UrlSummaryArgs) -> ToolResult[UrlSummaryResult]:
        try:
            result, _ = await _fetch_static_url(args.url, args.query, args.max_chars)
        except ValueError as e:
            return ToolResult(ok=False, error=str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"URL 读取失败：{type(e).__name__}")
        return ToolResult(
            ok=True,
            data=result,
            sources=[Citation(title=result.title, url=result.url, source="web")],
        )


class BrowserFetchSummaryTool(Tool):
    name = "browser_fetch_summary"
    description = (
        "动态网页读取/摘要：先静态读取，必要时用 Playwright 渲染公开页面、有限滚动并抽取可见文本/链接。"
        "用于 B站/论坛/动态网页/JS 渲染页面的单页摘要；不访问 localhost/内网，不处理登录墙。"
    )
    args_model = BrowserFetchArgs
    result_model = BrowserFetchResult

    async def run(self, args: BrowserFetchArgs) -> ToolResult[BrowserFetchResult]:
        static_result: UrlSummaryResult | None = None
        static_status: int | None = None
        if args.render in {"auto", "never"}:
            try:
                static_result, static_status = await _fetch_static_url(args.url, args.query, args.max_chars)
                if args.render == "never" or len(static_result.text) >= 500:
                    data = BrowserFetchResult(
                        url=static_result.url,
                        title=static_result.title,
                        render_mode="static",
                        text=static_result.text,
                        highlights=static_result.highlights,
                        status_code=static_status,
                        caveats=static_result.caveats,
                    )
                    return ToolResult(ok=True, data=data, sources=[Citation(title=data.title, url=data.url, source="web")])
            except ValueError as e:
                return ToolResult(ok=False, error=str(e))
            except Exception:
                static_result = None
        try:
            data = await _browser_fetch(args.url, args.query, args.max_chars, args.scrolls)
        except Exception as e:  # noqa: BLE001
            if static_result is not None:
                data = BrowserFetchResult(
                    url=static_result.url,
                    title=static_result.title,
                    render_mode="static_fallback",
                    text=static_result.text,
                    highlights=static_result.highlights,
                    status_code=static_status,
                    caveats=static_result.caveats + [f"浏览器渲染失败：{type(e).__name__}: {str(e)[:160]}"],
                )
                return ToolResult(ok=True, data=data, sources=[Citation(title=data.title, url=data.url, source="web")])
            return ToolResult(ok=False, error=f"浏览器摘要失败：{type(e).__name__}: {str(e)[:180]}")
        return ToolResult(
            ok=True,
            data=data,
            sources=[Citation(title=data.title, url=data.url, source="web")] + [
                Citation(title=x.title or x.url, url=x.url, source="web") for x in data.links[:4]
            ],
        )


def build_websearch_tools() -> list[Tool]:
    return [WebSearchTool(), FetchUrlSummaryTool(), BrowserFetchSummaryTool()]
