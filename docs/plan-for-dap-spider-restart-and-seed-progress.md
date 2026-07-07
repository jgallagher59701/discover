# Plan: restart/resume + seed-count progress for `dap_spider.py`

Source: [issue #22](https://github.com/jgallagher59701/discover/issues/22),
"Add a restart feature."

## Scope

Two related features, both driven by the same underlying bookkeeping (a
counter of which seed line in the seeds file is currently being processed):

1. **Restart/resume.** Let a long-running `dap_spider.py` invocation be
   stopped with Ctrl-C and resumed later without re-probing everything from
   scratch, and without persisting any state file — the only thing carried
   between runs is a seed-line number the user reads off stdout/log and
   passes back in on the next invocation.
2. **Seed-count progress.** Augment the existing dot-per-N-dereferences
   progress indicator (`--progress-every`, added for issue #17) with the
   count of seed URLs processed, since dots alone don't tell the user how far
   through the seed file the crawl actually is.

Issue #22 explicitly ranks #1 as more needed and #2 as "easier ... but less
useful." Both share the same new counter, so #1 is the foundation and #2 is a
small addition on top — implemented in that order below.

## Relevant existing code (`dap_spider.py`)

- `start()` (`dap_spider.py:174`) reads `self.seeds_file` line by line and
  yields requests. This is the only place that knows "which seed line are we
  on."
- `closed(self, reason)` (`dap_spider.py:164`) already fires exactly once
  when the spider finishes, for any reason, and is where the existing
  progress-dot trailing newline is printed. `reason` is `"finished"` for a
  normal, fully-drained run and `"shutdown"` for a graceful Ctrl-C-triggered
  stop (confirmed by reading `scrapy/core/engine.py:236,258` — Scrapy's
  `CrawlerProcess` installs a SIGINT handler that calls
  `engine.stop()` → `close_spider_async(reason="shutdown")`; a second Ctrl-C
  forces an unclean, non-graceful exit that `closed()` will not see).
- `_tick_progress()` / `self._deref_count` (`dap_spider.py:157-162`) is the
  existing dot-cadence counter, ticked from `on_dmr`, `on_dds`,
  `parse_thredds_catalog`, and `on_error`.
- `FEEDS` is hardcoded in class-level `custom_settings`
  (`dap_spider.py:144`) as `{"dap_endpoints.jsonl": {"format": "jsonlines"}}`
  with no `overwrite` key.

## Finding worth flagging before implementing

I checked `scrapy/extensions/feedexport.py` (`FileFeedStorage.__init__`,
line ~173): the local-file feed writer's default write mode is **append**
(`"ab"`) whenever `overwrite` is not set in `FEEDS` — `wb` only if
`overwrite: True` is set explicitly. Since `dap_spider.py`'s `FEEDS` entry
never sets `overwrite`, **every current run of `dap_spider.py` already
appends to `dap_endpoints.jsonl` instead of overwriting it**, even for a
fresh, non-resumed run. That's a latent gotcha independent of this feature
(a user re-running against the same output path without deleting it first
silently accumulates duplicate results) and it happens to be exactly the
append behavior a *resumed* run wants. Rather than leave the fresh-run case
silently relying on this, Step 2 below makes the fresh-vs-resume distinction
explicit instead of leaving today's implicit always-append as the only mode.

## Step 1 — seed-line counter in `start()` (shared foundation)

- Add `self._seed_index = 0` and `self._last_dispatched_seed = 0` in
  `__init__`.
- In `start()`, increment `self._seed_index` for each non-blank,
  non-comment line read (matching the existing `if not url or
  url.startswith("#"): continue` filter) — i.e. count seed *URLs*, not raw
  file lines.
- After a line is classified and its request(s) are yielded (THREDDS
  catalog branch or `probe()` branch), set
  `self._last_dispatched_seed = self._seed_index`.
- This mirrors the "spider would not have to store any state information"
  requirement in the issue: the only bookkeeping is an in-memory counter,
  reset every run.
- Precision note to record in the plan (not a blocker): because
  `AUTOTHROTTLE`/`CONCURRENT_REQUESTS_PER_DOMAIN=1` serializes most of the
  work, `_last_dispatched_seed` tracks closely with what has actually
  *completed* at the moment of interrupt, but a seed whose request(s) were
  just dispatched and are still in flight when Ctrl-C lands will be reported
  as "done" and re-probed on resume. That's a deliberate simplification
  (re-probing one seed is idempotent and cheap) rather than a bug to fix —
  flagged again in "Out of scope" below.

## Step 2 — `--resume-from` CLI flag + fresh-vs-resume FEEDS behavior

- Add `argparse` option `-r`/`--resume-from` (`type=int`, `default=0`):
  "number of seed URLs already processed in a prior run; skip that many and
  append to the existing output file instead of starting fresh."
- Pass it through to the spider as a constructor kwarg (`resume_from`,
  alongside the existing `seeds_file`/`progress_every` kwargs at
  `dap_spider.py:151` and the `process.crawl(...)` call at
  `dap_spider.py:312`).
- In `start()`, skip yielding requests for lines while
  `self._seed_index <= self.resume_from` (still count them, just don't
  dispatch), matching "provide the place where the restart should
  commence" from the issue — `--resume-from N` means "N seeds already done,
  continue at N+1."
- Fresh vs. append, decided in `main()` (not by fighting Scrapy's
  `custom_settings`-wins-over-`CrawlerProcess`-settings precedence, which is
  already called out in the comment above `custom_settings` for
  `LOG_LEVEL`): if `args.resume_from == 0`, truncate/remove
  `dap_endpoints.jsonl` before calling `process.crawl(...)` (equivalent to
  `overwrite: True` for a fresh run, and makes the current implicit
  always-append behavior explicit and intentional instead of accidental).
  If `args.resume_from > 0`, leave the file alone so Scrapy's existing
  append-by-default write mode does the right thing.

## Step 3 — Ctrl-C resume hint in `closed()`

- Extend `closed(self, reason)`: after the existing trailing-newline logic
  for `--progress-every`, if `reason != "finished"` (i.e. this is a Ctrl-C
  or otherwise early stop), print a resume hint to stderr (so it survives
  independently of stdout dot output / `--log-level`), e.g.:

  ```
  Stopped after seed URL 42 of dap_seeds.txt.
  Resume with: python dap_spider.py dap_seeds.txt --resume-from 42
  ```

  using `self._last_dispatched_seed` and `self.seeds_file`.
- This directly implements the issue's "when the spider is stopped (with
  ctrl-c) it should ... print/display the current seed URL number."
- No change needed to guarantee `dap_endpoints.jsonl` is flushed on a single
  Ctrl-C: a graceful `reason="shutdown"` close already runs the normal
  spider-close path, which closes the `FEEDS` exporter/file properly. This
  only covers a *single* Ctrl-C — a second Ctrl-C forces Scrapy's unclean
  exit path and `closed()` will not run; that's an explicit Scrapy behavior
  (a warning already logged by Scrapy itself: "Received SIGINT twice,
  forcing unclean shutdown"), not something this feature attempts to
  override.

## Step 4 — seed-count progress markers (feature 2)

- Reuse `_last_dispatched_seed` from Step 1. In `_tick_progress()`
  (`dap_spider.py:157`), alongside the existing dot-per-`progress_every`
  logic, track the seed index at the time of the previous printed marker
  (`self._last_reported_seed`, init `0`).
- When a dot is about to print and `self._last_dispatched_seed >
  self._last_reported_seed`, print `f"[{self._last_dispatched_seed}]"`
  instead of a bare `.`, and update `self._last_reported_seed`. Otherwise
  print `.` as today. Net effect, e.g. with `--progress-every 5`:

  ```
  .....[12].[13]..........[15]...
  ```

  (exact formatting to be confirmed with you before implementing — see
  "Open question" below). This satisfies "the dots were augmented with the
  number of seed URLs processed" without changing the dot cadence or firing
  a print on every single seed advance (still rate-limited by
  `progress_every`, so it doesn't spam output on a THREDDS-heavy run where
  many dereferences share one seed).
- Deliberately **not** doing a total-seed-count denominator (e.g.
  `[12/850]`) in the default implementation — that needs an extra full pass
  over the seeds file before crawling starts. Noted as an easy follow-up in
  "Out of scope" if you want it.

**Open question for you before I implement Step 4:** do you want the seed
number spliced into the dot stream as sketched above, or would you rather
seed-count progress be a wholly separate, less frequent line (e.g. one line
per K seeds, printed with a newline, decoupled entirely from
`--progress-every`/dereference dots)? The issue text itself says "or
something like that," so I want to confirm the display before writing it.

## Step 5 — tests

Consistent with the existing scoping in
`plan-for-dap-spider-unit-tests.md` (pure helper functions get unit tests;
`start()`/`closed()` async-generator behavior is verified manually, not via
automated Scrapy test harness):

- Extract the resume skip-decision into a small pure function, e.g.
  `should_dispatch_seed(seed_index: int, resume_from: int) -> bool`, and
  unit-test it directly (boundary cases: `resume_from=0` dispatches
  everything, `seed_index == resume_from` is the last skipped one,
  `seed_index == resume_from + 1` is the first dispatched one).
- Unit-test the marker-vs-dot decision in `_tick_progress` the same way if
  it's factored into a small pure helper (e.g.
  `format_progress_tick(deref_count, progress_every, last_dispatched_seed,
  last_reported_seed) -> tuple[str | None, int]`), following the pattern
  already used for `strip_dap_suffix`/`to_xml`/`strip_query_string`.
- Manual verification (documented in the log, same approach as the
  progress-indicators plan): run against a small local seed list, Ctrl-C
  once mid-run, confirm (a) `dap_endpoints.jsonl` contains the results
  gathered so far, (b) the resume hint prints with a plausible seed number,
  (c) re-running with `--resume-from <N>` appends further results rather
  than overwriting, and does not re-request seeds at or below `N`.

## Out of scope / follow-ups

- True mid-seed resumption (resuming inside a THREDDS catalog recursion
  rather than at the next top-level seed line) — the issue explicitly says
  the spider "would not have to store any state information for the
  restart," so re-walking one seed's catalog recursion from the top on
  resume is accepted, not a bug.
- Total-seed-count denominator in the progress marker (`[N/total]`) —
  requires a pre-pass over the seeds file; can be added cheaply later if
  wanted.
- Deduplication of `dap_endpoints.jsonl` across a resume boundary (the last
  dispatched-but-not-yet-completed seed from the previous run may be
  reprobed and re-emitted) — not handled here; downstream consumers of
  `dap_endpoints.jsonl` already need to tolerate duplicates from re-runs
  given the pre-existing append-by-default behavior described above.
- Auto-detecting a previous run's progress (e.g. reading back
  `dap_endpoints.jsonl` to infer `--resume-from`) instead of requiring the
  user to note the number down — issue text explicitly favors printing the
  number and taking it back in as an argument over storing state, so this
  wasn't designed in.
