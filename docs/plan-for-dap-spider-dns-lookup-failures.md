# Plan: quiet DNS lookup failures in `dap_spider.py`

Source: [issue #26](https://github.com/jgallagher59701/discover/issues/26),
"DNS lookup failures cause exceptions." (Note: issue #24 is the merged
"restart feature" PR for issue #22, not this bug — the DNS report is filed
as #26.)

## Root cause

The traceback in the issue is **not** coming from our own request handling.
Every request `dap_spider.py` issues (`probe()`, `on_dmr`'s DAP2 fallback,
`parse_thredds_catalog`'s sub-catalog/dataset requests) already sets
`errback=self.on_error`, and `on_error` (`dap_spider.py:418-424`) already
catches every failure — including DNS failures — without raising, logging
only at `DEBUG` (confirmed by the existing
`test_on_error_does_not_raise` in `tests/test_dap_spider.py:486`).

The noisy traceback instead comes from Scrapy's own
`RobotsTxtMiddleware` (`ROBOTSTXT_OBEY = True`,
`dap_spider.py:186`). Before letting any request through, that middleware
fetches `<scheme>://<netloc>/robots.txt` for the host
(`robotstxt.py:84-116` in the installed Scrapy 2.16.0). That fetch has no
errback of ours — it's an internal `Request` the middleware issues directly
via `self.crawler.engine.download_async(robotsreq)`. When it fails for any
reason other than `IgnoreRequest`, Scrapy logs it itself:

```python
# scrapy/downloadermiddlewares/robotstxt.py:100-110
try:
    resp = await self.crawler.engine.download_async(robotsreq)
    self._parse_robots(resp, netloc)
except Exception as e:
    if not isinstance(e, IgnoreRequest):
        logger.error(
            "Error downloading %(request)s: %(f_exception)s",
            {"request": request, "f_exception": e},
            exc_info=True,
            extra={"spider": self.crawler.spider},
        )
    self._robots_error(e, netloc)
```

This explains everything in the report:
- Logger name in the traceback is `scrapy.downloadermiddlewares.robotstxt`,
  not anything in `dap_spider.py`.
- It logs at `ERROR` with `exc_info=True` unconditionally — a full
  traceback — for *any* robots.txt-fetch failure, DNS or otherwise. Default
  `--log-level` is `WARNING` (`dap_spider.py:439`), so this always prints.
- The URL shown in the message (`.../21.dmr.xml`) is confusingly the
  *original* request that triggered the robots.txt check, not the
  `robots.txt` URL itself — that's just how Scrapy formats this message,
  not a bug we can fix from our side.
- After logging, `_robots_error` sets the cached parser to `None`
  (robotstxt.py:130-137) and `process_request_2` treats `rp is None` as
  "allowed" (robotstxt.py:66-68), so the crawl isn't blocked by this — it's
  purely a logging noise problem, not a crash or a stuck run. Given a large
  seed list this is expected to happen often (dead domains, typos,
  decommissioned subdomains), so on a real run it will dominate the log
  with these tracebacks.
- The exception class is `scrapy.exceptions.CannotResolveHostError`
  (`scrapy/exceptions.py:66`), which is what Scrapy's HTTP/1.1 download
  handler raises after wrapping Twisted's `DNSLookupError`
  (`scrapy/utils/_download_handlers.py:63`). It's also one of the
  `RETRY_EXCEPTIONS` (`default_settings.py:453-458`), so by the time it
  reaches `robot_parser`'s `except`, retries are already exhausted.

We can't edit Scrapy's own source (site-packages, not part of this repo),
so the fix has to intercept this at the logging layer, from `dap_spider.py`.

## Fix

Add a `logging.Filter` targeting the `scrapy.downloadermiddlewares.robotstxt`
logger specifically, analogous in spirit to `IdentityEncodingMiddleware`
(`dap_spider.py:153-178`, added for issue #20) which already intercepts a
different kind of Scrapy-internal noise (an unsupported-encoding warning)
without touching Scrapy's code:

- New class, e.g. `DnsFailureLogFilter(logging.Filter)`, in `dap_spider.py`.
- `filter(self, record)`:
  - If `record.exc_info` is set and `record.exc_info[1]` is an instance of
    `scrapy.exceptions.CannotResolveHostError` (the wrapped/normal case) or
    `twisted.internet.error.DNSLookupError` (defensive, in case some code
    path raises the unwrapped Twisted exception directly): emit a single
    `logger.info("DNS lookup failed for %s", <hostname from record/request>)`
    and return `False` to drop the original `ERROR` + traceback record.
  - Otherwise return `True` unchanged, so genuinely unexpected robots.txt
    failures (a 500, a malformed response, a timeout) still surface at
    `ERROR` as they do today — we only want to quiet the routine, expected
    case named in the issue title.
- Register the filter once at module import time:
  `logging.getLogger("scrapy.downloadermiddlewares.robotstxt").addFilter(DnsFailureLogFilter())`.
  Doing this at import time (rather than in `DapSpider.__init__` or
  `main()`) means it's active both for real runs and for anything that
  imports `dap_spider` in tests, with no dependency on how the spider is
  constructed.

## Step 1 — implement `DnsFailureLogFilter` and register it

- Add the class + module-level `addFilter(...)` call in `dap_spider.py`,
  near `IdentityEncodingMiddleware` (same "quiet Scrapy's internal noise"
  purpose).
- No change to `on_error`, `probe`, or any request/errback wiring — this is
  additive and only touches logging.

## Step 2 — regression tests in `tests/test_dap_spider.py`

Following the existing pattern for `IdentityEncodingMiddleware`
(`test_identity_encoding_middleware_prevents_httpcompression_warning`,
which drives the *real* Scrapy middleware and asserts on `caplog`), add:

1. **Positive case** — a fabricated `logging.getLogger(
   "scrapy.downloadermiddlewares.robotstxt").error(..., exc_info=(...))`
   call carrying a `CannotResolveHostError`, with the filter attached
   (already true at import time): assert the `ERROR` record / traceback
   text does not appear in `caplog.text`, and that an `INFO` record
   mentioning the failing host does.
2. **Same for the raw `twisted.internet.error.DNSLookupError` case**
   (defensive branch), same assertions.
3. **Negative case** — a fabricated `ERROR` log call from the same logger
   with an unrelated exception (e.g. a plain `TimeoutError` or a
   `ConnectionRefusedError`) still comes through unfiltered at `ERROR`,
   proving we're not silently swallowing other robots.txt failure modes.
4. Add a a live-network manual check (a real host with a genuinely
  unresolvable name) as part of the automated suite, even though this is contrary to the "no HTTP requests to remote hosts" tests policy in CLAUDE.md. For this case/issue, make an exception. Trigger this test using the -m live option to pytest.


## Out of scope / not proposed

- Not touching Scrapy's `ROBOTSTXT_OBEY`, `RETRY_EXCEPTIONS`, or download
  handler behavior — this is a logging-noise fix only, per the issue title.

