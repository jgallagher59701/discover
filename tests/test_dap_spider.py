import pytest

from dap_spider import is_thredds_catalog, strip_dap_suffix, strip_query_string


@pytest.mark.parametrize(
    "suffix",
    [".dmr.xml", ".dmr", ".dap", ".dsr", ".dds", ".das", ".dods", ".info", ".ascii"],
)
def test_strip_dap_suffix_removes_each_known_suffix(suffix):
    base = "http://example.org/thredds/dodsC/data/foo"
    assert strip_dap_suffix(base + suffix) == base


def test_strip_dap_suffix_is_case_insensitive_but_preserves_original_casing():
    assert strip_dap_suffix("http://example.org/Foo.DDS") == "http://example.org/Foo"


def test_strip_dap_suffix_no_suffix_returns_unchanged():
    url = "http://example.org/thredds/dodsC/data/foo"
    assert strip_dap_suffix(url) == url


def test_strip_dap_suffix_suffix_like_substring_not_at_end_is_unchanged():
    url = "http://example.org/data.dds.info.txt"
    assert strip_dap_suffix(url) == url


def test_strip_dap_suffix_dmr_xml_and_dmr_do_not_interfere():
    base = "http://example.org/data/foo"
    assert strip_dap_suffix(base + ".dmr.xml") == base
    assert strip_dap_suffix(base + ".dmr") == base


def test_strip_dap_suffix_empty_string_returns_unchanged():
    assert strip_dap_suffix("") == ""


# ---- strip_query_string -------------------------------------------------


def test_strip_query_string_removes_query():
    url = "http://example.org/data/foo.dds?dataset=1"
    assert strip_query_string(url) == "http://example.org/data/foo.dds"


def test_strip_query_string_no_query_returns_unchanged():
    url = "http://example.org/data/foo.dds"
    assert strip_query_string(url) == url


def test_strip_query_string_uses_first_question_mark():
    url = "http://example.org/data/foo.dds?a=1?b=2"
    assert strip_query_string(url) == "http://example.org/data/foo.dds"


def test_strip_query_string_question_mark_at_start_returns_empty():
    assert strip_query_string("?a=1") == ""


def test_strip_query_string_drops_fragment_that_follows_query():
    url = "http://example.org/data/foo.dds?a=1#section"
    assert strip_query_string(url) == "http://example.org/data/foo.dds"


# ---- is_thredds_catalog --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/thredds/catalog.html",
        "http://example.org/thredds/catalog.xml",
        "http://example.org/thredds/catalog/",
        "http://example.org/thredds/catalog/subdir/",
    ],
)
def test_is_thredds_catalog_true_cases(url):
    assert is_thredds_catalog(url) is True


def test_is_thredds_catalog_is_case_insensitive():
    assert is_thredds_catalog("http://example.org/THREDDS/Catalog.HTML") is True


def test_is_thredds_catalog_false_without_trailing_slash_or_extension():
    assert is_thredds_catalog("http://example.org/thredds/catalog") is False


def test_is_thredds_catalog_false_for_unrelated_path():
    assert is_thredds_catalog("http://example.org/opendap/data.nc") is False


def test_is_thredds_catalog_query_string_does_not_affect_result():
    url = "http://example.org/thredds/catalog.html?dataset=foo"
    assert is_thredds_catalog(url) is True


def test_is_thredds_catalog_matches_catalog_prefixed_names_too():
    # The check is a substring test ("/thredds/catalog" in path), so any
    # path segment starting with "catalog" and ending in .html/.xml also
    # matches (e.g. catalogRef.html), not just literal catalog listings.
    # This is intentional here: downstream code does a more detailed probe
    # to confirm it's actually a THREDDS catalog before acting on it.
    assert is_thredds_catalog("http://example.org/thredds/catalogRef.html") is True
