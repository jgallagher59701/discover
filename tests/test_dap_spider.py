import asyncio
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest
from scrapy.downloadermiddlewares.httpcompression import HttpCompressionMiddleware
from scrapy.exceptions import CannotResolveHostError
from scrapy.http import Request
from twisted.internet.error import DNSLookupError

from conftest import load_captured_response, make_response, make_xml_response
from dap_spider import (
    IdentityEncodingMiddleware,
    advance_seed_watermark,
    format_progress_tick,
    is_thredds_catalog,
    should_dispatch_seed,
    strip_dap_suffix,
    strip_query_string,
    to_xml,
)


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


def test_html_to_xml():
    assert to_xml("https://x.edu/thredds/catalog.html") == "https://x.edu/thredds/catalog.xml"


def test_html_to_xml_trailing_slash():
    assert to_xml("https://x.edu/thredds/catalog/") == "https://x.edu/thredds/catalog.xml"


def test_html_to_xml_bare_catalog():
    assert to_xml("https://x.edu/thredds/catalog") == "https://x.edu/thredds/catalog.xml"


# Test that the query string is preserved
def test_html_to_xml_html_with_query_string():
    assert to_xml("https://x.edu/thredds/catalog.html?x=1") == "https://x.edu/thredds/catalog.xml?x=1"

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


# ---- should_dispatch_seed (issue #22, restart/resume) --------------------


def test_should_dispatch_seed_resume_from_zero_dispatches_everything():
    assert should_dispatch_seed(1, resume_from=0) is True
    assert should_dispatch_seed(50, resume_from=0) is True


def test_should_dispatch_seed_at_resume_boundary_is_skipped():
    # seed_index == resume_from is the last seed already processed
    assert should_dispatch_seed(5, resume_from=5) is False


def test_should_dispatch_seed_first_seed_after_boundary_is_dispatched():
    assert should_dispatch_seed(6, resume_from=5) is True


def test_should_dispatch_seed_well_before_boundary_is_skipped():
    assert should_dispatch_seed(1, resume_from=5) is False


# ---- format_progress_tick (issue #22, seed-count progress) ---------------


def test_format_progress_tick_no_progress_every_prints_nothing():
    text, last_reported = format_progress_tick(
        deref_count=5, progress_every=None, last_completed_seed=3, last_reported_seed=0
    )
    assert text is None
    assert last_reported == 0


def test_format_progress_tick_off_boundary_prints_nothing():
    text, last_reported = format_progress_tick(
        deref_count=4, progress_every=5, last_completed_seed=3, last_reported_seed=0
    )
    assert text is None
    assert last_reported == 0


def test_format_progress_tick_on_boundary_with_new_seed_prints_marker():
    text, last_reported = format_progress_tick(
        deref_count=5, progress_every=5, last_completed_seed=3, last_reported_seed=0
    )
    assert text == "[3]"
    assert last_reported == 3


def test_format_progress_tick_on_boundary_without_new_seed_prints_dot():
    text, last_reported = format_progress_tick(
        deref_count=10, progress_every=5, last_completed_seed=3, last_reported_seed=3
    )
    assert text == "."
    assert last_reported == 3


def test_format_progress_tick_marker_only_fires_once_per_seed_advance():
    # second boundary after the same seed advance -> dot, not a repeat marker
    _, after_first = format_progress_tick(
        deref_count=5, progress_every=5, last_completed_seed=3, last_reported_seed=0
    )
    text, last_reported = format_progress_tick(
        deref_count=10, progress_every=5, last_completed_seed=3, last_reported_seed=after_first
    )
    assert text == "."
    assert last_reported == 3


# ---- advance_seed_watermark (issue #22, restart/resume correctness) ------
#
# This is the fix for a real bug found during manual verification: an
# earlier version tracked the *dispatch*-time seed index (the seed a
# request was yielded for in start()), not completion. Scrapy's engine
# drains the start() async generator to populate its scheduler far ahead of
# actual throttled downloads, so dispatch-time tracking raced to "done" for
# the entire seed file within seconds of a real run, regardless of how much
# work had actually completed -- a Ctrl-C would then tell the user to
# --resume-from a point that silently skipped almost everything. These
# tests pin the completion-based watermark instead.


def test_advance_seed_watermark_single_request_seed_resolves_immediately():
    pending, resolved = {1: 1}, set()
    result = advance_seed_watermark(pending, resolved, last_completed_seed=0, seed_index=1)
    assert result == 1
    assert pending == {}
    assert resolved == set()


