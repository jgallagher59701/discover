#!/usr/bin/env python3
"""
One-time capture script for regression test fixtures (plan Step B2,
docs/plan-for-dap-spider-callback-and-regression-tests.md).

Reads tests/fixtures/regression_seeds.txt, replicates the exact requests
DapSpider.start()/probe() would issue for each seed -- WITHOUT running
Scrapy or a live crawl -- and writes each response's status/headers/body to
tests/fixtures/regression/<slug>.json.

NOT part of the automated test suite and never invoked by pytest or CI.
Manual, one-time (or occasional, if the seed list changes) capture against
real hosts, run by hand:

    python tests/tools/capture_fixtures.py

Respects robots.txt and inserts a delay between requests, mirroring
dap_spider.py's own ROBOTSTXT_OBEY / DOWNLOAD_DELAY settings, even though
this script talks to hosts directly via urllib rather than through Scrapy.
"""
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dap_spider import is_thredds_catalog, strip_dap_suffix  # noqa: E402

SEEDS_FILE = Path(__file__).resolve().parent.parent / "fixtures" / "regression_seeds.txt"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "regression"

USER_AGENT = (
    "OPeNDAP-Discovery/0.1 (+https://www.opendap.org/; contact support@opendap.org)"
)
DOWNLOAD_DELAY = 2.0
TIMEOUT = 20

# on_dmr/on_dds only ever inspect a small body prefix (response.text[:1000]
# and response.text.lstrip()[:200] respectively), so truncating stored dmr/dds
# fixtures well past that is lossless for replay purposes -- several real
# ERDDAP responses ran 96-184KB (full HTML/JSON pages), which would otherwise
# bloat the repo for no fidelity gain. NOT applied to "catalog" fixtures:
# parse_thredds_catalog parses the entire document via its XML/HTML selector,
# so truncating would risk a malformed fragment misrepresenting the real page.
TRUNCATE_BODY_AT = 4096

_robots_cache = {}


def _robots_allowed(url):
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # no robots.txt / unreachable -> treat as allowed
        _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    return rp is None or rp.can_fetch(USER_AGENT, url)


def slugify(url):
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    host = urlparse(url).netloc.replace(":", "_")
    return f"{host}-{digest}"


def fetch(url):
    if not _robots_allowed(url):
        print(f"  SKIP (robots.txt disallows): {url}")
        return None
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            return {
                "url": url,
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                "body": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as e:
        body = e.read()
        return {
            "url": url,
            "status": e.code,
            "headers": dict(e.headers.items()) if e.headers else {},
            "body": body.decode("utf-8", errors="replace"),
        }
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return None


def requests_for_seed(url):
    """Mirror DapSpider.start()'s classification: which URL(s) would the
    spider actually request for this seed?"""
    if is_thredds_catalog(url):
        return [("catalog", url)]
    base = strip_dap_suffix(url)
    return [("dmr", base + ".dmr.xml"), ("dds", base + ".dds")]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    seeds = []
    with open(SEEDS_FILE, encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            seeds.append(url)

    print(f"{len(seeds)} seeds to capture from {SEEDS_FILE}")
    for seed in seeds:
        for kind, url in requests_for_seed(seed):
            print(f"fetching [{kind}] {url}")
            result = fetch(url)
            time.sleep(DOWNLOAD_DELAY)
            if result is None:
                continue
            slug = f"{slugify(seed)}-{kind}"
            out_path = OUTPUT_DIR / f"{slug}.json"
            result["seed"] = seed
            result["kind"] = kind
            if kind != "catalog" and len(result["body"]) > TRUNCATE_BODY_AT:
                result["body_full_length"] = len(result["body"])
                result["body"] = result["body"][:TRUNCATE_BODY_AT]
                result["body_truncated"] = True
            else:
                result["body_truncated"] = False
            out_path.write_text(json.dumps(result, indent=2, sort_keys=True))
            print(
                f"  wrote {out_path.name} "
                f"(status={result['status']}, {len(result['body'])} bytes)"
            )


if __name__ == "__main__":
    main()
