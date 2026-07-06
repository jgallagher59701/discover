"""
Opt-in live smoke test (plan Step B4,
docs/plan-for-dap-spider-callback-and-regression-tests.md).

Excluded from the default `pytest` run (see pytest.ini's
`addopts = -m "not live"`). Runs the real DapSpider through Scrapy's
CrawlerProcess against the small, already-reviewed seed list in
tests/fixtures/regression_seeds.txt -- hitting real hosts over the network.

This is NOT a correctness check -- Step B3's offline regression tests
already pin down the expected outcome for every seed in that list. This
test only validates that the parts the offline tests can't reach still
work end-to-end: robots.txt fetching/obeying, autothrottle, retries, and
DapSpider's own request wiring through a real Scrapy engine.

Run by hand, occasionally:

    pytest tests/test_live_smoke.py -m live -v -s
"""
import json
from pathlib import Path

import pytest
from scrapy.crawler import CrawlerProcess

from dap_spider import DapSpider

SEEDS_FILE = Path(__file__).resolve().parent / "fixtures" / "regression_seeds.txt"


@pytest.mark.live
def test_live_crawl_runs_end_to_end(tmp_path):
    # NOTE: this deliberately does NOT assert that any specific seed gets
    # confirmed. An earlier version of this test asserted the Hyrax
    # test.opendap.org seed (regression_seeds.txt's one clean, stable true
    # positive) would always be confirmed -- it failed on the very first
    # real run because that host's robots.txt currently has a blanket
    # "Disallow: /", correctly blocking the crawler. That's not a bug: it's
    # exactly the kind of real-world variability (robots.txt changes, hosts
    # go down, servers get reconfigured) that a live third-party host can
    # introduce at any moment, which is why Step B3's offline, fixture-based
    # tests -- not this one -- are the source of truth for per-seed
    # confirmation behavior. This test only checks that the plumbing those
    # offline tests can't reach (robots.txt fetching/obeying, real request
    # dispatch, retries, feed export) actually works end-to-end.
    output_file = tmp_path / "dap_endpoints.jsonl"

    # DapSpider.custom_settings hardcodes FEEDS to the repo-relative
    # "dap_endpoints.jsonl" -- override via a spider subclass (highest
    # settings precedence) rather than CrawlerProcess(settings=...), so this
    # test can never accidentally write to (or clobber) the real output file
    # regardless of the pytest working directory. Confirmed empirically
    # before writing this test that the subclass override wins and no
    # repo-root file gets created.
    class _SmokeSpider(DapSpider):
        custom_settings = {
            **DapSpider.custom_settings,
            "FEEDS": {str(output_file): {"format": "jsonlines"}},
        }

    process = CrawlerProcess(settings={"LOG_LEVEL": "INFO"})
    # process.crawl() only returns a completion Deferred, and
    # process.crawlers has already been emptied by the time start() returns
    # (crawlers remove themselves once stopped) -- create_crawler() first is
    # what gives a reference that's still valid for reading stats afterward.
    crawler = process.create_crawler(_SmokeSpider)
    process.crawl(crawler, seeds_file=str(SEEDS_FILE))
    process.start()  # blocks until the crawl finishes; real network I/O

    stats = crawler.stats.get_stats()

    assert stats.get("finish_reason") == "finished", (
        f"crawl did not finish cleanly: {stats.get('finish_reason')!r}"
    )
    assert stats.get("downloader/request_count", 0) > 0, (
        "no requests were issued at all"
    )
    assert stats.get("robotstxt/request_count", 0) > 0, (
        "ROBOTSTXT_OBEY isn't actually fetching robots.txt"
    )

    assert output_file.exists(), "crawl produced no output file at all"
    entries = [
        json.loads(line)
        for line in output_file.read_text().splitlines()
        if line.strip()
    ]
    # Reported, not asserted on -- see the note above.
    print(f"\nlive smoke crawl confirmed {len(entries)} endpoint(s):")
    for entry in entries:
        print(f"  {entry['url']} (dap{entry['dap_version']})")
