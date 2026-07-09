# Log: quiet DNS lookup failures in `dap_spider.py`

## 2026-07-09

**Prompt:** "Look at issue #24 in the repo (DNS lookup failures cause
exceptions) and plan to fix that."

**Reasoning steps:**

1. `gh issue view 24` showed #24 is actually the merged "Jhrg/22 restart
   feature" PR, not a DNS bug — title/body don't match the description.
   Searched open issues for "DNS" via `gh issue list --search DNS` and found
   the actual report filed as **issue #26**, title "DNS lookup failures
   cause exceptions," body matching the description given (and containing
   the full traceback pasted by the user). Proceeded against #26 and flagged
   the numbering mismatch to the user.
2. Read `dap_spider.py` in full. Noted every request already has
   `errback=self.on_error`, and `on_error` already catches all failures at
   `DEBUG` without raising — confirmed by the existing test
   `test_on_error_does_not_raise`. So the crash-looking traceback in the
   issue isn't coming from our own callbacks.
3. Traced the traceback's own frames: `scrapy/downloadermiddlewares/
   robotstxt.py`, `robot_parser()`, `self.crawler.engine.download_async(
   robotsreq)`. Read that file directly from the installed Scrapy 2.16.0
   (`/Users/jhrg/miniforge3/envs/discover/lib/python3.14/site-packages/
   scrapy/downloadermiddlewares/robotstxt.py`) to confirm: Scrapy's own
   `RobotsTxtMiddleware` fetches `robots.txt` for every new host before
   letting a request through (`ROBOTSTXT_OBEY = True` is set in
   `dap_spider.py`'s `custom_settings`), and unconditionally logs any
   fetch failure at `logger.error(..., exc_info=True)` — this is the
   noisy dump in the issue, not our code. Confirmed `rp is None` after a
   failure is treated as "allowed" (`_robots_error` /
   `process_request_2`), so the crawl isn't blocked or crashed by this —
   it's a pure logging-noise problem, expected to recur constantly across a
   large seed list of dead/typo'd/decommissioned hosts.
4. Confirmed the exception class via
   `grep -rn CannotResolveHostError .../scrapy/` and reading
   `scrapy/exceptions.py:66` and `scrapy/utils/_download_handlers.py:63`:
   Scrapy's HTTP/1.1 handler wraps Twisted's `DNSLookupError` into
   `scrapy.exceptions.CannotResolveHostError`, which is also listed in
   `RETRY_EXCEPTIONS` (`default_settings.py`) — so retries are already
   exhausted by the time `robot_parser` logs it.
5. Looked at `tests/test_dap_spider.py` for precedent: `dap_spider.py`
   already has one prior fix in the same spirit —
   `IdentityEncodingMiddleware` (issue #20), which intercepts a different
   Scrapy-internal noisy log (unsupported `Content-Encoding: identity`
   warning) without touching Scrapy's source, and is tested with `caplog`
   against the *real* Scrapy middleware
   (`test_identity_encoding_middleware_prevents_httpcompression_warning`).
   Used this as the template for both the fix's shape (intercept at our
   layer, don't patch site-packages) and the test's shape (caplog-based,
   exercised against the real logger).
6. Designed the fix as a `logging.Filter` attached to the
   `scrapy.downloadermiddlewares.robotstxt` logger: on a DNS-failure
   exception, demote to a one-line `INFO` and drop the original `ERROR` +
   traceback record; leave every other exception type on that logger
   untouched so genuinely unexpected robots.txt failures still surface.
   Wrote the plan to `docs/plan-for-dap-spider-dns-lookup-failures.md` with
   two review steps (implement filter; add regression tests covering the
   positive DNS case, the defensive unwrapped-`DNSLookupError` case, and a
   negative case proving other exception types are untouched). Did not
   implement yet — stopping here for review per the plan-review workflow.

## 2026-07-09 (implementation)

**Prompt:** "Implement the plan in
docs/plan-for-dap-spider-dns-lookup-failures.md. Note that I modified it and
added a fourth test that will use the live network. Implement all the steps
of the plan in one shot."

**Reasoning steps:**

1. Re-read the plan (user had added Step 2.4 for a live-network test, plus
   noted `pytest.ini` already carries a `live` marker excluded by default
   via `addopts = -m "not live"` — confirmed this by reading `pytest.ini`).
2. Read `dap_spider.py`'s existing imports and the `IdentityEncodingMiddleware`
   / `DapSpider` boundary to place the new filter in the same spot, and
   confirmed via the installed Scrapy 2.16.0 source
   (`robotstxt.py:104-109`) exactly how `logger.error(...)` is called —
   important detail: passing a single dict as the sole `%`-args argument
   makes `logging.LogRecord` store it as `record.args` directly (not
   wrapped in a tuple), so `record.args["request"]` is available and is the
   *original* triggering request (confirming the plan's note that the URL
   shown is not the robots.txt URL itself).
3. Implemented `DnsFailureLogFilter(logging.Filter)` in `dap_spider.py`,
   checking `record.exc_info[1]` against `CannotResolveHostError` /
   `DNSLookupError`, deriving the host from `record.args["request"].url` via
   `urlparse(...).netloc`, logging one `INFO` line through a new
   module-level `logger = logging.getLogger(__name__)`, and returning
   `False` to drop the original record. Registered it at import time via
   `logging.getLogger("scrapy.downloadermiddlewares.robotstxt").addFilter(...)`
   right before `class DapSpider`, matching `IdentityEncodingMiddleware`'s
   placement/spirit.
4. Added the three fabricated-record tests (`CannotResolveHostError`,
   raw `DNSLookupError`, and a `TimeoutError` negative case) to
   `tests/test_dap_spider.py`, using a shared `_log_robotstxt_error` helper
   that does a raise/except round-trip so `exc_info=True` captures a real
   exception, exactly mirroring `robot_parser`'s own log call. Confirmed
   experimentally that a logger-level filter (added via `addFilter` on the
   logger itself, not a handler) runs inside `Logger.handle()` before
   propagation, so `caplog`'s root handler never sees the suppressed record
   — no special caplog wiring needed beyond `caplog.at_level(logging.INFO)`
   to also capture the new INFO line from the `dap_spider` logger.
5. Added the live test (`test_dns_failure_log_filter_silences_real_robotstxt_dns_failure`,
   marked `@pytest.mark.live`): a real `CrawlerProcess` crawl against a
   hostname under the reserved `.invalid` TLD (RFC 2606 — guaranteed to
   never resolve, so this never touches an actual host despite being a
   genuine DNS failure), with `install_root_handler=False` /
   `LOG_ENABLED=False` so Scrapy's own console handler doesn't interfere
   with `caplog`. First attempt failed: the bare test spider had no
   errback, so Scrapy's *own* generic per-request failure logging (a
   differently-sourced, coincidentally similarly-worded "Error downloading
   ..." message from `scrapy.core.scraper`, unrelated to
   `RobotsTxtMiddleware`) also fired and broke the "no 'Error downloading'
   in caplog.text" assertion. Fixed by giving the probe spider's request an
   `errback` (mirroring the fact that every real `dap_spider.py` request
   already sets `errback=self.on_error`, per the plan's root-cause
   section), which isolates the assertion to the robots.txt-specific path.
6. Ran the full suite: `pytest -q` → 104 passed, 2 deselected (the new live
   test plus a pre-existing one in `tests/test_live_smoke.py`). Ran the live
   test explicitly: `pytest -m live -q` → 1 passed. Environment: conda env
   `discover` at `/Users/jhrg/miniforge3/envs/discover` (Scrapy 2.16.0,
   Twisted 26.4.0, Python 3.14.6).

## 2026-07-09 (fix: live suite fails when both live tests run together, issue #28)

**Prompt:** "the live test suite fails. See issue #28 (The live test suite
fails). Develop a fix for this - add info to the plan log
docs/plan-for-dap-spider-dns-lookup-failures-log.md"

**Reasoning steps:**

1. `gh issue view 28` — reporter ran `pytest -v -m live` and got
   `twisted.internet.error.ReactorNotRestartable` on the second of the two
   live tests (`tests/test_live_smoke.py::test_live_crawl_runs_end_to_end`),
   even though each test passes on its own. The traceback pointed at
   `CrawlerProcess.start()` → `reactor.run()` →
   `startRunning()` → `raise error.ReactorNotRestartable()` because
   `self._startedBefore` was already `True`.
2. Root cause: a Twisted reactor can be started (`reactor.run()`) at most
   once per **process**, ever — not per `CrawlerProcess` instance. The live
   test I added in the prior session
   (`test_dns_failure_log_filter_silences_real_robotstxt_dns_failure`) also
   calls `CrawlerProcess(...).start()` directly in-process, so once both
   live tests are selected in the same `pytest` invocation, whichever runs
   second hits an already-started reactor and fails with exactly the error
   in the issue. This is independent of which test runs first — order
   doesn't matter, only count (more than one in-process `reactor.run()` call
   anywhere in the session is fatal to the second one).
3. Considered and rejected: (a) restructuring both live tests to share one
   `CrawlerProcess`/one `process.start()` call — would require coupling two
   otherwise-independent test files through a shared fixture, more
   invasive than the bug warrants; (b) adding `pytest-twisted` to keep a
   long-lived reactor across the session — new dependency, not currently in
   `requirements.txt`/the conda env recipe in CLAUDE.md, avoided without
   asking first. Chose: run *my* live test's crawl in a fresh subprocess,
   since a new process always has a never-started reactor regardless of
   what ran earlier in the parent `pytest` process. This fixes the
   collision without touching the pre-existing `test_live_smoke.py`,
   keeping the change scoped to the test I added (per CLAUDE.md's "keep
   edits narrowly scoped").
4. Implementation choice: `subprocess.run([sys.executable, "-c", script])`
   with a self-contained script string, rather than `multiprocessing
   .Process` with a module-level target function. Rejected
   `multiprocessing` because its default "spawn" start method (confirmed
   via `multiprocessing.get_start_method()` → `'spawn'` on this macOS/conda
   setup) needs to re-import whatever module defines the target function in
   the child, and pytest test modules aren't always importable under a
   stable dotted name from a fresh interpreter (no `__init__.py` under
   `tests/`, so pytest imports this file as a bare top-level module) --
   `subprocess.run` with an inline script sidesteps that entirely: no
   pickling, no module-resolution question, and the child only needs
   `cwd=<repo root>` so `import dap_spider` (a plain top-level module, not a
   package) resolves.
5. Rewrote the test: `_DNS_FAILURE_PROBE_SCRIPT` (module-level string in
   `tests/test_dap_spider.py`) builds the same throwaway spider as before
   (still with `errback=self.on_error`, still targeting the `.invalid` TLD),
   runs it under its own `CrawlerProcess`, and writes everything logged at
   INFO+ to an in-process `io.StringIO` (via a plain `logging.StreamHandler`
   attached to the root logger) which it then prints to stdout. The test
   function runs that script with `subprocess.run(..., capture_output=True,
   text=True, timeout=60)` and asserts on `result.stdout` instead of
   `caplog` (caplog can't observe a child process). Removed the
   now-unused top-level `scrapy` / `CrawlerProcess` imports from
   `tests/test_dap_spider.py` (only the subprocess script needs them now,
   and it imports them itself).
6. Verified against the issue's exact repro: `pytest -v -m live` (both live
   tests selected together) → 2 passed (previously 1 passed, 1 failed with
   `ReactorNotRestartable`). Re-ran with the two test files reversed on the
   command line to confirm order-independence → 2 passed. Re-ran the full
   default suite (`pytest -q`) → 104 passed, 2 deselected, confirming the
   fix didn't disturb anything outside the live tests.
