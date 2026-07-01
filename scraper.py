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


def _score_url(path: str, high_kw: list[str], low_kw: list[str]) -> int:
    """Scores a URL path based on keyword matching. High keywords return +2, low return -1, else 0. High wins over low."""
    lower = path.lower()
    if any(kw in lower for kw in high_kw):
        return 2
    if any(kw in lower for kw in low_kw):
        return -1
    return 0


def _rank_and_pick(
    urls: list[str], high_kw: list[str], low_kw: list[str], max_count: int
) -> list[str]:
    """Ranks URLs by score (descending) then by path depth (ascending), returns top max_count URLs."""
    def sort_key(url: str):
        path = urlparse(url).path
        score = _score_url(path, high_kw, low_kw)
        depth = path.count("/")
        return (-score, depth)  # higher score first, shallower path first

    return sorted(urls, key=sort_key)[:max_count]


def _fetch_with_retry(
    url: str,
    retry_mode: str,
    profiles: tuple,
    timeout_s: float,
    start_profile_idx: int = 0,
) -> tuple[int, str, int]:
    """Fetches a URL with configurable retry behaviour and browser impersonation rotation.

    Returns (status_code, html_text, retries_used).
    On network exception: (0, "", retries_used_so_far).
    """
    n = len(profiles)
    i = start_profile_idx % n

    # Build attempt schedule: each entry is (profile, pre_sleep_seconds)
    if retry_mode == "none":
        schedule = [(profiles[i], 0.0)]
    elif retry_mode == "minimal":
        schedule = [(profiles[i], 0.0), (profiles[(i + 1) % n], 0.0)]
    else:  # "full"
        schedule = [
            (profiles[i], 0.0),
            (profiles[i], 1.0),
            (profiles[i], 2.0),
            (profiles[(i + 1) % n], 4.0),
            (profiles[(i + 2) % n], 8.0),
        ]

    last_status, last_html, retries_used = 0, "", 0

    for attempt_idx, (profile, sleep_s) in enumerate(schedule):
        if sleep_s > 0:
            time.sleep(sleep_s + random.uniform(0, 0.5))

        try:
            session = cffi_requests.Session(impersonate=profile)
            resp = session.get(url, timeout=timeout_s)
            last_status = resp.status_code
            last_html = resp.text
        except Exception:
            last_status = 0
            last_html = ""
            if attempt_idx < len(schedule) - 1:
                retries_used += 1
                continue

        if last_status == 404:
            break  # terminal — never retry

        if last_status not in _BLOCKING_STATUSES and last_status != 0:
            break  # success

        if attempt_idx < len(schedule) - 1:
            retries_used += 1

    return last_status, last_html, retries_used


