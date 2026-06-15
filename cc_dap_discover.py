#!/usr/bin/env python3
"""
Stage 1 of the OPeNDAP discovery pipeline.

Mine the Common Crawl columnar URL index with DuckDB to find candidate
DAP2/DAP4 endpoints on the .edu and .org TLDs, without crawling anything.

The CC index is public Parquet on S3, partitioned by crawl and subset:
    s3://commoncrawl/cc-index/table/cc-main/warc/crawl=<CRAWL>/subset=warc/*.parquet

We project only the columns we need and filter on:
    * url_host_tld -> 'edu' / 'org'
    * url          -> DAP path & suffix signatures (regex)

Output:
    candidate_urls.txt   -> one URL per line (feed straight into dap_spider.py)
    candidate_urls.csv   -> URL + host + detected mime (for inspection)

Cost note
---------
DuckDB streams the Parquet from S3. Column projection keeps transfer to the
few columns below, but row filtering still scans those columns across every
index file in a crawl (tens of GB per crawl). For repeated or very large
runs, AWS Athena over the same table is usually cheaper/faster because it
prunes server-side. The SQL below pastes into Athena with two changes:
replace read_parquet('s3://.../*.parquet') with the Glue table name, and
drop the DuckDB SET statements. See README.md.

Requirements:
    pip install duckdb
"""

import csv
import sys
import duckdb

# Pick current crawl IDs from https://commoncrawl.org/get-started
# (the "crawl" partition values, e.g. CC-MAIN-2025-08). One crawl is plenty
# to start; add more for higher recall.
CRAWLS = [
    "CC-MAIN-2025-08",
]

# Strong path signals + DAP response suffixes. Lower-cased; dots escaped.
DAP_REGEX = (
    r"(/thredds/(dodsc|catalog)"      # THREDDS OPeNDAP access + catalogs
    r"|/opendap/"                      # Hyrax / generic OPeNDAP root
    r"|/erddap/(grid|table)dap/"       # ERDDAP griddap & tabledap
    r"|/dap/"                          # generic DAP mount (noisier)
    r"|\.(dds|das|dods|dmr|dap|dsr|info)($|\?))"  # DAP2/DAP4 suffixes
)

SETUP_SQL = """
INSTALL httpfs;
LOAD httpfs;
SET s3_region='us-east-1';
-- commoncrawl is a public bucket; DuckDB uses anonymous access when no
-- credentials are present. If you hit a signing error, see README.md.
"""

QUERY_TEMPLATE = """
SELECT url, url_host_name, content_mime_type
FROM read_parquet(
    's3://commoncrawl/cc-index/table/cc-main/warc/crawl={crawl}/subset=warc/*.parquet',
    hive_partitioning = true
)
WHERE url_host_tld IN ('edu', 'org')
  AND fetch_status = 200
  AND regexp_matches(lower(url), '{regex}')
"""


def run():
    con = duckdb.connect()
    con.execute(SETUP_SQL)

    seen = {}  # url -> (host, mime); dedupe across crawls
    for crawl in CRAWLS:
        sql = QUERY_TEMPLATE.format(crawl=crawl, regex=DAP_REGEX)
        print(f"[*] querying {crawl} ...", file=sys.stderr)
        try:
            rows = con.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001 - surface S3/auth issues clearly
            print(f"[!] query failed for {crawl}: {exc}", file=sys.stderr)
            continue
        print(f"[*] {crawl}: {len(rows)} candidate rows", file=sys.stderr)
        for url, host, mime in rows:
            seen.setdefault(url, (host, mime))

    print(f"[*] {len(seen)} unique candidate URLs total", file=sys.stderr)

    with open("candidate_urls.txt", "w", encoding="utf-8") as f:
        for url in sorted(seen):
            f.write(url + "\n")

    with open("candidate_urls.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "host", "content_mime_type"])
        for url in sorted(seen):
            host, mime = seen[url]
            w.writerow([url, host, mime])

    print("[*] wrote candidate_urls.txt and candidate_urls.csv", file=sys.stderr)


if __name__ == "__main__":
    run()
