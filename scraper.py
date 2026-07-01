from __future__ import annotations

import random
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urljoin, urlparse

import pandas as pd
import trafilatura
from curl_cffi import requests as cffi_requests
from lxml import html as lxml_html

_BINARY_EXTS = frozenset([
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".zip", ".tar", ".gz", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".css", ".js", ".xml", ".json", ".ico",
])
_BLOCKING_STATUSES = frozenset([403, 429, 503])
_HARD_BLOCK_PATTERNS = [
    "just a moment", "access denied", "attention required",
    "checking your browser", "captcha", "robot verification",
]
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_MIN_TEXT_LEN = 200

_DEFAULT_HIGH = [
    "about", "about-us", "company", "who-we-are", "what-we-do",
    "mission", "vision", "team", "products", "product", "services",
    "solutions", "technology", "platform", "industries", "customers",
    "case-studies",
]
_DEFAULT_LOW = [
    "careers", "jobs", "press", "blog", "news", "events", "contact",
    "legal", "privacy", "terms", "cookie", "login", "signin", "signup", "cart",
]
_CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "[aria-label*='accept' i]",
    "#didomi-notice-agree-button",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
]


def _normalize_url(url: str) -> str:
    """Ensures https:// scheme, strips fragment and trailing slash."""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    # Drop fragment; strip trailing slash from path (but keep root as empty)
    path = parsed.path.rstrip("/")
    normalized = parsed._replace(fragment="", path=path)
    return normalized.geturl()


def _url_slug(url: str) -> str:
    """Returns last non-empty path segment or 'home' for root."""
    path = urlparse(url).path.rstrip("/")
    if not path:
        return "home"
    return path.split("/")[-1] or "home"


def _parse_sitemap(xml_text: str) -> list[str]:
    """Extracts <loc> URLs from a regular sitemap or sitemapindex."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    # Works for both <urlset> and <sitemapindex> — just grabs all <loc> elements
    ns = {"sm": _SITEMAP_NS}
    locs = [el.text.strip() for el in root.findall(".//sm:loc", ns) if el.text]
    return locs


def _is_sitemap_index(xml_text: str) -> bool:
    """Returns True if root tag (after stripping namespace) is 'sitemapindex'."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    return tag.lower() == "sitemapindex"


def _parse_anchor_links(html: str, base_url: str) -> list[str]:
    """Extracts all absolute hrefs from <a> tags."""
    if not html:
        return []
    try:
        doc = lxml_html.fromstring(html)
        doc.make_links_absolute(base_url)
        links = [a.get("href", "") for a in doc.xpath("//a[@href]")]
        # Filter out non-HTTP(S) schemes like mailto:
        return [link for link in links if link.startswith(("http://", "https://"))]
    except Exception:
        return []


def _filter_urls(urls: list[str], base_host: str) -> list[str]:
    """Filters URLs by host, scheme, binary extensions; deduplicates."""
    seen: set[str] = set()
    result = []
    base_host_norm = base_host.removeprefix("www.")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        host_norm = parsed.netloc.removeprefix("www.")
        if host_norm != base_host_norm:
            continue
        ext = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path.split("/")[-1] else ""
        if ext in _BINARY_EXTS:
            continue
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result
