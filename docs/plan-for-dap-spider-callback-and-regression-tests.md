# Plan: DapSpider callback unit tests + regression tests from real crawl data

## Context

`tests/test_dap_spider.py` already covers the three pure helper functions
(`strip_dap_suffix`, `strip_query_string`, `is_thredds_catalog`) per
`plan-for-dap-spider-unit-tests.md`. Out of scope there, and the subject of
this plan:

1. **`DapSpider`'s own methods** (`probe`, `on_dmr`, `on_dds`,
   `parse_thredds_catalog`, `start`, `on_error`) — untested. These are Scrapy
   callbacks, so they need request/response fixtures, not real network I/O.
2. **Regression coverage using real data.** `crawls/first/` now holds three
   real candidate-URL files from an actual Stage-1 run
   (`candidate_urls.2026.21.txt` — 11,913 lines, `candidate_urls_not_erddap.txt`
   — 3,258, `candidate_urls_not_erddap_not_info.txt` — 1,652), a real Stage-2
   output (`dap_endpoints.jsonl`, 821 confirmed endpoints), and
   `notes_from_first_crawl.md` documenting things that did *not* get crawled
   correctly. Running any of these files wholesale as a "test" takes hours and
   hits real hosts — not viable as a repeatable regression check.

Both tracks avoid live network calls in the default test run. Nothing here
hits a real host as part of `pytest`.

## Findings from reading the code + data that inform this plan

Worth flagging now rather than silently working around:

- **`strip_query_string` is defined and unit-tested but never called anywhere
  in `dap_spider.py`.** `start()` builds `base` via `strip_dap_suffix(url)`
  only. Consequence, visible in real `dap_endpoints.jsonl` output: seeds with
  a query string (e.g. an ERDDAP `.html?...` URL) get probed as
  `...&distinct().dds` — the DAP suffix appended *after* the query string,
  not as a real extension. It happens to work against lenient ERDDAP servers
  in the sample data, but it's fragile. This plan will add a regression test
  that pins down *today's* behavior (probe URL includes the query string
  verbatim) rather than silently fixing it — that's a separate decision for
  you.
- `.html`-suffixed `dodsC` URLs (e.g.
  `https://pae-paha.pacioos.hawaii.edu/thredds/dodsC/hmrg_bathytopo_50m_mhi.html`,
  present in `candidate_urls_not_erddap_not_info.txt`) aren't in
  `DAP_SUFFIXES`, so `strip_dap_suffix` leaves the `.html` in place and the
  probe becomes `...mhi.html.dmr.xml`. `notes_from_first_crawl.md` lists
  several of these among the seeds that "did not get crawled." Good
  regression-fixture candidate to document as a known gap.
- `alexporn.org/tags/dap/` in `candidate_urls.2026.21.txt` is a real
  false-positive from the Stage-1 regex (matches on the literal substring
  `dap`, not a DAP host) — a good "should probe and correctly find nothing"
  case.

None of these get fixed as part of this plan — they're noted so the tests
assert real, current behavior instead of what we'd wish it did.

## Part A — `DapSpider` callback unit tests (no network)

### Step A1 — Fixture harness (spike, small, reviewable on its own)

Build a small helper in `tests/conftest.py` for constructing a fake Scrapy
response without a downloader:

```python
from scrapy.http import TextResponse, Request

def make_response(url, body="", status=200, headers=None):
    return TextResponse(
        url=url,
        status=status,
        headers=headers or {},
        body=body.encode("utf-8"),
    )
```

And confirm empirically (this is the point of doing it as its own step)
that `DapSpider(seeds_file="x")` can be instantiated directly and its bound
methods (`self.logger`, `self.probe`, etc.) called without a `Crawler`
attached — Scrapy's `Spider.logger` works off `self.name` alone, but I want
to verify rather than assume before building 20 tests on top of it.

**Review point:** confirm this harness approach (real `TextResponse`
objects, no `Crawler`/`CrawlerProcess`) rather than mocking `response` with
`unittest.mock.Mock` — real response objects exercise the actual header
decoding and `response.text`/`.selector` machinery, which is where subtle
bugs live, at the cost of a slightly heavier fixture.

### Step A2 — `probe`, `on_dmr`, `on_dds`

- `probe(base)`: yields exactly one `Request` to `base + ".dmr.xml"`,
  `callback=on_dmr`, `cb_kwargs={"base": base}`, `dont_filter=True`.
