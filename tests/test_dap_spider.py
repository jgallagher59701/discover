import asyncio
import types

import pytest

from conftest import load_captured_response, make_response, make_xml_response
from dap_spider import is_thredds_catalog, strip_dap_suffix, strip_query_string, to_xml


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
    results = list(spider.on_dmr(response, base="http://example.org/data/foo"))
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
    requests = list(spider.probe(BASE))
    assert len(requests) == 1
    req = requests[0]
    assert req.url == BASE + ".dmr.xml"
    assert req.callback == spider.on_dmr
    assert req.cb_kwargs == {"base": BASE}
    assert req.dont_filter is True


# -- on_dmr: DAP4 confirmation signals --

# Even with a content description that's pretty obvious, the spider only
# uses the 'body signature.' jhrg 7/6/26
def test_on_dmr_fails_dap4_with_no_body_signature(spider):
    response = make_response(
        url=BASE + ".dmr.xml", body="not a real body", 
            headers={"Content-Description": b"application/vnd.opendap.dap4.dataset-metadata+xml"}
    )
    results = list(spider.on_dmr(response, base=BASE))
    assert len(results) == 1
    req = results[0]
    assert req.url == BASE + ".dds"
    assert req.callback == spider.on_dds
    assert req.cb_kwargs == {"base": BASE}
    assert req.dont_filter is True


def test_on_dmr_confirms_dap4_via_dmrversion_in_body(spider):
    response = make_response(url=BASE + ".dmr.xml", body="<Dataset dmrVersion='1.0'>")
    results = list(spider.on_dmr(response, base=BASE))
    assert len(results) == 1
    assert results[0]["dap_version"] == "4"


def test_on_dmr_includes_server_header_when_present(spider):
    response = make_response(
        url=BASE + ".dmr.xml",
        body="dmrVersion",
        headers={"Server": b"Hyrax/1.17.1"},
    )
    results = list(spider.on_dmr(response, base=BASE))
    assert results[0]["server"] == "Hyrax/1.17.1"


def test_on_dmr_missing_headers_decode_to_empty_string_not_error(spider):
    response = make_response(url=BASE + ".dmr.xml", body="dmrVersion")
    results = list(spider.on_dmr(response, base=BASE))
    assert results[0]["xdods_server"] == ""
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
    results = list(spider.on_dds(response, base=BASE))
    assert results == []


def test_on_dds_records_xdods_header_when_body_signature_present(spider):
    response = make_response(
        url=BASE + ".dds",
        body="Dataset {\n  Float64 x;\n}",
        headers={"XDODS-Server": b"dods/3.7"},
    )
    results = list(spider.on_dds(response, base=BASE))
    assert len(results) == 1
    assert results[0]["xdods_server"] == "dods/3.7"


def test_on_dds_tolerates_leading_whitespace_before_signature(spider):
    response = make_response(url=BASE + ".dds", body="   \n  Dataset {\n}")
    results = list(spider.on_dds(response, base=BASE))
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
    results = list(spider.on_dds(response, base="http://example.org/irrelevant-base"))
    assert results == []


def test_on_dds_still_confirms_real_hyrax_true_positive(spider):
    response, _ = load_captured_response("test.opendap.org_8080-3b6f8c0461-dds")
    results = list(
        spider.on_dds(
            response, base="http://test.opendap.org:8080/opendap/data/nc/fnoc1.nc"
        )
    )
    assert len(results) == 1
    assert results[0]["dap_version"] == "2"


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
    results = list(spider.parse_thredds_catalog(response))
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
    results = list(spider.parse_thredds_catalog(response))
    assert len(results) == 1
    assert results[0].url == "http://example.org/thredds/dodsC/test/foo.nc.dmr.xml"


def test_parse_thredds_catalog_no_service_defaults_to_dodsc_prefix(spider):
    body = """<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0">
  <dataset name="foo" urlPath="test/foo.nc"/>
</catalog>"""
    response = make_xml_response(url=CATALOG_URL, body=body)
    results = list(spider.parse_thredds_catalog(response))
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
    results = list(spider.parse_thredds_catalog(response))
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
    results = list(spider.parse_thredds_catalog(response))
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
    results = list(spider.parse_thredds_catalog(response))
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
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo"}


def test_start_probe_seed_strips_query_string_before_suffix(tmp_path, spider):
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("http://example.org/data/foo.dds?dataset=1\n")
    spider.seeds_file = str(seeds)
    results = asyncio.run(_drain(spider.start()))
    assert len(results) == 1
    # query string dropped AND suffix stripped, in that order -- not glued
    # onto the query string tail (issue #4)
    assert results[0].url == "http://example.org/data/foo.dmr.xml"
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo"}


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
    assert results[0].cb_kwargs == {"base": "http://example.org/data/foo"}


def test_start_no_seeds_file_yields_nothing(spider):
    spider.seeds_file = None
    results = asyncio.run(_drain(spider.start()))
    assert results == []


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
    results = list(spider.on_dmr(response, base=base))
    assert len(results) == 1
    assert results[0].url == base + ".dds"
    assert results[0].callback == spider.on_dds


def test_on_dmr_real_hyrax_capture_confirms_dap4(spider):
    response, data = load_captured_response("test.opendap.org_8080-3b6f8c0461-dmr")
    base = strip_dap_suffix(data["seed"])
    results = list(spider.on_dmr(response, base=base))
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
    results = list(spider.on_dds(response, base=base))
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
    results = list(spider.parse_thredds_catalog(response))
    assert results == []
