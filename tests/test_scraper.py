import scraper  # noqa: F401


def test_module_imports():
    assert hasattr(scraper, "_DEFAULT_HIGH")
    assert hasattr(scraper, "_DEFAULT_LOW")