def _extract_text(html: str) -> str:
    """Extracts prose text from HTML using trafilatura. Returns extracted text or ''."""
    if not html:
        return ""
    result = trafilatura.extract(html, include_comments=False, favor_precision=True)
    return result or ""


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class CompanyScraper:
    """Scrape the text from company websites for downstream business description generation.

    Databricks setup required once per cluster:
        %pip install curl_cffi trafilatura lxml pandas playwright
        !playwright install chromium

    Args:
        max_subpages: Total pages to scrape per company (including homepage). Default 8.
        high_value_keywords: URL path keywords scoring +2. Defaults to _DEFAULT_HIGH.
        low_value_keywords: URL path keywords scoring -1. Defaults to _DEFAULT_LOW.
        retry_mode: "none" | "minimal" | "full". See module docstring.
        impersonate_profiles: curl_cffi impersonation profiles to rotate through.
        timeout_s: Per-request timeout in seconds.
        subpage_workers: ThreadPoolExecutor workers for parallel subpage fetching.
        js_fallback: If True, escalate blocked/empty pages to Playwright.
        output_delta_path: If set, append results DataFrame to this Delta table.
        delta_log_path: If set, append per-company log rows to this Delta table.
        persist_raw_html: If True, write raw HTML to {output_delta_path}_raw Delta table.
            Requires output_delta_path.
        spark: SparkSession to use. If None, uses SparkSession.getActiveSession().
    """

    def __init__(
        self,
        max_subpages: int = 8,
        high_value_keywords: list[str] | None = None,
        low_value_keywords: list[str] | None = None,
        retry_mode: Literal["none", "minimal", "full"] = "full",
        impersonate_profiles: tuple = ("chrome124", "safari17_2", "firefox133"),
        timeout_s: float = 15.0,
        subpage_workers: int = 5,
        js_fallback: bool = True,
        output_delta_path: str | None = None,
        delta_log_path: str | None = None,
        persist_raw_html: bool = False,
        spark=None,
    ) -> None:
        if persist_raw_html and not output_delta_path:
            raise ValueError("persist_raw_html=True requires output_delta_path to be set.")

        self._spark = spark
        if (output_delta_path or delta_log_path) and self._spark is None:
            try:
                from pyspark.sql import SparkSession
                self._spark = SparkSession.getActiveSession()
                if self._spark is None:
                    raise RuntimeError(
                        "No active Spark session found. Pass spark= or run on Databricks."
                    )
            except ImportError:
                raise RuntimeError(
                    "PySpark not available. Delta persistence requires a Databricks runtime."
                )

        self.max_subpages = max_subpages
        self.high_value_keywords = high_value_keywords if high_value_keywords is not None else list(_DEFAULT_HIGH)
        self.low_value_keywords = low_value_keywords if low_value_keywords is not None else list(_DEFAULT_LOW)
        self.retry_mode = retry_mode
        self.impersonate_profiles = tuple(impersonate_profiles)
        self.timeout_s = timeout_s
        self.subpage_workers = subpage_workers
        self.js_fallback = js_fallback
        self.output_delta_path = output_delta_path
        self.delta_log_path = delta_log_path
        self.persist_raw_html = persist_raw_html

        self._browser = None
        self._playwright_ctx = None

    def close(self) -> None:
        if getattr(self, "_browser", None) is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if getattr(self, "_playwright_ctx", None) is not None:
            try:
                self._playwright_ctx.stop()
            except Exception:
                pass
            self._playwright_ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def _discover_subpages(self, base_url: str, homepage_html: str) -> list[str]:
        """Return candidate subpage URLs (filtered, not yet ranked or capped)."""
        parsed = urlparse(base_url)
        base_host = parsed.netloc.removeprefix("www.")
        all_urls: list[str] = []

        # 1. Try sitemap.xml
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        try:
            session = cffi_requests.Session(impersonate=self.impersonate_profiles[0])
            resp = session.get(sitemap_url, timeout=self.timeout_s)
            if resp.status_code == 200 and resp.text.strip().startswith("<"):
                if _is_sitemap_index(resp.text):
                    child_urls = _parse_sitemap(resp.text)[:3]
                    for child_url in child_urls:
                        try:
                            child_resp = session.get(child_url, timeout=self.timeout_s)
                            if child_resp.status_code == 200:
                                all_urls.extend(_parse_sitemap(child_resp.text))
                        except Exception:
                            pass
                else:
                    all_urls.extend(_parse_sitemap(resp.text))
        except Exception:
            pass

        # 2. Try robots.txt for Sitemap: directives
        if not all_urls:
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            try:
                session = cffi_requests.Session(impersonate=self.impersonate_profiles[0])
                resp = session.get(robots_url, timeout=self.timeout_s)
                if resp.status_code == 200:
                    for line in resp.text.splitlines():
                        if line.lower().startswith("sitemap:"):
                            sm_url = line.split(":", 1)[1].strip()
                            try:
                                sm_resp = session.get(sm_url, timeout=self.timeout_s)
                                if sm_resp.status_code == 200:
                                    all_urls.extend(_parse_sitemap(sm_resp.text))
                            except Exception:
                                pass
            except Exception:
                pass

        # 3. Fall back to anchor parsing
        if not all_urls:
            all_urls = _parse_anchor_links(homepage_html, base_url)

        # Cap sitemap results and filter
        return _filter_urls(all_urls[:200], base_host)

    def _scrape_company(self, row_id, url: str) -> dict:
        """Scrape one company. Returns a result dict matching the output DataFrame schema."""
        t_start = time.time()
        url = _normalize_url(url)
        pages_meta: list[dict] = []
        log_error: str | None = None
        total_retries = 0
        escalated_any = False

        # --- Fetch homepage ---
        start_idx = random.randint(0, len(self.impersonate_profiles) - 1)
        hp_status, hp_html, hp_retries = _fetch_with_retry(
            url, self.retry_mode, self.impersonate_profiles, self.timeout_s, start_idx
        )
        total_retries += hp_retries
        hp_escalated = False

        if hp_status in _BLOCKING_STATUSES or hp_status == 0:
            if self.js_fallback:
                hp_html = self._fetch_with_playwright(url)
                hp_escalated = True
                escalated_any = True

        hp_text = _extract_text(hp_html)

        if (not hp_text or len(hp_text) < _MIN_TEXT_LEN) and self.js_fallback and not hp_escalated:
            hp_html = self._fetch_with_playwright(url)
            hp_escalated = True
            escalated_any = True
            hp_text = _extract_text(hp_html)

        # Hard-block detection after Playwright
        if hp_escalated and hp_text:
            lower = hp_text.lower()
            if len(hp_text) < _MIN_TEXT_LEN or any(p in lower for p in _HARD_BLOCK_PATTERNS):
                hp_text = ""

        hp_fetched_at = datetime.now(timezone.utc)
        pages_meta.append({
            "url": url,
            "page_name": "home",
            "status": hp_status,
            "text": hp_text,
            "text_len": len(hp_text),
            "escalated_to_js": hp_escalated,
            "html": hp_html if self.persist_raw_html else "",
            "fetched_at": hp_fetched_at,
        })

        if not hp_text:
            log_error = f"Homepage blocked or empty: HTTP {hp_status}"

        # --- Discover & rank subpages ---
        candidate_urls = self._discover_subpages(url, hp_html)

        if not candidate_urls and self.js_fallback and not hp_escalated:
            # SPA: escalate homepage to get links
            hp_html = self._fetch_with_playwright(url)
            hp_escalated = True
            escalated_any = True
            pages_meta[0]["escalated_to_js"] = True
            pages_meta[0]["html"] = hp_html if self.persist_raw_html else ""
            candidate_urls = _parse_anchor_links(hp_html, url)
            parsed = urlparse(url)
            candidate_urls = _filter_urls(candidate_urls, parsed.netloc.removeprefix("www."))

        # Exclude homepage itself
        candidate_urls = [u for u in candidate_urls if _normalize_url(u) != url]
        ranked = _rank_and_pick(
            candidate_urls,
            self.high_value_keywords,
            self.low_value_keywords,
            max_count=self.max_subpages - 1,
        )

        # --- Fetch subpages in parallel ---
        def fetch_subpage(subpage_url: str) -> dict:
            s_idx = random.randint(0, len(self.impersonate_profiles) - 1)
            status, html, retries = _fetch_with_retry(
                subpage_url, self.retry_mode, self.impersonate_profiles, self.timeout_s, s_idx
            )
            escalated = False
            if status in _BLOCKING_STATUSES or status == 0:
                if self.js_fallback:
                    html = self._fetch_with_playwright(subpage_url)
                    escalated = True
            text = _extract_text(html)
            if (not text or len(text) < _MIN_TEXT_LEN) and self.js_fallback and not escalated:
                html = self._fetch_with_playwright(subpage_url)
                escalated = True
                text = _extract_text(html)
            if escalated and text:
                lower = text.lower()
                if len(text) < _MIN_TEXT_LEN or any(p in lower for p in _HARD_BLOCK_PATTERNS):
                    text = ""
            return {
                "url": subpage_url,
                "page_name": _url_slug(subpage_url),
                "status": status,
                "text": text,
                "text_len": len(text),
                "escalated_to_js": escalated,
                "html": html if self.persist_raw_html else "",
                "fetched_at": datetime.now(timezone.utc),
                "retries": retries,
            }

        if ranked:
            with ThreadPoolExecutor(max_workers=self.subpage_workers) as pool:
                futures = {pool.submit(fetch_subpage, u): u for u in ranked}
                for fut in as_completed(futures):
                    try:
                        page = fut.result()
                        pages_meta.append(page)
                        total_retries += page.get("retries", 0)
                        if page["escalated_to_js"]:
                            escalated_any = True
                        if not log_error and not page["text"]:
                            log_error = f"No content from {page['url']}"
                    except Exception as e:
                        log_error = log_error or str(e)

        # --- Combine text ---
        ordered = [pages_meta[0]] + sorted(
            pages_meta[1:],
            key=lambda p: -_score_url(
                urlparse(p["url"]).path,
                self.high_value_keywords,
                self.low_value_keywords,
            ),
        )
        parts = []
        for page in ordered:
            if page["text"]:
                parts.append(f"[Page name: {page['page_name']}]\n{page['text']}")
        combined_text = "\n\n".join(parts)

        num_ok = sum(1 for p in pages_meta if p["text"])
        if num_ok == 0:
            status_str = "failed"
        elif num_ok < len(pages_meta):
            status_str = "partial"
        else:
            status_str = "ok"

        return {
            "id": row_id,
            "url": url,
            "combined_text": combined_text,
            "num_pages_tried": len(pages_meta),
            "num_pages_ok": num_ok,
            "pages": [
                {
                    "url": p["url"],
                    "page_name": p["page_name"],
                    "status": p["status"],
                    "text_len": p["text_len"],
                    "escalated_to_js": p["escalated_to_js"],
                }
                for p in pages_meta
            ],
            "escalated_to_js": escalated_any,
            "retries_used": total_retries,
            "status": status_str,
            "error": log_error,
            "total_time_s": time.time() - t_start,
            "ts": datetime.now(timezone.utc),
            "_raw_rows": [
                {
                    "id": row_id,
                    "url": url,
                    "page_url": p["url"],
                    "html": p["html"],
                    "fetched_at": p["fetched_at"],
                    "escalated_to_js": p["escalated_to_js"],
                }
                for p in pages_meta
            ] if self.persist_raw_html else [],
        }

    def _fetch_with_playwright(self, url: str) -> str:
        global sync_playwright
        if sync_playwright is None:
            raise ImportError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        if self._playwright_ctx is None:
            self._playwright_ctx = sync_playwright().__enter__()
            self._browser = self._playwright_ctx.chromium.launch(headless=True)

        page = self._browser.new_page()
        try:
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(1.5)

            for selector in _CONSENT_SELECTORS:
                try:
                    el = page.query_selector(selector)
                    if el:
                        el.click(timeout=2_000)
                        break
                except Exception:
                    continue

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)

            html = page.content()
            return html
        except Exception:
            return ""
        finally:
            try:
                page.close()
            except Exception:
                pass
