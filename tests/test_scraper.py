import scraper  # noqa: F401
from scraper import (
    _filter_urls, _is_sitemap_index, _normalize_url, _parse_anchor_links,
    _parse_sitemap, _url_slug,
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