def test_advance_seed_watermark_multi_request_seed_waits_for_all_pending():
    # e.g. a DAP4 probe that fell through to a DAP2 fallback: two requests
    # attributed to the same seed. The watermark must not advance after
    # only the first of the two resolves.
    pending, resolved = {1: 2}, set()
    result = advance_seed_watermark(pending, resolved, last_completed_seed=0, seed_index=1)
    assert result == 0
    assert pending == {1: 1}
    result = advance_seed_watermark(pending, resolved, last_completed_seed=result, seed_index=1)
    assert result == 1
    assert pending == {}


def test_advance_seed_watermark_out_of_order_completion_does_not_skip_gap():
    # seed 2 (a fast host) resolves before seed 1 (a slow host, still
    # in flight) -- the watermark must stay at 0, not jump to 2, or a
    # --resume-from taken at this point would skip seed 1 entirely.
    pending, resolved = {1: 1, 2: 1}, set()
    result = advance_seed_watermark(pending, resolved, last_completed_seed=0, seed_index=2)
    assert result == 0
    assert resolved == {2}
    assert pending == {1: 1}


def test_advance_seed_watermark_gap_fills_in_and_advances_past_both():
    pending, resolved = {1: 1, 2: 1}, set()
    after_seed_2 = advance_seed_watermark(pending, resolved, last_completed_seed=0, seed_index=2)
    result = advance_seed_watermark(
        pending, resolved, last_completed_seed=after_seed_2, seed_index=1
    )
    assert result == 2
    assert pending == {}
    assert resolved == set()


def test_advance_seed_watermark_untracked_seed_defaults_to_single_pending():
    # A request that was never explicitly begun (e.g. a defensive on_error
    # call in a test) is treated as if it had one pending request, so ending
    # it resolves the seed rather than going negative.
    pending, resolved = {}, set()
    result = advance_seed_watermark(pending, resolved, last_completed_seed=0, seed_index=5)
    assert result == 0  # seed 5 resolves, but watermark can't skip seeds 1-4
    assert resolved == {5}


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
#
# This test also shows that testing the body is heuristic. jhrg 7/6/26


def test_harness_on_dmr_confirms_dap4_from_body_signature(spider):
    response = make_response(
        url="http://example.org/data/foo.dmr.xml",
        body="Dataset { dmrVersion }",
        headers={"Content-Description": b"application/vnd.opendap.dap4.dataset-metadata+xml"},
    )
    results = list(spider.on_dmr(response, base="http://example.org/data/foo", seed_index=1))
    assert results == [
        {
            "url": "http://example.org/data/foo",
            "dap_version": "4",
            "probe_url": "http://example.org/data/foo.dmr.xml",
            "content_description": "application/vnd.opendap.dap4.dataset-metadata+xml",
            "xdods_server": "",
            "server": ""
        }
    ]


# ---- DapSpider.probe / on_dmr / on_dds (plan Step A2) ---------------------

BASE = "http://example.org/data/foo"


def test_probe_yields_single_dmr_request(spider):
    requests = list(spider.probe(BASE, seed_index=1))
    assert len(requests) == 1
    req = requests[0]
    assert req.url == BASE + ".dmr.xml"
    assert req.callback == spider.on_dmr
    assert req.cb_kwargs == {"base": BASE, "seed_index": 1}
    assert req.dont_filter is True


# -- on_dmr: DAP4 confirmation signals --

# Even with a content description that's pretty obvious, the spider only
# uses the 'body signature.' jhrg 7/6/26
def test_on_dmr_fails_dap4_with_no_body_signature(spider):
    response = make_response(
        url=BASE + ".dmr.xml", body="not a real body", 
            headers={"Content-Description": b"application/vnd.opendap.dap4.dataset-metadata+xml"}
    )
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert len(results) == 1
    req = results[0]
    assert req.url == BASE + ".dds"
    assert req.callback == spider.on_dds
    assert req.cb_kwargs == {"base": BASE, "seed_index": 1}
    assert req.dont_filter is True


def test_on_dmr_confirms_dap4_via_dmrversion_in_body(spider):
    response = make_response(url=BASE + ".dmr.xml", body="<Dataset dmrVersion='1.0'>")
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert len(results) == 1
    assert results[0]["dap_version"] == "4"


