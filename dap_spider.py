#!/usr/bin/env python3
"""
Stage 2 of the OPeNDAP discovery pipeline.

A self-contained Scrapy spider that takes the candidate URLs from
cc_dap_discover.py (or any seed list) and:

  1. Classifies each seed:
       - THREDDS catalog  -> parse XML, recurse sub-catalogs, build dataset URLs
       - URL with a DAP suffix -> strip to base, probe
       - anything else -> treat as a base, probe
  2. Probes a base by requesting <base>.dmr.xml (DAP4) then <base>.dds (DAP2),
     confirming via response headers AND body signature.
  3. Emits verified endpoints to dap_endpoints.jsonl.

This is the *probing* path: a crawler request is the same mechanism whether
the response is HTML or a DAP metadata document. The callbacks below inspect
headers + non-HTML bodies instead of scraping links.

Run:
    pip install scrapy
    python dap_spider.py candidate_urls.txt

Notes:
    * THREDDS parsing here is pragmatic. For production-grade catalog walking
      (compound services, serviceName references, nested datasets), use
      Unidata's `siphon` or the `thredds_crawler` package.
    * Edit the USER_AGENT contact string before running against real hosts.
"""

import sys
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.crawler import CrawlerProcess

# DAP response suffixes we may need to strip from a seed to get the base URL.
# This is linked to the extensions used in the query to the Common Crawl database.
# jhrg 7/3/26
DAP_SUFFIXES = (
    ".dmr.xml", ".dmr", ".dap", ".dsr",          # DAP4
    ".dds", ".das", ".dods", ".info", ".ascii",  # DAP2
)

THREDDS_NS = {
    "t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


def strip_dap_suffix(url: str) -> str:
    """Return the dataset base URL by removing a trailing DAP suffix, if any."""
    for suf in DAP_SUFFIXES:
        if url.lower().endswith(suf):
            return url[: -len(suf)]
    return url

def strip_query_string(url: str) -> str:
    """Return the dataset base URL by removing an HTML query string, if any."""
    if "?" in url:
        return url[:url.find("?")]
    return url

def is_thredds_catalog(url: str) -> bool:
    p = urlparse(url).path.lower()
    return "/thredds/catalog" in p and (p.endswith(".html") or p.endswith(".xml") or p.endswith("/"))


class DapSpider(scrapy.Spider):
    name = "dap"

    custom_settings = {
        # ---- politeness: behave like a guest, not a scanner ----
        "ROBOTSTXT_OBEY": True,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 30,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.0,
        "DOWNLOAD_TIMEOUT": 20,
        "RETRY_TIMES": 2,
        "USER_AGENT": (
            "OPeNDAP-Discovery/0.1 "
            "(+https://www.opendap.org/; contact support@opendap.org)"
        ),
        # ---- output ----
        "FEEDS": {"dap_endpoints.jsonl": {"format": "jsonlines"}},
        "LOG_LEVEL": "INFO",
    }

    def __init__(self, seeds_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seeds_file = seeds_file

    # ---- seeding & classification -------------------------------------

    async def start(self):
        if not self.seeds_file:
            self.logger.error("no seeds file provided")
            return
        with open(self.seeds_file, encoding="utf-8") as f:
            self.logger.info(f"open seed file {self.seeds_file}")
            for line in f:
                url = line.strip()
                if not url or url.startswith("#"):
                    continue
                if is_thredds_catalog(url):
                    self.logger.info(f"seed [thredds catalog]: {url}")
                    yield scrapy.Request(
                        url, callback=self.parse_thredds_catalog, errback=self.on_error
                    )
                else:
                    base = strip_dap_suffix(url)
                    self.logger.info(f"seed [probe]: {url} -> base {base}")
                    for req in self.probe(base):
                        yield req

    # ---- DAP probing ---------------------------------------------------

    def probe(self, base: str):
        """Try DAP4 first, fall back to DAP2."""
        yield scrapy.Request(
            base + ".dmr.xml", # Changed from .dmr.xml to just .dmr
            callback=self.on_dmr,
            errback=self.on_error,
            cb_kwargs={"base": base},
            dont_filter=True,
        )

    def on_dmr(self, response, base):
        """
        Both Hyrax/DAP4 and TDS/DAP4 include a Content-Description
        header value application/vnd.opendap.dap4.dataset-metadata+xml.

        I decided to test only for "dmrVersion" in the body because 
        other servers might not have read that part of the spec.
        """
        body = response.text[:1000]
        if response.status == 200 and "dmrVersion" in body:
            yield {
                "url": base,
                "dap_version": "4",
                "probe_url": response.url,
                # drop 'xdap' and use Content-Description. jhrg 7/6/26
                "content_description": response.headers.get("Content-Description", b"").decode("latin1"),
                # With scrapy, header lookup is case-insensitive. ERDDSP has this 
                # header as lowercase. jhrg 7/6/26
                "xdods_server": response.headers.get("XDODS-Server", b"").decode("latin1"),
                "server": response.headers.get("Server", b"").decode("latin1"),
            }
            return
        # not DAP4 -> try DAP2
        yield scrapy.Request(
            base + ".dds",
            callback=self.on_dds,
            errback=self.on_error,
            cb_kwargs={"base": base},
            dont_filter=True,
        )

    def on_dds(self, response, base):
        # The body signature is required, not just a supporting signal: some
        # ERDDAP hosts stamp XDODS-Server/Content-Description on ordinary UI
        # pages (index listings, "Make A Graph" forms), not just genuine DDS
        # responses, so header/description alone are not trustworthy enough
        # to confirm on their own. jhrg 7/5/26
        body = response.text.lstrip()[:200]
        if response.status == 200 and body.startswith("Dataset {"):
            yield {
                "url": base,
                "dap_version": "2",
                "probe_url": response.url,
                "content_description": response.headers.get("Content-Description", b"").decode("latin1"),
                "xdods_server": response.headers.get("XDODS-Server", b"").decode("latin1"),
                "server": response.headers.get("Server", b"").decode("latin1"),
            }

    # ---- THREDDS catalog recursion ------------------------------------

    def parse_thredds_catalog(self, response):
        sel = response.selector
        for ns, uri in THREDDS_NS.items():
            sel.register_namespace(ns, uri)

        # 1) follow sub-catalogs (catalogRef hrefs are relative to this catalog)
        for href in sel.xpath("//t:catalogRef/@xlink:href").getall():
            yield response.follow(
                href, callback=self.parse_thredds_catalog, errback=self.on_error
            )

        # 2) find OPeNDAP service base(s) (serviceType is case-insensitive)
        opendap_bases = sel.xpath(
            "//t:service[translate(@serviceType,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
            "='opendap']/@base"
        ).getall()

        # 3) build dataset access URLs from urlPath + service base, then probe
        for url_path in sel.xpath("//t:dataset[@urlPath]/@urlPath").getall():
            for base in opendap_bases or ["/thredds/dodsC/"]:
                access = urljoin(response.url, base.rstrip("/") + "/" + url_path)
                yield from self.probe(access)

    # ---- error handling -----------------------------------------------

    def on_error(self, failure):
        # dead hosts / timeouts / 4xx-5xx: log and move on, never crash the run
        self.logger.debug("request failed: %s", failure.value)


def main():
    if len(sys.argv) < 2:
        print("usage: python dap_spider.py <seeds_file>", file=sys.stderr)
        sys.exit(1)
    process = CrawlerProcess()
    process.crawl(DapSpider, seeds_file=sys.argv[1])
    process.start()


if __name__ == "__main__":
    main()
