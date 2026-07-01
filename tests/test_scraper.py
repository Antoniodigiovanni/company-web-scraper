from unittest.mock import MagicMock, call, patch

import scraper  # noqa: F401
from scraper import (
    _filter_urls, _fetch_with_retry, _is_sitemap_index, _normalize_url,
    _parse_anchor_links, _parse_sitemap, _url_slug, _score_url, _rank_and_pick,
)


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