def test_on_dmr_includes_server_header_when_present(spider):
    response = make_response(
        url=BASE + ".dmr.xml",
        body="dmrVersion",
        headers={"Server": b"Hyrax/1.17.1"},
    )
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert results[0]["server"] == "Hyrax/1.17.1"


def test_on_dmr_missing_headers_decode_to_empty_string_not_error(spider):
    response = make_response(url=BASE + ".dmr.xml", body="dmrVersion")
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert results[0]["xdods_server"] == ""
    assert results[0]["server"] == ""


# -- on_dmr: falls through to DAP2 probe --


def test_on_dmr_no_signature_falls_through_to_dds_request(spider):
    response = make_response(url=BASE + ".dmr.xml", body="<html>not dap</html>")
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert len(results) == 1
    req = results[0]
    assert req.url == BASE + ".dds"
    assert req.callback == spider.on_dds
    assert req.cb_kwargs == {"base": BASE, "seed_index": 1}
    assert req.dont_filter is True


def test_on_dmr_non_200_falls_through_to_dds_even_if_body_would_match(spider):
    # status check short-circuits the "and" before the body/header check, so
    # a non-200 response falls through to the DAP2 attempt regardless of
    # whether the body looks like a DAP4 signature.
    response = make_response(url=BASE + ".dmr.xml", body="DAP/4.0", status=404)
    results = list(spider.on_dmr(response, base=BASE, seed_index=1))
    assert len(results) == 1
    assert results[0].url == BASE + ".dds"


# -- on_dds: DAP2 confirmation signals --


def test_on_dds_confirms_dap2_via_body_signature(spider):
    response = make_response(url=BASE + ".dds", body="Dataset {\n  Float64 x;\n}")
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert results == [
        {
            "url": BASE,
            "dap_version": "2",
            "probe_url": BASE + ".dds",
            "content_description": "",
            "xdods_server": "",
            "server": "",
        }
    ]


def test_on_dds_xdods_header_alone_is_not_sufficient(spider):
    # Regression test for a real false positive found via
    # tests/fixtures/regression/ captures (plan Step B2): some ERDDAP hosts
    # stamp XDODS-Server on ordinary UI pages (index listings, "Make A
    # Graph" forms), not just genuine DDS responses, so the header alone
    # must not be enough to confirm a DAP2 endpoint.
    response = make_response(
        url=BASE + ".dds", body="not a dds body", headers={"XDODS-Server": b"dods/3.7"}
    )
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert results == []


def test_on_dds_records_xdods_header_when_body_signature_present(spider):
    response = make_response(
        url=BASE + ".dds",
        body="Dataset {\n  Float64 x;\n}",
        headers={"XDODS-Server": b"dods/3.7"},
    )
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert len(results) == 1
    assert results[0]["xdods_server"] == "dods/3.7"


def test_on_dds_tolerates_leading_whitespace_before_signature(spider):
    response = make_response(url=BASE + ".dds", body="   \n  Dataset {\n}")
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert len(results) == 1


# -- on_dds: real-world regression fixtures (Step B2 capture) --
#
# These replay actual captured responses from tests/fixtures/regression/
# (see tests/tools/capture_fixtures.py) rather than hand-written bodies, to
# prove the fix above against the real false positive it was written for,
# and that it doesn't regress the one real true positive on hand.


@pytest.mark.parametrize(
    "slug",
    [
        "apdrc.soest.hawaii.edu-dcfb75a117-dds",
        "gcoos4.geos.tamu.edu-0e988be706-dds",
        "erddap.dataexplorer.oceanobservatories.org-103982f1c3-dds",
    ],
)
def test_on_dds_rejects_real_erddap_ui_pages_that_carry_xdods_header(spider, slug):
    # None of these three real captures is actual DDS content -- they're
    # ERDDAP's own UI pages (a dataset-listing table, a "Make A Graph" form,
    # a "Data Access Form") -- but all three carry XDODS-Server regardless.
    # Before the fix, header presence alone confirmed all three as
    # false-positive "DAP2 endpoints".
    response, _ = load_captured_response(slug)
    results = list(spider.on_dds(response, base="http://example.org/irrelevant-base", seed_index=1))
    assert results == []


def test_on_dds_still_confirms_real_hyrax_true_positive(spider):
    response, _ = load_captured_response("test.opendap.org_8080-3b6f8c0461-dds")
    results = list(
        spider.on_dds(
            response, base="http://test.opendap.org:8080/opendap/data/nc/fnoc1.nc", seed_index=1
        )
    )
    assert len(results) == 1
    assert results[0]["dap_version"] == "2"


