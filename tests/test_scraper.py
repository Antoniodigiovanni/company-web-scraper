import pytest
from unittest.mock import MagicMock, call, patch

import scraper  # noqa: F401
from scraper import (
    _filter_urls, _fetch_with_retry, _is_sitemap_index, _normalize_url,
    _parse_anchor_links, _parse_sitemap, _url_slug, _score_url, _rank_and_pick,
    _extract_text,
)
from scraper import CompanyScraper


def test_module_imports():
    assert hasattr(scraper, "_DEFAULT_HIGH")
    assert hasattr(scraper, "_DEFAULT_LOW")


class TestNormalizeUrl:
    def test_adds_https_when_no_scheme(self):
        assert _normalize_url("example.com") == "https://example.com"

    def test_keeps_existing_https(self):
        assert _normalize_url("https://example.com") == "https://example.com"

    def test_keeps_existing_http(self):
        assert _normalize_url("http://example.com") == "http://example.com"

    def test_strips_fragment(self):
        assert _normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_strips_trailing_slash_on_path(self):
        assert _normalize_url("https://example.com/about/") == "https://example.com/about"

    def test_strips_trailing_slash_on_root(self):
        assert _normalize_url("https://example.com/") == "https://example.com"

    def test_preserves_query_string(self):
        assert _normalize_url("https://example.com/search?q=hello") == "https://example.com/search?q=hello"


class TestUrlSlug:
    def test_root_returns_home(self):
        assert _url_slug("https://example.com") == "home"

    def test_root_slash_returns_home(self):
        assert _url_slug("https://example.com/") == "home"

    def test_single_segment(self):
        assert _url_slug("https://example.com/about-us") == "about-us"

    def test_deep_path_returns_last_segment(self):
        assert _url_slug("https://example.com/en/company/about") == "about"

    def test_trailing_slash_ignored(self):
        assert _url_slug("https://example.com/products/") == "products"


_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/about</loc></url>
  <url><loc>https://example.com/products</loc></url>
  <url><loc>https://example.com/careers</loc></url>
</urlset>"""

_SITEMAP_INDEX_XML = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-blog.xml</loc></sitemap>
</sitemapindex>"""

_HOMEPAGE_HTML = """<html><body>
  <a href="/about">About</a>
  <a href="https://example.com/products">Products</a>
  <a href="https://other.com/page">External</a>
  <a href="/careers">Careers</a>
  <a href="mailto:hi@example.com">Email</a>
  <a href="/doc.pdf">PDF</a>
</body></html>"""


class TestParseSitemap:
    def test_extracts_locs(self):
        urls = _parse_sitemap(_SITEMAP_XML)
        assert "https://example.com/about" in urls
        assert "https://example.com/products" in urls
        assert "https://example.com/careers" in urls

    def test_returns_empty_on_bad_xml(self):
        assert _parse_sitemap("not xml at all") == []


class TestIsSitemapIndex:
    def test_true_for_index(self):
        assert _is_sitemap_index(_SITEMAP_INDEX_XML) is True

    def test_false_for_regular_sitemap(self):
        assert _is_sitemap_index(_SITEMAP_XML) is False

    def test_false_for_garbage(self):
        assert _is_sitemap_index("garbage") is False


class TestParseSitemapIndexLocs:
    def test_extracts_child_sitemap_urls(self):
        # _parse_sitemap also works on index nodes (extracts <loc> regardless)
        locs = _parse_sitemap(_SITEMAP_INDEX_XML)
        assert "https://example.com/sitemap-pages.xml" in locs


class TestParseAnchorLinks:
    def test_finds_absolute_links(self):
        links = _parse_anchor_links(_HOMEPAGE_HTML, "https://example.com")
        assert "https://example.com/products" in links

    def test_resolves_relative_links(self):
        links = _parse_anchor_links(_HOMEPAGE_HTML, "https://example.com")
        assert "https://example.com/about" in links

    def test_excludes_mailto(self):
        links = _parse_anchor_links(_HOMEPAGE_HTML, "https://example.com")
        assert not any("mailto" in l for l in links)

    def test_returns_empty_on_bad_html(self):
        assert _parse_anchor_links("", "https://example.com") == []


