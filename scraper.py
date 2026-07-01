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