def test_on_dds_no_signal_yields_nothing(spider):
    response = make_response(url=BASE + ".dds", body="<html>404-ish page</html>")
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert results == []


def test_on_dds_non_200_yields_nothing_even_if_body_would_match(spider):
    response = make_response(
        url=BASE + ".dds", body="Dataset {\n}", status=500
    )
    results = list(spider.on_dds(response, base=BASE, seed_index=1))
    assert results == []


# -- on_error: never raises --


def test_on_error_does_not_raise(spider):
    failure = types.SimpleNamespace(
        value=Exception("boom"),
        request=types.SimpleNamespace(cb_kwargs={"seed_index": 1}),
    )
    spider.on_error(failure)  # no assertion beyond "did not raise"
    assert spider._last_completed_seed == 1


# ---- DapSpider.parse_thredds_catalog (plan Step A3) -----------------------
#
# NOTE: these fixtures must be built with make_xml_response, not
# make_response. A plain TextResponse's .selector defaults to an HTML
# parser, under which the namespace-based XPath matching below silently
# finds nothing (empty results, no error) regardless of whether the XML
# uses a default or prefixed namespace. Confirmed empirically before writing
# these tests: production Scrapy sniffs a real .xml catalog response into an
# XmlResponse automatically, so this is a fixture-construction detail, not a
# spider bug.

CATALOG_URL = "http://example.org/thredds/catalog/catalog.xml"


def test_parse_thredds_catalog_follows_subcatalog_ref(spider):
    body = """<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"
         xmlns:xlink="http://www.w3.org/1999/xlink">
  <catalogRef xlink:href="sub/catalog.xml" xlink:title="Sub"/>
</catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert len(results) == 1
    assert results[0].url == "http://example.org/thredds/catalog/sub/catalog.xml"
    assert results[0].callback == spider.parse_thredds_catalog


@pytest.mark.parametrize("service_type", ["OPENDAP", "opendap", "OpenDAP"])
def test_parse_thredds_catalog_matches_servicetype_case_insensitively(
    spider, service_type
):
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0">
  <service name="odap" serviceType="{service_type}" base="/thredds/dodsC/"/>
  <dataset name="foo" urlPath="test/foo.nc"/>
</catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert len(results) == 1
    assert results[0].url == "http://example.org/thredds/dodsC/test/foo.nc.dmr.xml"


def test_parse_thredds_catalog_no_service_defaults_to_dodsc_prefix(spider):
    body = """<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0">
  <dataset name="foo" urlPath="test/foo.nc"/>
</catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert len(results) == 1
    assert results[0].url == "http://example.org/thredds/dodsC/test/foo.nc.dmr.xml"


def test_parse_thredds_catalog_multiple_opendap_services_probes_each(spider):
    body = """<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0">
  <service name="odap1" serviceType="OPENDAP" base="/thredds/dodsC/"/>
  <service name="odap2" serviceType="OPENDAP" base="/alt/dodsC/"/>
  <dataset name="foo" urlPath="test/foo.nc"/>
</catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    urls = sorted(r.url for r in results)
    assert urls == sorted(
        [
            "http://example.org/thredds/dodsC/test/foo.nc.dmr.xml",
            "http://example.org/alt/dodsC/test/foo.nc.dmr.xml",
        ]
    )


def test_parse_thredds_catalog_matches_regardless_of_documents_own_prefix(spider):
    # register_namespace binds a prefix to a URI in the *selector*, matched
    # by URI at XPath time -- so this should work whether the source XML
    # uses the same "t:" prefix as the code, a different prefix, or (the
    # common case for real TDS servers) a default/unprefixed namespace, as
    # covered by the other tests in this section. Using an unrelated prefix
    # name here to isolate that the match is URI-based, not text-based.
    body = """<?xml version="1.0" encoding="UTF-8"?>
<thredds:catalog xmlns:thredds="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"
                  xmlns:xlink="http://www.w3.org/1999/xlink">
  <thredds:service name="odap" serviceType="OPENDAP" base="/thredds/dodsC/"/>
  <thredds:dataset name="foo" urlPath="test/foo.nc"/>
</thredds:catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert len(results) == 1
    assert results[0].url == "http://example.org/thredds/dodsC/test/foo.nc.dmr.xml"


