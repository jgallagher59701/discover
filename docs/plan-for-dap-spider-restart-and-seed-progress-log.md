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