- `on_dmr`:
  - 200 + `"DAP/4.0"` in body → yields the DAP4 result dict, checks all
    fields (`url`, `dap_version`, `probe_url`, `xdap`, `server`).
  - 200 + `XDAP` header starting with `"4"` (body has no signature) → DAP4
    result.
  - 200 + `"dapVersion"` in body → DAP4 result.
  - 200, none of the three signals → yields one fallback `Request` to
    `base + ".dds"`, `callback=on_dds`.
  - Non-200 status (404/500) with a body that *would* match if status were
    200 → still falls through to the DAP2 `Request` (status is checked with
    `and`, short-circuiting before the body check) — pins down current
    behavior.
  - Header decoding: confirm `XDAP`/`Server` absent → empty string, not a
    `KeyError` or `None`.
- `on_dds`:
  - 200 + body `.lstrip()` startswith `"Dataset {"` → DAP2 result dict, all
    fields checked.
  - 200 + `XDODS-Server` header present but body doesn't start with
    `"Dataset {"` → still yields (header alone is sufficient per the `or`).
  - 200 + `Content-Description` containing `"dods"` case-insensitively, no
    other signal → yields.
  - 200, no signal at all → yields nothing (DAP2 has no further fallback).
  - Non-200 → yields nothing regardless of body.
  - Leading whitespace before `"Dataset {"` is tolerated (`.lstrip()`).
- `on_error(failure)`: pass an object with a `.value` attribute (e.g.
  `types.SimpleNamespace(value=Exception("boom"))`) and assert it doesn't
  raise — this is the one method whose entire contract is "never crash the
  run."

**Review point:** none expected — this is mechanical once A1 is settled.

### Step A3 — `parse_thredds_catalog`

Build small literal THREDDS XML fixtures rather than reusing anything from
`crawls/first/` (those are seed *URLs*, not catalog XML bodies we have on
disk).

