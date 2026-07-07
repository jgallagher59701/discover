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

import argparse
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.crawler import CrawlerProcess

# Stage-2 output path, referenced both in DapSpider.custom_settings (FEEDS)
# and in main() to truncate it for a fresh (non-resumed) run.
OUTPUT_FEED = "dap_endpoints.jsonl"

# DAP response suffixes we may need to strip from a seed to get the base URL.
# This is linked to the extensions used in the query to the Common Crawl database.
# jhrg 7/3/26
DAP_SUFFIXES = (
    ".dmr.xml", ".dmr", ".dap", ".dsr",          # DAP4
    ".dds", ".das", ".dods", ".info", ".ascii",  # DAP2
    ".html",  # THREDDS/ERDDAP HTML view of a dataset, e.g. .../dodsC/foo.nc.html
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


from urllib.parse import urlsplit, urlunsplit

def to_xml(url: str) -> str:
    """
    Since the spider parses the thredds catalog as XML, we need to 
    turn .../catalog.html URLs into catalog.xml URLs. This code will do
    that under all sorts of conditions and won't mange the http:// part
    of the URL.

    From CLAUDE.
    """
    parts = urlsplit(url)
    path = parts.path
    if path.endswith("/"):
        path = path.rstrip("/") + ".xml"      # catalog/ -> catalog.xml
    elif "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit(".", 1)[0] + ".xml"  # catalog.html -> catalog.xml
    else:
        path = path + ".xml"                   # catalog -> catalog.xml
    return urlunsplit(parts._replace(path=path))


class IdentityEncodingMiddleware:
    """
    Scrapy's HttpCompressionMiddleware only recognizes gzip/deflate/br/zstd;
    any other Content-Encoding value -- including the legitimate HTTP/1.1
    'identity' token (RFC 7231 sec 5.3.4, meaning "no transformation") --
    is logged as an unsupported-encoding WARNING. Some ERDDAP hosts send
    'Content-Encoding: identity' explicitly (see issue #20). Strip that
    no-op token here, before HttpCompressionMiddleware runs, so it never
    sees an encoding it doesn't recognize. jhrg 7/7/26
    """

    def process_response(self, request, response, spider):
        raw = response.headers.getlist("Content-Encoding")
        if not raw:
            return response
        kept = []
        for entry in raw:
            tokens = [t.strip() for t in entry.split(b",")]
            tokens = [t for t in tokens if t.lower() != b"identity"]
            if tokens:
                kept.append(b", ".join(tokens))
        if kept:
            response.headers["Content-Encoding"] = kept
        elif raw:
            del response.headers["Content-Encoding"]
        return response


class DapSpider(scrapy.Spider):
    name = "dap"

    custom_settings = {
        # ---- politeness: behave like a guest, not a scanner ----
        "ROBOTSTXT_OBEY": True,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 30,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0.5,
        "DOWNLOAD_TIMEOUT": 20,
        "RETRY_TIMES": 2,
        "USER_AGENT": (
            "OPeNDAP-Discovery/0.2"
            "(+https://www.opendap.org/; contact support@opendap.org)"
        ),
        # Run just ahead of HttpCompressionMiddleware (priority 590) so it
        # strips the no-op 'identity' encoding before that middleware logs
        # it as unsupported.
        "DOWNLOADER_MIDDLEWARES": {
            IdentityEncodingMiddleware: 595,
        },
        # ---- output ----
        "FEEDS": {OUTPUT_FEED: {"format": "jsonlines"}},
        # LOG_LEVEL is set via --log-level in main() instead of here: spider
        # custom_settings take precedence over settings passed to
        # CrawlerProcess, so hardcoding it here would make --log-level a
        # no-op.
    }

    def __init__(self, seeds_file=None, progress_every=None, resume_from=0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seeds_file = seeds_file
        self.progress_every = progress_every
        self.resume_from = resume_from
        self._deref_count = 0
        # Seed-line bookkeeping for restart/resume (issue #22). Counts seed
        # *URLs* (non-blank, non-comment lines), not raw file lines.
        self._seed_index = 0
        self._last_dispatched_seed = 0

    def _tick_progress(self):
        """Print a '.' every Nth dereference (response or failure), across
        on_dmr/on_dds/parse_thredds_catalog/on_error combined."""
        self._deref_count += 1
        if self.progress_every and self._deref_count % self.progress_every == 0:
            print(".", end="", flush=True)

    def closed(self, reason):
        # trailing newline so a mid-line run of dots doesn't collide with the
        # shell prompt or a subsequent log line; called once when the spider
        # finishes, since dereferences happen asynchronously with no single
        # loop-end point to print it from.
        if self.progress_every:
            print()
        # reason == "finished" for a normal, fully-drained run; a graceful
        # Ctrl-C (single SIGINT) closes the spider with reason == "shutdown"
        # instead -- print a resume hint in that case (issue #22). Printed
        # to stderr so it survives independently of stdout dot output and
        # --log-level.
        if reason != "finished" and self.seeds_file:
            print(
                f"Stopped after seed URL {self._last_dispatched_seed} of "
                f"{self.seeds_file}.\n"
                f"Resume with: python dap_spider.py {self.seeds_file} "
                f"--resume-from {self._last_dispatched_seed}",
                file=sys.stderr,
            )

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
                self._seed_index += 1
                if self._seed_index <= self.resume_from:
                    # Already processed in a prior run (--resume-from); count
                    # it but don't re-dispatch a request for it.
                    continue
                if is_thredds_catalog(url):
                    url = to_xml(url)
                    self.logger.info(f"seed [thredds catalog]: {url}")
                    yield scrapy.Request(
                        url, callback=self.parse_thredds_catalog, errback=self.on_error
                    )
                else:
                    # Added call to strip the query string. jhrg 7/6/26
                    base = strip_dap_suffix(strip_query_string(url))
                    self.logger.info(f"seed [probe]: {url} -> base {base}")
                    for req in self.probe(base):
                        yield req
                self._last_dispatched_seed = self._seed_index

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
        self._tick_progress()
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
        self._tick_progress()
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
        self._tick_progress()
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
        self._tick_progress()
        self.logger.debug("request failed: %s", failure.value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds_file")
    parser.add_argument(
        "-p", "--progress-every",
        type=int,
        default=None,
        help="print a '.' for every Nth URL dereferenced (response or failure)",
    )
    parser.add_argument(
        "-l", "--log-level",
        type=str.upper,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Scrapy log level (default: %(default)s)",
    )
    parser.add_argument(
        "-r", "--resume-from",
        type=int,
        default=0,
        metavar="N",
        help=(
            "number of seed URLs already processed in a prior run; skip "
            "them and append to the existing %s instead of starting fresh"
            % OUTPUT_FEED
        ),
    )
    args = parser.parse_args()
    if not args.resume_from:
        # Fresh run: make the current append-by-default FEEDS behavior
        # explicit instead of accidental by starting from a clean file.
        Path(OUTPUT_FEED).unlink(missing_ok=True)
    process = CrawlerProcess(settings={"LOG_LEVEL": args.log_level})
    process.crawl(
        DapSpider,
        seeds_file=args.seeds_file,
        progress_every=args.progress_every,
        resume_from=args.resume_from,
    )
    process.start()


if __name__ == "__main__":
    main()