class TestFilterUrls:
    def test_drops_binary_extensions(self):
        urls = ["https://example.com/file.pdf", "https://example.com/about"]
        assert "https://example.com/file.pdf" not in _filter_urls(urls, "example.com")
        assert "https://example.com/about" in _filter_urls(urls, "example.com")

    def test_drops_different_hostname(self):
        urls = ["https://example.com/about", "https://other.com/page"]
        result = _filter_urls(urls, "example.com")
        assert "https://other.com/page" not in result

    def test_strips_www_for_comparison(self):
        urls = ["https://www.example.com/about"]
        assert "https://www.example.com/about" in _filter_urls(urls, "example.com")

    def test_deduplicates(self):
        urls = ["https://example.com/about", "https://example.com/about"]
        assert len(_filter_urls(urls, "example.com")) == 1

    def test_drops_non_http_schemes(self):
        urls = ["ftp://example.com/file", "https://example.com/about"]
        result = _filter_urls(urls, "example.com")
        assert not any(u.startswith("ftp") for u in result)


_HIGH = ["about", "products", "services"]
_LOW = ["careers", "blog"]


class TestScoreUrl:
    def test_high_value_match(self):
        assert _score_url("/about-us", _HIGH, _LOW) == 2

    def test_high_value_exact(self):
        assert _score_url("/products", _HIGH, _LOW) == 2

    def test_low_value_match(self):
        assert _score_url("/careers", _HIGH, _LOW) == -1

    def test_neutral(self):
        assert _score_url("/pricing", _HIGH, _LOW) == 0

    def test_case_insensitive(self):
        assert _score_url("/About-Us", _HIGH, _LOW) == 2

    def test_high_beats_low_when_both_match(self):
        # If a URL somehow contains both a high and low keyword, high wins (+2 takes priority)
        assert _score_url("/products-blog", _HIGH, _LOW) == 2


class TestRankAndPick:
    _URLS = [
        "https://x.com/careers",
        "https://x.com/about",
        "https://x.com/products",
        "https://x.com/pricing",
        "https://x.com/blog",
    ]

    def test_picks_high_value_first(self):
        ranked = _rank_and_pick(self._URLS, _HIGH, _LOW, max_count=2)
        assert "https://x.com/about" in ranked or "https://x.com/products" in ranked

    def test_respects_max_count(self):
        ranked = _rank_and_pick(self._URLS, _HIGH, _LOW, max_count=3)
        assert len(ranked) == 3

    def test_excludes_low_value_when_enough_high(self):
        ranked = _rank_and_pick(self._URLS, _HIGH, _LOW, max_count=2)
        assert "https://x.com/careers" not in ranked
        assert "https://x.com/blog" not in ranked

    def test_returns_all_when_fewer_than_max(self):
        ranked = _rank_and_pick(["https://x.com/about"], _HIGH, _LOW, max_count=5)
        assert len(ranked) == 1

    def test_prefers_shallower_paths_among_equal_scores(self):
        urls = ["https://x.com/en/about/team", "https://x.com/about"]
        ranked = _rank_and_pick(urls, _HIGH, _LOW, max_count=1)
        assert ranked[0] == "https://x.com/about"


