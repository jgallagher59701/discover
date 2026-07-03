import types

import pytest

from conftest import make_response
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


# ---- DapSpider callback harness spike (plan Step A1) ---------------------
#
# Confirms the make_response()/spider fixture combo in conftest.py can drive
# DapSpider's bound methods directly against a real scrapy TextResponse, with
# no Crawler/CrawlerProcess involved. Full case coverage for these callbacks
# is Steps A2-A4.


def test_harness_on_dmr_confirms_dap4_from_body_signature(spider):
    response = make_response(
        url="http://example.org/data/foo.dmr.xml",
        body="Dataset { DAP/4.0 }",
        headers={"XDAP": b"4.0"},
    )
    results = list(spider.on_dmr(response, base="http://example.org/data/foo"))
    assert results == [
        {
            "url": "http://example.org/data/foo",
            "dap_version": "4",
            "probe_url": "http://example.org/data/foo.dmr.xml",
            "xdap": "4.0",
            "server": "",
        }
    ]


# ---- DapSpider.probe / on_dmr / on_dds (plan Step A2) ---------------------

BASE = "http://example.org/data/foo"


def test_probe_yields_single_dmr_request(spider):
    requests = list(spider.probe(BASE))
    assert len(requests) == 1
    req = requests[0]
    assert req.url == BASE + ".dmr.xml"
    assert req.callback == spider.on_dmr
    assert req.cb_kwargs == {"base": BASE}
    assert req.dont_filter is True


# -- on_dmr: DAP4 confirmation signals --


def test_on_dmr_confirms_dap4_via_xdap_header_with_no_body_signature(spider):
    response = make_response(
        url=BASE + ".dmr.xml", body="not a real body", headers={"XDAP": b"4.0"}
    )
    results = list(spider.on_dmr(response, base=BASE))
    assert results == [
        {
            "url": BASE,
            "dap_version": "4",
            "probe_url": BASE + ".dmr.xml",
            "xdap": "4.0",
            "server": "",
        }
    ]


def test_on_dmr_confirms_dap4_via_dapversion_in_body(spider):
    response = make_response(url=BASE + ".dmr.xml", body="<Dataset dapVersion='4.0'>")
    results = list(spider.on_dmr(response, base=BASE))
    assert len(results) == 1
    assert results[0]["dap_version"] == "4"


def test_on_dmr_includes_server_header_when_present(spider):
    response = make_response(
        url=BASE + ".dmr.xml",
        body="DAP/4.0",
        headers={"Server": b"Hyrax/1.17.1"},
    )
    results = list(spider.on_dmr(response, base=BASE))
    assert results[0]["server"] == "Hyrax/1.17.1"


def test_on_dmr_missing_headers_decode_to_empty_string_not_error(spider):
    response = make_response(url=BASE + ".dmr.xml", body="DAP/4.0")
    results = list(spider.on_dmr(response, base=BASE))
    assert results[0]["xdap"] == ""
    assert results[0]["server"] == ""


# -- on_dmr: falls through to DAP2 probe --


def test_on_dmr_no_signature_falls_through_to_dds_request(spider):
    response = make_response(url=BASE + ".dmr.xml", body="<html>not dap</html>")
    results = list(spider.on_dmr(response, base=BASE))
    assert len(results) == 1
    req = results[0]
    assert req.url == BASE + ".dds"
    assert req.callback == spider.on_dds
    assert req.cb_kwargs == {"base": BASE}
    assert req.dont_filter is True


def test_on_dmr_non_200_falls_through_to_dds_even_if_body_would_match(spider):
    # status check short-circuits the "and" before the body/header check, so
    # a non-200 response falls through to the DAP2 attempt regardless of
    # whether the body looks like a DAP4 signature.
    response = make_response(url=BASE + ".dmr.xml", body="DAP/4.0", status=404)
    results = list(spider.on_dmr(response, base=BASE))
    assert len(results) == 1
    assert results[0].url == BASE + ".dds"


# -- on_dds: DAP2 confirmation signals --


def test_on_dds_confirms_dap2_via_body_signature(spider):
    response = make_response(url=BASE + ".dds", body="Dataset {\n  Float64 x;\n}")
    results = list(spider.on_dds(response, base=BASE))
    assert results == [
        {
            "url": BASE,
            "dap_version": "2",
            "probe_url": BASE + ".dds",
            "xdods_server": "",
            "server": "",
        }
    ]


def test_on_dds_confirms_dap2_via_xdods_header_alone(spider):
    response = make_response(
        url=BASE + ".dds", body="not a dds body", headers={"XDODS-Server": b"dods/3.7"}
    )
    results = list(spider.on_dds(response, base=BASE))
    assert len(results) == 1
    assert results[0]["xdods_server"] == "dods/3.7"


def test_on_dds_confirms_dap2_via_content_description_case_insensitive(spider):
    response = make_response(
        url=BASE + ".dds",
        body="not a dds body",
        headers={"Content-Description": b"DODS-DDS"},
    )
    results = list(spider.on_dds(response, base=BASE))
    assert len(results) == 1


def test_on_dds_tolerates_leading_whitespace_before_signature(spider):
    response = make_response(url=BASE + ".dds", body="   \n  Dataset {\n}")
    results = list(spider.on_dds(response, base=BASE))
    assert len(results) == 1


def test_on_dds_no_signal_yields_nothing(spider):
    response = make_response(url=BASE + ".dds", body="<html>404-ish page</html>")
    results = list(spider.on_dds(response, base=BASE))
    assert results == []


def test_on_dds_non_200_yields_nothing_even_if_body_would_match(spider):
    response = make_response(
        url=BASE + ".dds", body="Dataset {\n}", status=500
    )
    results = list(spider.on_dds(response, base=BASE))
    assert results == []


# -- on_error: never raises --


def test_on_error_does_not_raise(spider):
    failure = types.SimpleNamespace(value=Exception("boom"))
    spider.on_error(failure)  # no assertion beyond "did not raise"