def test_parse_thredds_catalog_html_rendered_page_yields_nothing(spider):
    # KNOWN GAP, documented here rather than silently fixed: is_thredds_catalog
    # classifies a "*/thredds/catalog*.html" URL as a THREDDS catalog (see
    # test_is_thredds_catalog_true_cases), but a real THREDDS server's
    # catalog.html is an XSLT-rendered HTML *view* of catalog.xml -- plain
    # <a href> links, none of the InvCatalog catalogRef/service/dataset
    # elements this parser looks for. Seeding with a "*.html" catalog URL
    # therefore recurses into nothing, matching the "did not get crawled"
    # catalog.html entries noted in crawls/first/notes_from_first_crawl.md
    # (e.g. gcoos5.geos.tamu.edu/thredds/catalog/catalog.html).
    body = """<html><body><h1>Catalog</h1><a href="foo.nc.html">foo.nc</a></body></html>"""
    response = make_xml_response(
        url="http://example.org/thredds/catalog/catalog.html", body=body
    )
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert results == []


# ---- DapSpider.start (plan Step A4) ---------------------------------------


async def _drain(agen):
    return [item async for item in agen]


def test_start_skips_blank_and_comment_lines(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("\n# a comment\n   \nhttp://example.org/data/foo.dds\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    # the only non-skipped line is the last one; skipped lines produce no
    # requests at all, not empty/error requests
    assert len(results) == 1
    assert results[0].url == "http://example.org/data/foo.dmr.xml"


def test_start_classifies_thredds_catalog_seed(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("http://example.org/thredds/catalog/catalog.xml\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    req = results[0]
    # dispatched to parse_thredds_catalog on the raw seed URL, not stripped
    assert req.url == "http://example.org/thredds/catalog/catalog.xml"
    assert req.callback == spider.parse_thredds_catalog
    assert req.errback == spider.on_error


def test_start_probe_seed_uses_stripped_base_not_raw_url(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("http://example.org/data/foo.dds\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    # base is the DAP-suffix-stripped seed, then probe() re-appends .dmr.xml
    # -- the raw seed URL itself is never requested directly
    assert results[0].url == "http://example.org/data/foo.dmr.xml"
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo", "seed_index": 1}


def test_start_probe_seed_strips_query_string_before_suffix(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("http://example.org/data/foo.dds?dataset=1\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    # query string dropped AND suffix stripped, in that order -- not glued
    # onto the query string tail (issue #4)
    assert results[0].url == "http://example.org/data/foo.dmr.xml"
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo", "seed_index": 1}


def test_start_probe_seed_with_unsuffixed_query_string_issue_4(tmp_path, spider):
    # Real-world case from tests/fixtures/regression_seeds.txt's
    # "demonstrates the dead strip_query_string finding" seed: before the
    # fix, the suffix got glued after "distinct()" instead of onto the base.
    seeds = tmp_path / "seeds.txt"
    seeds.write_text(
        "https://erddap.dataexplorer.oceanobservatories.org/erddap/tabledap/"
        "allDatasets.html?accessible,dataStructure,cdm_data_type,class,"
        "institution,testOutOfDate&distinct()\n"
    )
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    # suffix appended to the real base, not glued after "distinct()". ".html"
    # is also stripped (issue #3): confirmed on the wire that
    # allDatasets.html.dmr.xml/.dds 404s ("unknown datasetID=allDatasets.html")
    # while allDatasets.dmr.xml/.dds is the real ERDDAP dataset.
    assert results[0].url == (
        "https://erddap.dataexplorer.oceanobservatories.org/erddap/tabledap/"
        "allDatasets.dmr.xml"
    )


def test_start_probes_seed_with_no_suffix_as_is(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("http://example.org/data/foo\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    assert results[0].url == "http://example.org/data/foo.dmr.xml"
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo", "seed_index": 1}


def test_start_no_seeds_file_yields_nothing(spider):
    spider.seeds_file = None
    results = asyncio.run(_drain(spider.start()))
    assert results == []


def test_start_resume_from_skips_already_processed_seeds(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text(
        "http://a.example.org/data/foo.dds\n"
        "http://b.example.org/data/bar.dds\n"
        "http://c.example.org/data/baz.dds\n"
    )
    spider.seeds_file = str(seeds)
    spider.resume_from = 2
    results = asyncio.run(_drain(spider.start()))
    # only the third seed (index 3) is dispatched; the first two are counted
    # but not re-requested
    assert len(results) == 1
    assert results[0].url == "http://c.example.org/data/baz.dmr.xml"
    assert spider._seed_index == 3
    # only seed 3 was dispatched (one outstanding .dmr.xml request); seeds 1
    # and 2 were skipped entirely, never even incrementing pending counts.
    # start() alone (no Crawler pulling responses) never completes a
    # request, so _last_completed_seed stays 0 here -- that's exercised via
    # the on_dmr/on_dds/on_error paths and advance_seed_watermark directly.
    assert spider._pending_by_seed == {3: 1}
    assert spider._last_completed_seed == 0


def test_start_resume_from_zero_dispatches_all_seeds(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text(
        "http://a.example.org/data/foo.dds\nhttp://b.example.org/data/bar.dds\n"
    )
    spider.seeds_file = str(seeds)
    spider.resume_from = 0
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 2
    assert spider._pending_by_seed == {1: 1, 2: 1}


# ---- Step B3: remaining real-world regression fixtures --------------------
#
# Every fixture in tests/fixtures/regression/ (tests/tools/capture_fixtures.py)
# replayed here, beyond the on_dds-specific ones already covered while fixing
# the XDODS-Server false positive above. Pins down real, observed behavior so
# a future change to dap_spider.py shows up as a diff here instead of
# silently changing behavior against real institutional hosts.

ON_DMR_FALLS_THROUGH_TO_DDS = [
    "savannah.gnu.org-f596456d85-dmr",
    "apdrc.soest.hawaii.edu-dcfb75a117-dmr",
    "erddap.dataexplorer.oceanobservatories.org-103982f1c3-dmr",
    "gcoos4.geos.tamu.edu-0e988be706-dmr",
    "pae-paha.pacioos.hawaii.edu-31aff2ba4d-dmr",
    "pae-paha.pacioos.hawaii.edu-c5af10e022-dmr",
    "wcs.hycom.org-94959ae7b2-dmr",
]


@pytest.mark.parametrize("slug", ON_DMR_FALLS_THROUGH_TO_DDS)
def test_on_dmr_real_captures_fall_through_to_dds(spider, slug):
    # Covers: a 404 false-positive host (savannah.gnu.org), three real ERDDAP
    # hosts whose dmr.xml probe has no DAP4 signal (the same three whose dds
    # probe was the on_dds false positive above), the .html-suffixed dodsC
    # gap's dmr side (400 "dods-error", pae-paha.pacioos.hawaii.edu x2), and
    # the jnlp-embedded-URL false positive (wcs.hycom.org).
    response, data = load_captured_response(slug)
    base = strip_dap_suffix(data["seed"])
    results = list(spider.on_dmr(response, base=base, seed_index=1))
    assert len(results) == 1
    assert results[0].url == base + ".dds"
    assert results[0].callback == spider.on_dds


def test_on_dmr_real_hyrax_capture_confirms_dap4(spider):
    response, data = load_captured_response("test.opendap.org_8080-3b6f8c0461-dmr")
    base = strip_dap_suffix(data["seed"])
    results = list(spider.on_dmr(response, base=base, seed_index=1))
    assert len(results) == 1
    assert results[0]["dap_version"] == "4"
    assert results[0]["url"] == base


ON_DDS_REAL_CAPTURES_YIELD_NOTHING = [
    "savannah.gnu.org-f596456d85-dds",
    "pae-paha.pacioos.hawaii.edu-31aff2ba4d-dds",
    "pae-paha.pacioos.hawaii.edu-c5af10e022-dds",
    "wcs.hycom.org-94959ae7b2-dds",
]


@pytest.mark.parametrize("slug", ON_DDS_REAL_CAPTURES_YIELD_NOTHING)
def test_on_dds_real_captures_yield_nothing(spider, slug):
    # The three real ERDDAP false positives already have dedicated coverage
    # above (test_on_dds_rejects_real_erddap_ui_pages_that_carry_xdods_header);
    # these four round out the rest of the frozen seed list's dds captures:
    # a 404 (savannah.gnu.org), the .html-suffixed dodsC gap's actual wire shape
    # (200 status, 0-byte body -- no signal at all, a false negative rather
    # than a false positive), and the jnlp false positive (wcs.hycom.org).
    response, data = load_captured_response(slug)
    base = strip_dap_suffix(data["seed"])
    results = list(spider.on_dds(response, base=base, seed_index=1))
    assert results == []


CATALOG_REAL_CAPTURES_YIELD_NOTHING = [
    "gcoos5.geos.tamu.edu-0a81cde700-catalog",
    "sgbd.acmad.org_8080-348d7e4ca0-catalog",
]


@pytest.mark.parametrize("slug", CATALOG_REAL_CAPTURES_YIELD_NOTHING)
def test_parse_thredds_catalog_real_captures_yield_nothing(spider, slug):
    # Real Content-Type on both captures is text/html, so Scrapy would build
    # an HtmlResponse (not XmlResponse) for these in production too --
    # load_captured_response's plain TextResponse (HTML-parser .selector) is
    # the faithful choice here, unlike the synthetic .xml fixtures in Step A3
    # which needed make_xml_response. Confirms Step A3's root-cause theory
    # (catalog.html is a rendered view with zero InvCatalog elements) against
    # the real pages, not just a hand-written stand-in.
    response, _ = load_captured_response(slug)
    results = list(spider.parse_thredds_catalog(response, seed_index=1))
    assert results == []


# ---- IdentityEncodingMiddleware (issue #20) --------------------------------
#
# Some ERDDAP hosts send 'Content-Encoding: identity' -- a legitimate
# HTTP/1.1 no-op token -- which Scrapy's own HttpCompressionMiddleware
# doesn't recognize and logs as an unsupported-encoding WARNING. These
# tests cover the middleware in isolation, plus one integration check that
# it actually silences the warning HttpCompressionMiddleware would
# otherwise log.

def test_identity_encoding_middleware_strips_sole_identity_header():
    request = Request("http://example.org/foo.dmr.xml")
    response = make_response(request.url, headers={"Content-Encoding": "identity"})
    out = IdentityEncodingMiddleware().process_response(request, response, spider=None)
    assert "Content-Encoding" not in out.headers


def test_identity_encoding_middleware_leaves_other_encodings_untouched():
    request = Request("http://example.org/foo.dmr.xml")
    response = make_response(request.url, headers={"Content-Encoding": "gzip"})
    out = IdentityEncodingMiddleware().process_response(request, response, spider=None)
    assert out.headers.getlist("Content-Encoding") == [b"gzip"]


def test_identity_encoding_middleware_strips_identity_from_combined_list():
    request = Request("http://example.org/foo.dmr.xml")
    response = make_response(
        request.url, headers={"Content-Encoding": "gzip, identity"}
    )
    out = IdentityEncodingMiddleware().process_response(request, response, spider=None)
    assert out.headers.getlist("Content-Encoding") == [b"gzip"]


def test_identity_encoding_middleware_noop_without_content_encoding_header():
    request = Request("http://example.org/foo.dmr.xml")
    response = make_response(request.url)
    out = IdentityEncodingMiddleware().process_response(request, response, spider=None)
    assert "Content-Encoding" not in out.headers


def test_identity_encoding_middleware_prevents_httpcompression_warning(caplog):
    request = Request("http://erddap.secoora.org/erddap/tabledap/foo.dmr.xml")
    response = make_response(
        request.url,
        body='<Dataset dmrVersion="1.0">',
        headers={"Content-Encoding": "identity"},
    )

    stripped = IdentityEncodingMiddleware().process_response(
        request, response, spider=None
    )
    with caplog.at_level(
        "WARNING", logger="scrapy.downloadermiddlewares.httpcompression"
    ):
        HttpCompressionMiddleware().process_response(request, stripped)
    assert "unsupported encoding" not in caplog.text


# ---- DnsFailureLogFilter (issue #26) ---------------------------------------
#
# RobotsTxtMiddleware.robot_parser (scrapy/downloadermiddlewares/robotstxt.py)
# logs *every* robots.txt-fetch failure at ERROR with a full traceback,
# including plain DNS lookup failures -- routine and expected across a large
# seed list. DnsFailureLogFilter, registered on that logger at dap_spider
# import time, downgrades just that case to a single INFO line naming the
# host and drops the original record; anything else still surfaces at ERROR.


def _log_robotstxt_error(exc, request):
    """Fabricate the exact ERROR + traceback log call robot_parser makes on
    a robots.txt-fetch failure, so DnsFailureLogFilter can be exercised
    without a real crawl. exc_info=True needs a live exception, hence the
    raise/except round-trip."""
    robots_logger = logging.getLogger("scrapy.downloadermiddlewares.robotstxt")
    try:
        raise exc
    except type(exc):
        robots_logger.error(
            "Error downloading %(request)s: %(f_exception)s",
            {"request": request, "f_exception": exc},
            exc_info=True,
        )


def test_dns_failure_log_filter_silences_cannot_resolve_host_error(caplog):
    request = Request("http://dead-host.example.org/thredds/dodsC/21.dmr.xml")
    with caplog.at_level(logging.INFO):
        _log_robotstxt_error(CannotResolveHostError("DNS lookup failed"), request)
    assert "Error downloading" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "DNS lookup failed for dead-host.example.org" in caplog.text


def test_dns_failure_log_filter_silences_raw_twisted_dns_lookup_error(caplog):
    # Defensive branch: some code path could raise the unwrapped Twisted
    # exception directly instead of Scrapy's CannotResolveHostError wrapper.
    request = Request("http://dead-host.example.org/thredds/dodsC/21.dmr.xml")
    with caplog.at_level(logging.INFO):
        _log_robotstxt_error(DNSLookupError("dead-host.example.org"), request)
    assert "Error downloading" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "DNS lookup failed for dead-host.example.org" in caplog.text


def test_dns_failure_log_filter_leaves_other_robotstxt_errors_at_error(caplog):
    # Not a DNS failure -- must not be silently swallowed.
    request = Request("http://flaky-host.example.org/thredds/dodsC/21.dmr.xml")
    with caplog.at_level(logging.INFO):
        _log_robotstxt_error(TimeoutError("timed out"), request)
    assert "Error downloading" in caplog.text
    assert "DNS lookup failed for" not in caplog.text


# Runs as a standalone script in a fresh subprocess -- see comment on
# test_dns_failure_log_filter_silences_real_robotstxt_dns_failure (issue #28)
# for why. Prints whatever got logged at INFO+ to stdout for the parent
# test to assert on.
_DNS_FAILURE_PROBE_SCRIPT = """
import io
import logging
import sys

import scrapy
from scrapy.crawler import CrawlerProcess

import dap_spider  # noqa: F401  (import side effect: registers DnsFailureLogFilter)

log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.INFO)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)


class _DnsFailureProbeSpider(scrapy.Spider):
    # Mirrors dap_spider.py's real requests, which always set
    # errback=self.on_error (see plan root-cause section) -- without one,
    # Scrapy's own generic per-request failure logging (a
    # differently-sourced "Error downloading ..." message, unrelated to
    # RobotsTxtMiddleware) would confound this test's assertions.
    name = "dns_failure_probe"
    custom_settings = {"ROBOTSTXT_OBEY": True, "RETRY_ENABLED": False}

    async def start(self):
        yield scrapy.Request(
            "http://this-host-does-not-resolve.invalid/probe",
            callback=self.parse,
            errback=self.on_error,
        )

    def parse(self, response):
        return

    def on_error(self, failure):
        return


process = CrawlerProcess(settings={"LOG_ENABLED": False}, install_root_handler=False)
process.crawl(_DnsFailureProbeSpider)
process.start()

sys.stdout.write(log_stream.getvalue())
"""


@pytest.mark.live
def test_dns_failure_log_filter_silences_real_robotstxt_dns_failure():
    """
    Live-network exception to the "no HTTP requests to remote hosts" tests
    policy in CLAUDE.md, approved for this issue: drives a real Scrapy crawl
    against a hostname under the .invalid TLD (RFC 2606 -- reserved to
    always fail DNS resolution, so this never reaches an actual host) to
    confirm the filter silences a genuine CannotResolveHostError raised by
    RobotsTxtMiddleware end to end, not just a fabricated log record.

    Runs the crawl in a subprocess rather than in-process (issue #28): a
    Twisted reactor can only be started once per process -- CrawlerProcess
    .start() calls reactor.run(), and a second call anywhere else in the
    same pytest process (e.g. test_live_smoke.py's live test) raises
    ReactorNotRestartable regardless of which CrawlerProcess instance made
    it or the order the two tests run in. A fresh subprocess always gets a
    reactor that has never been started, so this test can't collide with
    any other live test sharing the same `pytest -m live` invocation.

    Excluded from the default run (pytest.ini: addopts = -m "not live");
    run explicitly with: pytest -m live
    """
    result = subprocess.run(
        [sys.executable, "-c", _DNS_FAILURE_PROBE_SCRIPT],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (exit {result.returncode}):\n{result.stderr}"
    )
    log_text = result.stdout

    assert "Error downloading" not in log_text
    assert "Traceback" not in log_text
    assert "DNS lookup failed for this-host-does-not-resolve.invalid" in log_text
