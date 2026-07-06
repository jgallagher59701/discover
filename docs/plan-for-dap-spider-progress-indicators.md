# Plan: progress indicators for `dap_spider.py`

## Status

Revision of an already-implemented feature. Steps 1–3 below (schedule-time
dots, counting seed lines in `start()`) were implemented and verified — see
`plan-for-dap-spider-progress-indicators-log.md`. This revision changes where
the dot fires and requires re-implementation + re-verification of Step 2/3
only; Step 1 (constructor option + CLI wiring) is untouched.

## Scope

One opt-in display option for `DapSpider`: print a period (no newline) for
every Nth URL **dereferenced** (an actual HTTP response or failure comes
back), not every Nth seed line scheduled.

**Revised 2026-07-06.** The original plan counted seed lines as they were
read in `start()`. But `start()` only *yields* `scrapy.Request` objects — the
actual fetch happens later, asynchronously, throttled by
`AUTOTHROTTLE`/`DOWNLOAD_DELAY`/`CONCURRENT_REQUESTS_PER_DOMAIN=1`. Counting
seed lines makes the dots print in a tight burst as fast as the seed file can
be iterated (effectively instantaneous), which doesn't track the actual,
network-bound progress of the crawl — the thing a progress indicator is for.

Kept from the original plan, unchanged:
- Opt-in via `progress_every` (constructor) / `--progress-every`/`-p` (CLI).
- Plain `print(".", end="", flush=True)`, not `self.logger` — stays on stdout
  independent of Scrapy's stderr logging and `--log-level`.
- Seed echo still dropped; existing `self.logger.info("seed [...]")` calls in
  `start()` remain the sole mechanism for seed visibility (unaffected by this
  revision).

## Design change: what counts as "dereferenced"

A seed line does not correspond 1:1 with an HTTP request:
- A THREDDS catalog seed can recurse into any number of sub-catalog requests
  via `parse_thredds_catalog`.
- A probe seed produces 1 request (`.dmr.xml`) if DAP4 confirms in `on_dmr`,
  or 2 (`.dmr.xml` then `.dds`) if it falls back to DAP2.
- Catalog datasets discovered during recursion (`parse_thredds_catalog` →
  `probe()`) generate further probe requests that were never literal
  seed-file lines at all.

So "dereferenced" is redefined as: **one Scrapy response or failure actually
returned from the network**, counted at the four points in `dap_spider.py`
where a completed request lands:
- `on_dmr` — response for `<base>.dmr.xml`
- `on_dds` — response for `<base>.dds`
- `parse_thredds_catalog` — response for a catalog URL (top-level or
  recursive)
- `on_error` — a failed request (timeout, connection error, robots.txt
  disallow, etc.)

**Considered and rejected:** hooking Scrapy's `response_received` signal
instead of instrumenting these four methods directly. Rejected because that
signal only fires on success — failed requests still represent a completed
dereference attempt but go through `errback`/`on_error` and never fire
`response_received`. Using the signal would still require instrumenting
`on_error` separately on top of the signal wiring, which is more moving parts
for no simpler outcome than a shared counter helper called directly from the
four existing callbacks.

## Step 1 — constructor option + CLI wiring

**Unchanged, already implemented** (`dap_spider.py:116-119`, `245-266`):
`progress_every` constructor param, `-p`/`--progress-every` CLI flag via
argparse, cast at the CLI boundary. No action needed.

## Step 2 — progress dots on dereference (revised)

- Remove the counter and both `print()` calls currently in `start()`'s seed
  loop (`dap_spider.py:129`, `135-136`, `149-150`) — scheduling a request no
  longer prints anything.
- Add `self._deref_count = 0` in `__init__`, and a small helper, e.g.
  `self._tick_progress()`, that increments it and prints `.` when
  `self.progress_every` is set and the new count is a multiple of it.
- Call `self._tick_progress()` at the top of `on_dmr`, `on_dds`,
  `parse_thredds_catalog`, and `on_error`, before any other logic in each —
  every completed dereference ticks the same counter regardless of which
  callback it came through.
- Trailing newline: since dereferences now happen asynchronously throughout
  the whole crawl with no single loop-end point, move
  `if self.progress_every: print()` out of `start()` and into a
  `closed(self, reason)` override — Scrapy calls this automatically exactly
  once, when the spider finishes (all pending requests drained).

## Step 3 — manual verification (revised)

Same network-free-harness approach as the original verification (see log,
2026-07-06 15:12), but exercising the four callbacks instead of `start()`:
- Confirm scheduling a batch of requests with `--progress-every` set prints
  no dots until responses/failures are fed back through
  `on_dmr`/`on_dds`/`parse_thredds_catalog`/`on_error`.
- Feed N synthetic responses/failures through those four callbacks directly
  (no real network) and confirm one dot per `progress_every`-th call, in a
  mix across all four, to check the shared counter is order-independent.
- Confirm `closed()` emits exactly one trailing newline, and only when
  `--progress-every` was set.
- Confirm `--progress-every` omitted → stdout byte-identical to current
  behavior (regression check, same as original plan).

## Out of scope / follow-ups

- No automated test added for `start()`/callback output — same rationale as
  the original plan (`plan-for-dap-spider-unit-tests.md` scopes spider
  methods out of the pure-function unit-test suite).
- Seed echo as a dedicated, Scrapy-noise-free stdout stream was considered
  and dropped in the original plan; unaffected by this revision.
