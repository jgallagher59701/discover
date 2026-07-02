# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-stage pipeline that discovers public OPeNDAP (DAP2/DAP4) endpoints on the
web without crawling it from scratch:

```
cc_dap_discover.py  ──►  candidate_urls.txt  ──►  dap_spider.py  ──►  dap_endpoints.jsonl
   (DuckDB / S3)                                    (Scrapy)
```

Rationale: DAP servers are sparse and clustered on relatively few institutional
hosts, so mining Common Crawl's URL index for path/suffix signatures first
(cheap, no requests to real hosts) and only then crawling/probing the
candidates (expensive, hits real hosts) beats a broad crawl.

## Environment & commands

```bash
conda create -n discover duckdb scrapy   # or: conda activate discover
# equivalently: pip install -r requirements.txt   (duckdb, scrapy)
```

Stage 1 — mine Common Crawl (no network access to target hosts, only to S3):
```bash
python cc_dap_discover.py
# writes candidate_urls.txt and candidate_urls.csv
```

Stage 2 — probe and verify candidates (hits real hosts — see politeness note below):
```bash
python dap_spider.py candidate_urls.txt
# writes dap_endpoints.jsonl
```

There is no build step, linter, or test suite in this repo — validate changes
by running the two stages above (Stage 1 against a small/single crawl, Stage 2
against a short seed list) and inspecting the output files.

## Architecture

**`cc_dap_discover.py`** (Stage 1): queries the public Common Crawl Parquet
index (`s3://commoncrawl/cc-index/table/cc-main/warc/...`) via DuckDB's
`httpfs` extension. `CRAWLS` must be updated to current crawl IDs from
https://commoncrawl.org/get-started before a real run. `DAP_REGEX` encodes the
URL path/suffix signatures that identify DAP candidates (`/thredds/dodsC/`,
`/opendap/`, `/erddap/{grid,table}dap/`, `.dds`/`.das`/`.dods`/`.dmr`/`.dmr.xml`/
`.dap`/`.dsr`). Several alternate `QUERY_TEMPLATE`/TLD-filter variants are kept
commented out in the file as ready-to-swap options (narrower `.edu`/`.org`-only
filtering, broader academic-TLD heuristics). An **Athena variant** of the same
query is documented in README.md for cheaper server-side scans at scale — it
uses the Glue-cataloged table instead of `read_parquet`.

Because the CC bucket is public, `SETUP_SQL` creates an anonymous
`credential_chain` S3 secret; without this DuckDB may try to pick up local AWS
credentials/metadata and fail to authenticate against a bucket that needs none.

**`dap_spider.py`** (Stage 2): a self-contained Scrapy spider (`DapSpider`).
Key design point: a crawler request is the same mechanism whether the response
is HTML or a DAP metadata document — the callbacks below inspect headers and
non-HTML bodies instead of scraping links.

- `start_requests` classifies each seed line: THREDDS catalog URL → recurse via
  `parse_thredds_catalog`; anything else → `strip_dap_suffix` to a base URL and
  `probe()`.
- `probe()` tries DAP4 first (`<base>.dmr.xml`, `on_dmr`), falling back to DAP2
  (`<base>.dds`, `on_dds`) if DAP4 doesn't confirm. Confirmation always checks
  **both** response headers (`XDAP`, `XDODS-Server`, `Content-Description`) and
  a body signature (`DAP/4.0`/`dapVersion`, or a body starting `Dataset {`) —
  header or body alone is not trusted.
- `parse_thredds_catalog` follows `catalogRef` sub-catalogs recursively and
  combines each `dataset[@urlPath]` with the catalog's OPeNDAP `service/@base`
  to build access URLs, which are fed back into `probe()`. THREDDS parsing here
  is intentionally pragmatic (no compound services / `serviceName` refs); for
  production-grade catalog walking, swap in Unidata's `siphon` or
  `thredds_crawler`.
- `custom_settings` enforces politeness by default: `ROBOTSTXT_OBEY`,
  autothrottle, 1 concurrent request per domain, ~2s download delay. Rapid
  sequential probing across many `.edu`/`.org` hosts reads as scanning to
  institutional IDS — loosen these only for hosts you operate or have
  permission to hit hard, and always set a real contact address in
  `USER_AGENT` before running against real hosts (it ships with an
  `example.org`/`you@example.org` placeholder).

## Data files

`candidate_urls*.{txt,csv}` in the repo root are generated Stage-1 output
snapshots from past runs (see `NOTE.md` for what each one is), not
hand-maintained inputs — regenerate with `cc_dap_discover.py` rather than
editing them.
