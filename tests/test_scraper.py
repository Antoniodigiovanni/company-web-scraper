import scraper  # noqa: F401
from scraper import _normalize_url, _url_slug


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