Cases:
- Sub-catalog recursion: a `catalogRef` with an `xlink:href` yields a
  `response.follow(...)` request with `callback=parse_thredds_catalog`
  (check `.url` resolves relative to the fixture's `response.url`).
- `serviceType="OPENDAP"` (and `"opendap"`, `"OpenDAP"`) all resolve via the
  case-normalizing xpath — one parametrized test per casing.
- A `dataset[@urlPath]` combined with the service `@base` produces a
  `probe()` call for the joined URL — assert the resulting `Request` targets
  `<base><urlPath>.dmr.xml`.
- No `service` element at all → falls back to the hardcoded
  `/thredds/dodsC/` prefix.
- Multiple `service` elements with `serviceType="OPENDAP"` → probes the
  dataset against *each* base (the `for base in opendap_bases` loop).
- **Default-namespace fixture, not just prefixed.** Real THREDDS catalogs
  typically declare `xmlns="http://www.unidata.ucar.edu/.../InvCatalog/v1.0"`
  as the *default* namespace with no prefix on elements
  (`<catalog xmlns="...">`, bare `<dataset>`, not `<t:dataset>`), whereas a
  fixture I'd naturally hand-write tends to mirror the code's own `t:`
  prefix. `sel.register_namespace` matches by URI, not by the source
  document's own prefix choice, so this *should* still work — but that's
  exactly the kind of assumption I want a real test to confirm rather than
  have it silently pass only because my fixture happened to use the same
  prefix as the code. I'll write at least one fixture using the
  default-namespace style.

**Review point:** if the default-namespace case fails, that's a real bug
(catalogs from most institutional TDS servers wouldn't be parsed at all) —
flagging now so it doesn't get quietly "fixed" as part of what was supposed
to be test-only work. I'll report the result before touching
`dap_spider.py`.

### Step A4 — `start()` (seed classification)

`start()` is an `async def` generator reading a seeds file. No new test
dependency needed — drive it with a small local `asyncio.run(...)` helper in
the test rather than adding `pytest-asyncio`:

```python
async def _drain(agen):
    return [item async for item in agen]
```

Cases, using `tmp_path` for the seeds file:
- Blank lines and `#`-comment lines are skipped.
- A THREDDS-catalog-looking line dispatches to `parse_thredds_catalog`.
- A line with a DAP suffix is stripped to its base and dispatches into
  `probe()` (i.e. ends up as a `.dmr.xml` request for the stripped base, not
  the raw seed).
- A line with no suffix and not a THREDDS catalog is probed as-is.
- No `seeds_file` at all → logs an error and yields nothing (doesn't raise).

## Part B — Regression tests from real crawl data (still no live network)

Goal: catch real-world shapes that hand-written fixtures in Part A wouldn't
think to cover, without a multi-hour live crawl.

### Step B1 — Select and freeze a small seed set

Hand-pick ~20-30 URLs from `crawls/first/`, stratified, and commit them as a
small fixture list (e.g. `tests/fixtures/regression_seeds.txt`) — not a
random sample, a deliberate one covering:

- A handful of confirmed-live DAP2/DAP4 endpoints, cross-referenced against
  `crawls/first/dap_endpoints.jsonl` (known-true-positive, e.g. the
  `test.opendap.org` DAP4 Hyrax entry and one ERDDAP DAP2 entry).
- The ERDDAP `.html?...` query-string case (known dead-`strip_query_string`
  behavior above).
- One or two `.html`-suffixed `dodsC` URLs from
  `candidate_urls_not_erddap_not_info.txt` (known gap above).
- One THREDDS catalog URL flagged in `notes_from_first_crawl.md` as "did not
  get crawled" (e.g. the `sgbd.acmad.org` or `tds.hycom.org` catalog URLs) —
  worth finding out *why* it didn't get crawled, since `is_thredds_catalog`'s
  own logic doesn't obviously explain a miss on a `.html` catalog URL with a
  query string.
- The `alexporn.org/tags/dap/` false positive.

**Review point:** I'll list the exact URLs chosen before capturing anything,
so you can swap in specific ones you know are interesting (e.g. a particular
host that's been flaky).

### Step B2 — One-time capture (manual, not part of `pytest`)

A small standalone script (`tests/tools/capture_fixtures.py`, run by hand,
never invoked by the test suite or CI) fetches each URL in the frozen seed
list *once* — using the probe URLs the spider itself would generate
(`<base>.dmr.xml`, `<base>.dds`, or the catalog XML itself) — and writes each
response's status, headers, and body to
`tests/fixtures/regression/<slug>.json`. This is the only step that touches
real hosts, and it's a one-time, low-volume (~30-60 requests total, at the
spider's normal polite rate) capture — not a repeated test run.

**Review point:** confirm you're OK with committing captured response bodies
into the repo (sizes should be small — DAP metadata docs and small THREDDS
catalog XML, not data payloads) before I run the capture script.

### Step B3 — Replay fixtures as regression tests

Load each captured `(url, status, headers, body)` fixture and feed it through
the same `make_response` + bound-callback harness from Part A. These are
still fast, deterministic, offline tests — the only difference from Part A is
the fixture content comes from a real server instead of hand-written XML.
Assertions pin down *actual observed* behavior (e.g. "the ERDDAP query-string
seed currently produces this exact malformed-looking probe URL and still
gets confirmed" or "the `.html` dodsC seed currently produces zero confirmed
endpoints") so future changes to `dap_spider.py` show up as a diff here
instead of silently changing behavior against real institutional hosts.

### Step B4 (optional) — opt-in live smoke test

A `@pytest.mark.live`-marked test (excluded from the default `pytest` run via
`addopts = -m "not live"` in a new `pytest.ini`/`pyproject.toml` section),
running the real `DapSpider` via `CrawlerProcess` against a tiny (5-10 URL)
seed file hitting real hosts. Not part of routine testing — a manual,
occasional check that the full Scrapy stack (robots.txt, autothrottle,
retries) still behaves, run by you when you want that assurance. I'd only
build this if you want it; flagging it as optional rather than assuming.

## Out of scope

- The full `crawls/first/*.txt` files themselves — production discovery
  inputs, not test material, per your existing framing.
- `cc_dap_discover.py` / Stage 1 — this plan is Stage 2 (`dap_spider.py`)
  only.
- Fixing any of the three findings noted above (`strip_query_string` dead
  code, `.html`-suffixed `dodsC` bases, the uncrawled-catalog note) — this
  plan only makes their current behavior visible and pinned down in tests.

## Sequencing

Steps A1 → A4 → B1 → B2 → B3 → B4, each a separate reviewable unit as per
your process. A1 gates everything else in Part A; B1 gates B2/B3. I'll pause
after each step for review before continuing, and update
`plan-for-dap-spider-callback-and-regression-tests-log.md` with a timestamp
after each one.