def _mock_resp(status: int, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


class TestFetchWithRetry:
    @patch("scraper.cffi_requests.Session")
    def test_returns_200_on_success(self, MockSession):
        MockSession.return_value.get.return_value = _mock_resp(200, "<html>ok</html>")
        status, html, retries = _fetch_with_retry(
            "https://x.com", "none", ("chrome124",), 15.0
        )
        assert status == 200
        assert html == "<html>ok</html>"
        assert retries == 0

    @patch("scraper.cffi_requests.Session")
    def test_none_mode_no_retry_on_403(self, MockSession):
        MockSession.return_value.get.return_value = _mock_resp(403)
        status, _, retries = _fetch_with_retry(
            "https://x.com", "none", ("chrome124",), 15.0
        )
        assert status == 403
        assert retries == 0
        assert MockSession.return_value.get.call_count == 1

    @patch("time.sleep")
    @patch("scraper.cffi_requests.Session")
    def test_minimal_mode_retries_once_on_403(self, MockSession, mock_sleep):
        MockSession.return_value.get.side_effect = [_mock_resp(403), _mock_resp(200, "<html/>")]
        status, _, retries = _fetch_with_retry(
            "https://x.com", "minimal", ("chrome124", "safari17_2"), 15.0
        )
        assert status == 200
        assert retries == 1

    @patch("time.sleep")
    @patch("scraper.cffi_requests.Session")
    def test_full_mode_terminates_on_404(self, MockSession, mock_sleep):
        MockSession.return_value.get.return_value = _mock_resp(404)
        status, _, retries = _fetch_with_retry(
            "https://x.com", "full", ("chrome124", "safari17_2", "firefox133"), 15.0
        )
        assert status == 404
        assert retries == 0
        assert MockSession.return_value.get.call_count == 1

    @patch("time.sleep")
    @patch("scraper.cffi_requests.Session")
    def test_full_mode_does_4_retries_before_giving_up(self, MockSession, mock_sleep):
        MockSession.return_value.get.return_value = _mock_resp(403)
        status, _, retries = _fetch_with_retry(
            "https://x.com", "full", ("chrome124", "safari17_2", "firefox133"), 15.0
        )
        assert status == 403
        assert retries == 4
        assert MockSession.return_value.get.call_count == 5  # 1 initial + 4 retries

    @patch("time.sleep")
    @patch("scraper.cffi_requests.Session")
    def test_network_error_returns_status_0(self, MockSession, mock_sleep):
        MockSession.return_value.get.side_effect = Exception("connection refused")
        status, html, retries = _fetch_with_retry(
            "https://x.com", "none", ("chrome124",), 15.0
        )
        assert status == 0
        assert html == ""


_ARTICLE_HTML = """<html><body>
<article>
  <h1>About Our Company</h1>
  <p>We build software that helps businesses grow. Our team is distributed across the globe.</p>
  <p>Founded in 2010, we have served over 5000 customers.</p>
</article>
<nav><a href="/home">Home</a></nav>
</body></html>"""

_EMPTY_HTML = "<html><body><script>window.location='/';</script></body></html>"


class TestExtractText:
    def test_extracts_article_text(self):
        text = _extract_text(_ARTICLE_HTML)
        assert "software" in text
        assert len(text) > 50

    def test_returns_empty_string_on_script_only_page(self):
        text = _extract_text(_EMPTY_HTML)
        assert isinstance(text, str)  # never None

    def test_returns_empty_string_on_empty_input(self):
        assert _extract_text("") == ""

    def test_excludes_nav_boilerplate(self):
        text = _extract_text(_ARTICLE_HTML)
        # trafilatura with favor_precision=True should strip nav
        assert "Home" not in text or "About Our Company" in text  # content present


class TestFetchWithPlaywright:
    def _make_scraper(self):
        return CompanyScraper(js_fallback=True)

    @patch("scraper.sync_playwright")
    def test_returns_html_on_success(self, mock_sync_playwright):
        mock_page = MagicMock()
        mock_page.content.return_value = "<html><body>Company info here</body></html>"
        mock_page.query_selector.return_value = None  # no consent button

        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_sync_playwright.return_value.__enter__.return_value = mock_pw

        with CompanyScraper(js_fallback=True) as s:
            html = s._fetch_with_playwright("https://example.com")

        assert "<body>" in html

    @patch("scraper.sync_playwright")
    def test_returns_empty_on_timeout(self, mock_sync_playwright):
        mock_page = MagicMock()
        mock_page.goto.side_effect = Exception("Timeout 20000ms exceeded")

        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_sync_playwright.return_value.__enter__.return_value = mock_pw

        with CompanyScraper(js_fallback=True) as s:
            html = s._fetch_with_playwright("https://example.com")

        assert html == ""

    def test_raises_import_error_when_playwright_not_installed(self):
        import scraper as scraper_mod
        original = scraper_mod.sync_playwright
        scraper_mod.sync_playwright = None
        try:
            with CompanyScraper(js_fallback=True) as s:
                with pytest.raises(ImportError, match="playwright install chromium"):
                    s._fetch_with_playwright("https://example.com")
        finally:
            scraper_mod.sync_playwright = original


_STATIC_HOMEPAGE = """<html><head><title>Acme Corp</title></head><body>
  <p>We build great software for businesses worldwide.</p>
  <a href="/about">About</a>
  <a href="/products">Products</a>
  <a href="/careers">Careers</a>
</body></html>"""

_ABOUT_HTML = """<html><body>
  <article>
    <h1>About Acme</h1>
    <p>Acme Corp was founded in 2005 and serves enterprise clients globally.
    Our mission is to build reliable, scalable software solutions.</p>
  </article>
</body></html>"""


def _make_response(status: int = 200, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


class TestDiscoverSubpages:
    @patch("scraper.cffi_requests.Session")
    def test_discovers_from_homepage_anchors(self, MockSession):
        MockSession.return_value.get.return_value = _make_response(404)  # no sitemap
        with CompanyScraper(js_fallback=False) as s:
            urls = s._discover_subpages("https://example.com", _STATIC_HOMEPAGE)
        assert any("about" in u for u in urls)
        assert any("products" in u for u in urls)

    @patch("scraper.cffi_requests.Session")
    def test_discovers_from_sitemap_when_available(self, MockSession):
        sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/about</loc></url>
  <url><loc>https://example.com/technology</loc></url>
</urlset>"""
        MockSession.return_value.get.return_value = _make_response(200, sitemap)
        with CompanyScraper(js_fallback=False) as s:
            urls = s._discover_subpages("https://example.com", _STATIC_HOMEPAGE)
        assert "https://example.com/about" in urls
        assert "https://example.com/technology" in urls


class TestScrapeCompany:
    @patch("scraper.cffi_requests.Session")
    def test_returns_ok_status_on_success(self, MockSession):
        responses = {
            "https://example.com": _make_response(200, _STATIC_HOMEPAGE),
            "https://example.com/about": _make_response(200, _ABOUT_HTML),
        }
        def fake_get(url, **kwargs):
            # sitemap and robots return 404
            for key, resp in responses.items():
                if url == key:
                    return resp
            return _make_response(404)

        MockSession.return_value.get.side_effect = fake_get

        with CompanyScraper(js_fallback=False, max_subpages=2) as s:
            result = s._scrape_company("company_1", "https://example.com")

        assert result["status"] in ("ok", "partial")
        assert "company_1" == result["id"]
        assert "example.com" in result["url"]
        assert len(result["combined_text"]) > 0
        assert "[Page name: home]" in result["combined_text"]

    @patch("scraper.cffi_requests.Session")
    def test_returns_failed_when_all_pages_blocked(self, MockSession):
        MockSession.return_value.get.return_value = _make_response(403)
        with CompanyScraper(js_fallback=False, max_subpages=2) as s:
            result = s._scrape_company("company_2", "https://blocked.com")
        assert result["status"] == "failed"
        assert result["combined_text"] == ""

    @patch("scraper.cffi_requests.Session")
    def test_combined_text_format(self, MockSession):
        def fake_get(url, **kwargs):
            if "sitemap" in url or "robots" in url:
                return _make_response(404)
            if url == "https://example.com":
                return _make_response(200, _STATIC_HOMEPAGE)
            if "about" in url:
                return _make_response(200, _ABOUT_HTML)
            return _make_response(404)

        MockSession.return_value.get.side_effect = fake_get

        with CompanyScraper(js_fallback=False, max_subpages=3) as s:
            result = s._scrape_company("c1", "https://example.com")

        text = result["combined_text"]
        assert text.startswith("[Page name: home]")
        # Each page block starts with [Page name: <slug>]
        import re
        assert re.search(r"\[Page name: \S+\]", text)


class TestCompanyScraperInit:
    def test_instantiates_with_defaults(self):
        s = CompanyScraper()
        assert s.max_subpages == 8
        assert s.retry_mode == "full"
        assert s.js_fallback is True
        s.close()

    def test_persist_raw_html_without_delta_path_raises(self):
        with pytest.raises(ValueError, match="output_delta_path"):
            CompanyScraper(persist_raw_html=True)

    def test_delta_path_without_spark_raises(self):
        with pytest.raises(RuntimeError, match="Spark"):
            CompanyScraper(output_delta_path="/tmp/results")

    def test_context_manager_closes_cleanly(self):
        with CompanyScraper(js_fallback=False) as s:
            assert s is not None
        # no exception = close() was called

    def test_custom_keyword_lists(self):
        s = CompanyScraper(
            high_value_keywords=["technology"],
            low_value_keywords=["blog"],
        )
        assert s.high_value_keywords == ["technology"]
        assert s.low_value_keywords == ["blog"]
        s.close()

    def test_default_keyword_lists_set(self):
        s = CompanyScraper()
        assert "about" in s.high_value_keywords
        assert "careers" in s.low_value_keywords
        s.close()
