# OPeNDAP (DAP2/DAP4) endpoint discovery pipeline

Two stages: cheaply discover candidates from Common Crawl, then verify and
expand them with a polite probing crawler.

```
cc_dap_discover.py  ──►  candidate_urls.txt  ──►  dap_spider.py  ──►  dap_endpoints.jsonl
   (DuckDB / S3)                                    (Scrapy)
```

## Stage 1 — mine Common Crawl

```bash
pip install duckdb
python cc_dap_discover.py
```

Edit `CRAWLS` to current crawl IDs (from https://commoncrawl.org/get-started),
e.g. `CC-MAIN-2025-08`. One crawl is enough to start; add more for recall.

Outputs `candidate_urls.txt` (seeds) and `candidate_urls.csv` (for inspection).

The query keeps `.edu`/`.org` rows whose URL matches DAP path/suffix signatures
(`/thredds/dodsC/`, `/thredds/catalog`, `/opendap/`, `/erddap/{grid,table}dap/`,
and `.dds .das .dods .dmr .dmr.xml .dap .dsr .info`). High recall, decent
precision — the spider supplies the precision.

## Stage 2 — probe & verify

```bash
pip install scrapy
python dap_spider.py candidate_urls.txt
```

For each seed the spider strips any DAP suffix to a base URL, requests
`<base>.dmr.xml` (DAP4) then `<base>.dds` (DAP2), and confirms via both HTTP
headers (`XDAP`, `XDODS-Server`, `Content-Description: dods-*`) and the body
signature (`DAP/4.0`/`dapVersion`, or a body starting `Dataset {`). THREDDS
catalogs are parsed recursively — sub-catalogs are followed and dataset
`urlPath`s are combined with the OPeNDAP service base into access URLs, which
are then probed. Verified hits land in `dap_endpoints.jsonl`.

**Before running against real hosts:** set a real contact in `USER_AGENT`.
The politeness defaults (robots.txt obeyed, 1 request/domain, ~2s delay,
autothrottle) keep the crawler from looking like a scanner to institutional
IDS. Loosen them only for hosts you operate or have permission to hit hard.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

Unit tests for the pure helper functions (`strip_dap_suffix`,
`strip_query_string`, `is_thredds_catalog`) and for `DapSpider`'s callbacks
(`probe`, `on_dmr`, `on_dds`, `parse_thredds_catalog`, `start`), run against
synthetic `scrapy.http.Response` objects — no network access, runs in well
under a second. There's no live-crawl test suite; see
`docs/plan-for-dap-spider-callback-and-regression-tests.md` for the plan to
add regression coverage from real crawl data.

## Athena variant (Stage 1, server-side, cheaper at scale)

The same logic runs on the Glue-cataloged CC index. Replace the
`read_parquet('s3://...')` source with the table name and drop the DuckDB
`SET`/`INSTALL` lines:

```sql
SELECT url, url_host_name, content_mime_type
FROM "ccindex"."ccindex"
WHERE crawl = 'CC-MAIN-2025-08'
  AND subset = 'warc'
  AND url_host_tld IN ('edu','org')
  AND fetch_status = 200
  AND regexp_like(lower(url),
      '(/thredds/(dodsc|catalog)|/opendap/|/erddap/(grid|table)dap/|/dap/|\.(dds|das|dods|dmr|dap|dsr|info)($|\?))');
```

Always include `crawl` and `subset` in the WHERE clause — they are the
partition keys, and filtering on them is what keeps the scan (and cost) small.

## Troubleshooting

- **DuckDB S3 signing error:** the bucket is public; DuckDB should use
  anonymous access with no credentials. If it tries to sign, create an
  anonymous secret: `CREATE SECRET (TYPE s3, PROVIDER credential_chain);`
  or run with `AWS_EC2_METADATA_DISABLED=true` and no AWS env vars set.
- **Slow Stage 1:** scanning a whole crawl over the network is the bottleneck.
  Use Athena, or restrict to fewer crawls.
- **Noisy `.dds`/`.info` hits:** `.dds` also means DirectDraw Surface images;
  the Stage-2 header/body check filters these out, so don't over-tune Stage 1.
- **THREDDS catalogs with compound services:** swap the pragmatic parser for
  Unidata `siphon` (`TDSCatalog`) or `thredds_crawler` for full fidelity.
