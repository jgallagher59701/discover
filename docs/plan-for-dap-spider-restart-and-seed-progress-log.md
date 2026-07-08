# Log: restart/resume + seed-count progress for `dap_spider.py`

## 2026-07-07 14:38 BST

**Prompt:** "Take a repo issue 22
(https://github.com/jgallagher59701/discover/issues/22). Write a plan for
the two related features described there."

**Reasoning steps:**

1. Fetched issue #22 via `gh issue view 22 --repo jgallagher59701/discover
   --json title,body,comments`. Title: "Add a restart feature." Body
   describes two features: (1) let `dap_spider.py` be stopped with Ctrl-C
   and resumed later, driven by a printed/displayed seed URL number rather
   than any persisted state; (2) augment the existing progress dots with a
   count of seed URLs processed.
2. Read `dap_spider.py` in full to find the relevant hooks: `start()`
   (seed-file iteration), `closed(self, reason)` (already fires once at
   spider shutdown, currently used for the progress-dot trailing newline),
   `_tick_progress()`/`_deref_count` (existing dot cadence from the issue
   #17 progress-indicator work), and the hardcoded `FEEDS` setting.
3. Checked `docs/plan-for-dap-spider-progress-indicators.md` to match the
   established plan style/scoping in this repo (opt-in via
   `progress_every`, plain `print()` not `self.logger`, pure-function unit
   tests only — spider callbacks verified manually).
4. Needed to know exactly what happens on Ctrl-C under Scrapy's
   `CrawlerProcess`. Read `scrapy/crawler.py` (`_signal_shutdown`,
   `_signal_kill`) and `scrapy/core/engine.py`: confirmed a first Ctrl-C
   triggers a graceful stop that ends up calling
   `close_spider_async(reason="shutdown")` — so `closed()` does fire, with
   `reason == "shutdown"`, distinguishable from the normal `"finished"`
   case. A second Ctrl-C forces Scrapy's unclean exit path, which does not
   run `closed()` — an explicit Scrapy behavior, not something to try to
   override.
5. Checked `scrapy/extensions/feedexport.py`'s `FileFeedStorage.__init__`
   to understand whether resuming needs an explicit "append" config change.
   Found the local-file feed writer already defaults to append (`"ab"`)
   whenever `overwrite` isn't set in `FEEDS` — which `dap_spider.py`'s
   config doesn't set. So *every* current run already appends, not just a
   resumed one. Flagged this as worth making explicit (truncate the output
   file up front for a fresh, non-resumed run) rather than leaving the
   fresh-run case relying on an implicit, easy-to-miss default.
6. Also checked whether `FEEDS`/`overwrite` could be toggled dynamically
   per-run via spider kwargs, since `resume_from` is only known at
   `process.crawl(...)` time. `custom_settings` is a class attribute
   resolved before `__init__` runs, and per the existing comment in
   `dap_spider.py` about `LOG_LEVEL`, spider `custom_settings` wins over
   settings passed into `CrawlerProcess()`. Decided against fighting that
   precedence — the plan instead handles the fresh-vs-resume file mode in
   `main()` by truncating the output file before crawling when not
   resuming, leaving Scrapy's append-by-default write mode untouched
   otherwise.
7. Grepped `tests/test_dap_spider.py` to confirm the existing test-scoping
   convention (pure helper functions get unit tests; `start()`/`closed()`
   async-generator/Scrapy-signal behavior is verified manually) and
   designed Step 5 of the plan to follow the same pattern, extracting a
   pure `should_dispatch_seed`-style helper for the resume skip logic so it
   stays testable without a Scrapy test harness.
8. Wrote
   `docs/plan-for-dap-spider-restart-and-seed-progress.md` as five
   reviewable steps (shared seed counter → CLI/FEEDS wiring → Ctrl-C resume
   hint → progress-dot augmentation → tests), plus an "Out of scope"
   section listing simplifications the issue itself endorses (no true
   mid-seed resumption, no auto-detection of prior progress, accepted
   possible duplicate re-probing of the last in-flight seed across a resume
   boundary).
9. Left one open question in the plan rather than guessing: exact display
   format for the seed-count progress marker (spliced into the dot stream
   vs. a separate periodic line) — the issue's own phrasing ("or something
   like that") signals this wasn't fully specified, so it needs
   confirmation before Step 4 is implemented.
10. Caught and fixed a typo in the plan's own draft example
    (`[12+12/850]`) before finishing, and removed a `[N/total]`-style
    denominator from the example that contradicted the plan's own decision
    (a few lines later) not to implement a total-seed-count denominator by
    default.

**Status:** Plan and log written. No code changed yet. Waiting for review
of the plan (and the open question in Step 4) before implementation.

## 2026-07-08 08:10 BST

**Prompt:** "Implement step one of
@docs/plan-for-dap-spider-restart-and-seed-progress.md", then "Proceed to
step 2", "Proceed to step 3", and (after the user answered the Step 4 open
question with "spliced into dot stream") "Proceed to step 4", then "Proceed
to step 5".

**Reasoning steps (Steps 1-4, implemented as planned):**

1. Step 1: added `self._seed_index`/`self._last_dispatched_seed` counters
   in `__init__`, incremented/set in `start()` for each dispatched seed
   line. Tests green (85/85), no behavior change yet (nothing reads the new
   counters).
2. Step 2: added `OUTPUT_FEED` module constant, `resume_from` constructor
   kwarg, skip-but-count logic in `start()`, `-r`/`--resume-from` CLI flag,
   and `Path(OUTPUT_FEED).unlink(missing_ok=True)` in `main()` for a fresh
   (non-resumed) run. Verified offline (no network) that a 3-line seed file
   with `resume_from=2` dispatches only the 3rd seed.
3. Step 3: extended `closed(self, reason)` to print a resume hint to
   stderr when `reason != "finished"` (a graceful Ctrl-C closes with
   `reason == "shutdown"`, per the plan's Scrapy-source read). Verified by
   calling `closed("shutdown")` vs `closed("finished")` directly and
   confirming the hint only fires for the former.
4. User answered the Step 4 open question: seed marker spliced into the
   dot stream (`[N]` in place of a `.`), not a separate line. Implemented
   by tracking `self._last_reported_seed` and changing `_tick_progress` to
   print `[N]` when the seed watermark has advanced since the last printed
   marker. Verified with a scripted trace matching the exact expected dot/
   marker sequence for a hand-computed seed-advance schedule.

**Step 5 (tests) — found and fixed a real correctness bug during manual
verification, not just added tests as planned:**

5. Extracted `should_dispatch_seed` and `format_progress_tick` as pure
   functions (already partially done inline in Steps 2/4; formalized here)
   and added unit tests for both, plus integration tests for `start()`'s
   resume-skip behavior, following the existing pure-function-tests +
   direct-`start()`-driving convention already used in
   `tests/test_dap_spider.py`.
6. Per CLAUDE.md's verify guidance, did not stop at unit tests — ran
   `dap_spider.py` for real. First attempt: a seed list pointing at a
   closed local port (127.0.0.1:9) to fail fast without hitting real hosts.
   This instead hit an unrelated Scrapy robots.txt retry loop (connection-
   refused on `/robots.txt` gets retried indefinitely by
   `RobotsTxtMiddleware` in this Scrapy version) and had to be killed.
   Switched to a local `http.server` instance (in the scratchpad directory,
   not the repo) that serves `robots.txt` and 404s every probe, to get
   clean, fast, host-safe manual runs.
7. First real run (30 seeds, artificial 0.4s per-request server delay) and
   a single SIGINT after 3 seconds: the resume hint printed
   `--resume-from 30` — the *entire* seed file — after only ~3 seconds,
   when the throttled network side could have completed at most 2-3 seeds.
   Root cause: `_last_dispatched_seed` was updated in `start()` at
   *dispatch* time (when a request is yielded), but Scrapy's engine drains
   the `start()` async generator to populate its scheduler far ahead of
   actual throttled downloads — `DOWNLOAD_DELAY`/`AUTOTHROTTLE`/
   `CONCURRENT_REQUESTS_PER_DOMAIN` only throttle the downloads themselves,
   not how far ahead scheduling races. This directly contradicted the
   assumption written into the plan's Step 1 ("_last_dispatched_seed
   tracks closely with what has actually completed") and reproduces,
   for the seed-count marker, exactly the mistake the *existing*
   `plan-for-dap-spider-progress-indicators.md` had already found and
   fixed for the plain dot cadence (see that plan's "Design change"
   section) — should have cross-checked against that precedent before
   calling Step 1 done, not after.
8. Stopped and reported this to the user rather than patching silently,
   given it undermines the core "more needed" feature from issue #22.
   User asked to rework Steps 1/3 to track completion instead of dispatch.
9. Redesigned the bookkeeping around per-seed pending-request reference
   counting: `_begin_seed_request`/`_end_seed_request` instance methods
   backed by a new pure function `advance_seed_watermark(pending_by_seed,
   resolved_seeds, last_completed_seed, seed_index)` that only advances a
   *contiguous* low-water mark (the largest N such that seeds 1..N have
   zero outstanding requests) — chosen over a naive "max completed seed"
   because seeds can resolve out of order across concurrent domains, and a
   naive max would let `--resume-from` skip past a seed that raced behind
   a faster one. Threaded `seed_index` through `cb_kwargs` into
   `probe()`/`on_dmr`/`on_dds`/`parse_thredds_catalog`/`on_error` (the
   latter via `failure.request.cb_kwargs`, since errbacks don't receive
   `cb_kwargs` directly) so every completion path — confirmed match, DAP4→
   DAP2 fallback, catalog recursion, or request failure — calls
   `_end_seed_request` exactly once per request it resolves. Renamed
   `_last_dispatched_seed` to `_last_completed_seed` throughout (removed
   dispatch-time tracking entirely).
10. Updated all existing tests whose callback signatures changed
    (`on_dmr`/`on_dds`/`parse_thredds_catalog`/`probe` all gained a
    `seed_index` parameter; `on_error`'s test mock needed a
    `request.cb_kwargs` attribute) and replaced the two resume-integration
    tests' now-nonsensical `_last_dispatched_seed` assertions with
    `_pending_by_seed` assertions (accurate for what `start()` alone, with
    no responses ever completing, can actually prove). Added dedicated
    unit tests for `advance_seed_watermark` covering: single-request
    resolution, multi-request seeds (DAP4→DAP2 fallback) not resolving
    until all pending requests clear, out-of-order completion *not*
    advancing the watermark past a gap, and the gap filling in later to
    advance past both. 101/101 tests passing.
11. Re-ran the same local-server manual test against the fixed code:
    after 3 seconds the printed watermark was `[9]` (not `[30]`), and after
    the full graceful shutdown the resume hint read `--resume-from 26`
    (of 30) — a plausible, throttling-consistent number instead of an
    instant jump to the end of the file. Confirmed `--resume-from 26`
    (via `--log-level INFO`) dispatched only seeds 27-30, matching the
    corrected watermark exactly.
12. **Separate finding, out of scope for issue #22, reported to the user
    but not fixed:** the manual verification incidentally proved a
    pre-existing, unrelated bug in `on_dmr`'s DAP4→DAP2 fallback. Scrapy's
    `HttpErrorMiddleware` (enabled by default, `HTTPERROR_ALLOWED_CODES`
    unset in `custom_settings`) intercepts any non-2xx response *before*
    the spider callback runs and routes it to the request's `errback`
    (`on_error`) instead — so whenever a real `.dmr.xml` probe returns a
    non-2xx status (404, 400, etc.), `on_dmr` never runs at all, and its
    "not DAP4 -> try DAP2" fallback is dead code for that seed; the `.dds`
    check never happens. Confirmed via `--log-level DEBUG` stats
    (`scheduler/enqueued: 4` for 4 seeds, meaning zero fallback requests
    were ever scheduled) and the log line `on_error`'s own
    `self.logger.debug` emits: "request failed: Ignoring non-200
    response". The existing regression tests
    (`test_on_dmr_real_captures_fall_through_to_dds`, parametrized over
    captures including `savannah.gnu.org` at 404 and both
    `pae-paha.pacioos.hawaii.edu` captures at 400) call `on_dmr` directly
    and pass, masking this: they never go through Scrapy's spider
    middleware chain, so they don't exercise the interception. In the
    specific captures on hand this doesn't appear to have caused a missed
    real endpoint (their `.dds` captures are already in the
    "yields nothing" regression set), but the underlying gap is real and
    would silently miss a genuine DAP2 endpoint on any host whose
    `.dmr.xml` 404s/400s while its `.dds` would have confirmed. Not
    touched — orthogonal to issue #22's scope, flagged for a separate
    decision.
13. Cleaned up: killed the local test server, confirmed `git status`
    shows only the two intended files modified (`dap_spider.py`,
    `tests/test_dap_spider.py`), no repo-root `dap_endpoints.jsonl` or
    other tracked file touched by manual testing (all manual runs used a
    scratchpad directory, not the repo root).

**Status:** Steps 1-5 of the plan implemented and verified, including a
mid-flight redesign of the seed-tracking mechanism (dispatch-based →
completion-based) after manual verification caught the dispatch-based
version reporting an incorrect resume point. 101/101 tests passing. A
second, unrelated pre-existing bug (DAP4→DAP2 fallback unreachable for
non-2xx `.dmr.xml` responses) was found and reported separately, not
fixed. Awaiting user decision on whether/when to address that second bug.
