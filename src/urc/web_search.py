from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


@dataclass
class WebHit:
    source: str
    title: str
    url: str
    snippet: str


def _clean(text: str) -> str:
    t = unescape((text or "").strip())
    t = re.sub(r"\s+", " ", t)
    return t


def _fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    req = Request(url=url, headers={"User-Agent": "planetonyx-agent/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _fetch_text(url: str, timeout: float) -> str:
    req = Request(url=url, headers={"User-Agent": "planetonyx-agent/1.0"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def _search_searxng(base_url: str, query: str, limit: int, timeout: float) -> list[WebHit]:
    b = base_url.rstrip("/")
    url = f"{b}/search?q={quote_plus(query)}&format=json"
    data = _fetch_json(url, timeout=timeout)
    if not data:
        return []
    items = data.get("results")
    if not isinstance(items, list):
        return []
    hits: list[WebHit] = []
    for item in items[: max(1, limit)]:
        if not isinstance(item, dict):
            continue
        title = _clean(str(item.get("title") or ""))
        link = _clean(str(item.get("url") or ""))
        snippet = _clean(str(item.get("content") or ""))
        if not title or not link:
            continue
        hits.append(WebHit(source="searxng", title=title, url=link, snippet=snippet))
    return hits


def _search_duckduckgo_html(query: str, limit: int, timeout: float) -> list[WebHit]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        html = _fetch_text(url, timeout=timeout)
    except Exception:
        return []

    hits: list[WebHit] = []
    for m in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        link = _clean(m.group("url"))
        title = _clean(re.sub(r"<[^>]+>", "", m.group("title")))
        if not link or not title:
            continue
        hits.append(WebHit(source="duckduckgo", title=title, url=link, snippet=""))
        if len(hits) >= max(1, limit):
            break
    return hits


def build_web_context(query: str, *, endpoints: list[str], limit: int = 4, timeout: float = 7.0) -> str:
    q = _clean(query)
    if not q:
        return ""

    hits: list[WebHit] = []
    for endpoint in endpoints:
        hits = _search_searxng(endpoint, q, limit=limit, timeout=timeout)
        if hits:
            break
    if not hits:
        hits = _search_duckduckgo_html(q, limit=limit, timeout=timeout)
    if not hits:
        return ""

    lines: list[str] = []
    for h in hits[: max(1, limit)]:
        snippet = h.snippet
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        lines.append(f"[WEB|{h.source}] {h.title} :: {h.url} :: {snippet}")
    return "\n".join(lines)
