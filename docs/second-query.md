How about both?

Response:

Both it is. I'll give you a two-stage pipeline: a Common Crawl miner (DuckDB) that produces a candidate URL list, and a Scrapy spider that probes/verifies those candidates and recurses THREDDS catalogs. Let me write them out.Let me syntax-check both before handing them over.Here's the pipeline. Both files compile cleanly; I couldn't run them end-to-end here since they need public S3 and arbitrary outbound HTTP, but the logic and dialects are sound.

**`cc_dap_discover.py`** queries the Common Crawl Parquet index with DuckDB, filters `.edu`/`.org` rows whose URLs match DAP path/suffix signatures, and writes a deduplicated `candidate_urls.txt` to feed the spider. Set `CRAWLS` to a current crawl ID first.

**`dap_spider.py`** takes those seeds, classifies each (THREDDS catalog vs. DAP-suffixed URL vs. bare base), then probes `.dmr.xml`→`.dds` and confirms hits via both headers (`XDAP`/`XDODS-Server`/`Content-Description`) and body signatures. THREDDS catalogs get walked recursively so one catalog yields many verified endpoints. Output is `dap_endpoints.jsonl`.

**`README.md`** wires the two stages together and includes the Athena variant of the query (cheaper at scale because it prunes server-side on the `crawl`/`subset` partitions) plus the common S3-anonymous-access gotcha.

Two things to change before a real run: put a genuine contact address in the spider's `USER_AGENT`, and pick live crawl IDs from commoncrawl.org/get-started. The politeness defaults are deliberately conservative — keep them that way when probing across institutions, since rapid sequential probes to many hosts is exactly the pattern campus IDS flags.

One natural extension if you want it: a small reconciliation step that diffs your `dap_endpoints.jsonl` against known registries (ESGF, ERDDAP server lists, NOAA/NASA THREDDS) so you can see which endpoints are genuinely new versus already cataloged. Happy to add that, or to swap the pragmatic THREDDS parser for a `siphon`-based one.
