# Issues to file

Findings surfaced while building the `dap_spider.py` test suite (see
`plan-for-dap-spider-callback-and-regression-tests.md` and its log). Each
entry below is written as a ready-to-file GitHub issue: a suggested title,
then a body.

## Bugs / behavioral gaps (not yet fixed)

### 1. THREDDS `catalog.html` seeds are silently never parsed

`is_thredds_catalog()` (`dap_spider.py:64-66`) correctly classifies
`.html`-suffixed catalog URLs as THREDDS catalogs, but a real TDS server's
`catalog.html` is an XSLT-rendered HTML *view* (plain `<a href>` links) --
not the InvCatalog XML document `parse_thredds_catalog()`
(`dap_spider.py:172-194`) expects (`catalogRef`/`service`/`dataset`
elements). Result: zero recursion, zero probes, no error -- a silent miss.

Confirmed against real captured pages from two hosts (`gcoos5.geos.tamu.edu`,
`sgbd.acmad.org`) -- neither contains `catalogRef`, `urlPath`, or
`serviceType` anywhere in the body. Explains the "did not get crawled"
`catalog.html` entries in `crawls/first/notes_from_first_crawl.md`.

Regression tests already pin down this exact behavior:
`tests/test_dap_spider.py::test_parse_thredds_catalog_html_rendered_page_yields_nothing`
and the `test_parse_thredds_catalog_real_captures_yield_nothing` fixture
tests.

Possible fix direction: when a `.html` catalog seed is classified, request
the sibling `.xml` catalog instead (e.g. `catalog.html` -> `catalog.xml`)
before parsing.

### 2. `.html`-suffixed `dodsC` URLs are never correctly probed

`.html` isn't in `DAP_SUFFIXES` (`dap_spider.py:40-43`), so
`strip_dap_suffix()` leaves it in place, producing malformed probe URLs like
`.../foo.html.dmr.xml`.

Confirmed on the wire: `.dmr.xml` -> 400 `dods-error`; `.dds` -> 200 with an
empty body (no signal at all). A false negative -- real datasets at these
two `pae-paha.pacioos.hawaii.edu` URLs are simply missed.

Regression tests: `test_on_dmr_real_captures_fall_through_to_dds` /
`test_on_dds_real_captures_yield_nothing` (the `pae-paha...` parametrize
cases).

### 3. Query strings in seed URLs break suffix-based probing (`strip_query_string` is dead code)

`strip_query_string()` (`dap_spider.py:58-62`) is defined and unit-tested
but never called anywhere in `start()`/`probe()`.

Consequence: a seed with a query string gets the DAP suffix glued onto the
end of it (e.g. `...allDatasets.html?...&distinct().dds`), not stripped
first. Happens to still work against lenient ERDDAP servers in the real
data on hand, but it's fragile and not what the function was clearly
written to do.

### 4. `on_dmr` has the same header-alone/body-alone confirmation shape that caused the `on_dds` false positive

`on_dds` was fixed this session (it previously confirmed on
`XDODS-Server`/`Content-Description` alone, which several real ERDDAP UI
pages carry regardless of whether the URL is a real dataset).

`on_dmr` (`dap_spider.py:130-135`) still uses `"DAP/4.0" in body or
xdap.startswith("4") or "dapVersion" in body` -- the same "any one signal
alone is sufficient" shape. No confirmed instance of this causing a false
positive in the data captured so far, but the risk shape is identical.
Worth an audit, possibly against more ERDDAP/THREDDS hosts, to see if any
stamp `XDAP` broadly the way `XDODS-Server` was stamped.

### 5. CLAUDE.md's documented confirmation design doesn't match the implementation

CLAUDE.md states: "Confirmation always checks **both** response headers...
and a body signature... -- header or body alone is not trusted." The actual
code (both before, and for `on_dmr` still after, this session's fix) uses
OR logic per signal, not AND. Either the code should be tightened to match
the doc, or the doc should be corrected to describe what's actually
intentional (a real Hyrax DAP4 response has no `XDAP` header at all and
only confirms via body -- so a strict AND would break a real true positive;
this needs a deliberate design decision, not a mechanical "make it match
the doc").

## Data / operational follow-ups

### 6. `crawls/first/dap_endpoints.jsonl` is stale relative to the `on_dds` fix

Most of its 820 ERDDAP-flavored "confirmed" entries were likely produced
via the header-alone path that's now removed. They need re-verification via
a fresh Stage 2 run before being trusted as real.

### 7. Some ERDDAP hosts 302-redirect a malformed suffixed probe URL back to the canonical (un-suffixed) URL

Observed live (not just in a synthetic fixture):
`apdrc.soest.hawaii.edu/erddap/griddap/index.json?...dmr.xml` -> 302 -> the
original URL, sans suffix. This means `probe_url` in the output record can
silently not reflect the actual URL that was requested when this happens,
and `on_dmr`/`on_dds` never notice a redirect occurred. Worth deciding
whether to record `response.url` (post-redirect, current behavior) vs. the
pre-redirect request URL, or to flag redirected probes distinctly.

## Coverage / process follow-ups

### 8. No test coverage for Stage 1 (`cc_dap_discover.py`)

This session's entire test-writing effort (unit + regression + live smoke)
was explicitly scoped to Stage 2 (`dap_spider.py`) only. `DAP_REGEX` and the
DuckDB query logic in `cc_dap_discover.py` have zero tests.

### 9. `test.opendap.org`'s `robots.txt` currently blocks all crawling (`Disallow: /`)

Discovered while building the live smoke test -- confirmed independently
via `curl`. This is OPeNDAP's own reference test server, so this one may be
directly actionable rather than a code issue in this repo. Also worth
noting it wasn't consistent moment-to-moment during testing (allowed a
request, then blocked ~2 minutes later), suggesting possibly a
load-balanced deployment with inconsistent backend config.

## Not filed (working as designed)

Two Stage-1 regex false positives were found during regression fixture
selection -- `alexporn.org/tags/dap/` (matches the literal substring "dap"
in an unrelated URL) and `wcs.hycom.org/thredds/view/idv.jnlp?url=...dodsC...`
(a real `dodsC` URL embedded in a query parameter of an unrelated
viewer-launch page). Both are already correctly rejected by Stage 2 today,
so they're working as designed -- this is exactly why the two-stage
architecture exists, not a bug to fix.
