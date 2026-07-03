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

# Plans

- For any plan, write that plan in a markdown document in the 'docs' directory (e.g., plan-for-A.md).
- Break plans down into a series of steps that will provide me with opportunities to review the work so far and make changes to either the work or the plan before proceeding.
- For any plan, also write a log file, using markdown, in 'docs'. Name the plan *-log.md (e.g., plan-for-A-log.md)
- In the log, include the date and time, the prompt and the reasoning steps taken.
- When implementing a plan, you may query web sites, write code in the open repository and compile and run software. You may run commands in the repository directories (rg, ls, etc.) as needed to make or carry out the plan. You may activate conda environments if needed to run tests.
- At the end of each step of a plan, update the plan log with a time stamp and any reasoning and then wait for me to review your work.


## Communication

- State assumptions and environment details explicitly, especially configure flags and dependency locations.
- If full validation was not run, say exactly what was run and what was not.
- Do not make up data
- Talk to me directly
- Be concise and to the point
- Be critical of my requests and your own work
- State assumptions and environment details explicitly (python environment, test scope).
- If full validation is not run, say exactly what was run and what was not.

## Change Discipline

- Do not revert unrelated local changes in a dirty worktree.
- Keep edits narrowly scoped to the request.
- If you encounter unexpected repository changes that conflict with the task, stop and ask how to proceed.
- Do not run destructive git commands unless explicitly requested.

## Review Priorities

When asked to review, prioritize:

1. Behavioral regressions in function responses, URL handling, and runtime behavior
2. Increases in memory use
3. Increases in runtime
4. Missing or weak regression coverage

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
