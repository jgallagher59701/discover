# Plan: progress indicators for `dap_spider.py`

## Scope

One opt-in display option for `DapSpider`, driven from the seed-file loop in
`async def start()`:

1. **Progress dots** — print a period (no newline) for every Nth seed URL
   read from the seeds file.

This is for interactive visibility during a run and is independent of
Scrapy's own `LOG_LEVEL`/logger — it uses plain `print()` so it behaves the
same whether Scrapy logging is verbose, quiet, or redirected. Confirmed via
Scrapy's own defaults (`LOG_STDOUT=False`) that this keeps the dots on stdout
while Scrapy's own logging stays on stderr, so the two don't interleave.

**Seed echo dropped from this plan.** `custom_settings["LOG_LEVEL"] = "INFO"`
plus the existing `self.logger.info(f"seed [thredds catalog]: {url}")` /
`self.logger.info(f"seed [probe]: {url} -> base {base}")` calls in `start()`
already print every seed URL, today, with no code change. Decision: rely on
that existing INFO-level logging for seed visibility instead of adding a
second, parallel `print()`-based mechanism. Tradeoff worth remembering if
this resurfaces later: those log lines are mixed in with every other Scrapy
INFO record (crawled responses, autothrottle, robots.txt fetches, stats
dumps) and fully formatted (timestamp/logger-name/level prefix), not a bare
URL — acceptable per your call, but not equivalent to a clean seeds-only
stream.

Not in scope: changing existing `self.logger.info(...)` calls in `start()`,
changing `custom_settings["LOG_LEVEL"]`, or adding progress output anywhere
inside `probe()`/`on_dmr`/`on_dds`.

## Step 1 — constructor option + CLI wiring

- Add one `__init__` param to `DapSpider`:
  - `progress_every: int | None = None` — `None`/`0` disables; otherwise
    print `.` for every Nth seed line.
- Scrapy spider args arrive as strings when passed via `-a`; since `main()`
  is the only current entry point and constructs `DapSpider` directly via
  `process.crawl(...)`, cast in `main()` before passing through, not in
  `__init__`.
- `main()` currently does bare positional `sys.argv[1]` parsing. Decided:
  switch to `argparse`, with one positional (`seeds_file`) and one optional
  flag (`--progress-every N`, `type=int`, default `None`). Replaces the
  current `len(sys.argv) < 2` / manual usage-message check with argparse's
  built-in handling.

## Step 2 — progress dots in `start()`

- Add a counter incremented once per non-blank, non-comment line (i.e., the
  same lines that currently reach the `is_thredds_catalog`/`probe` branch —
  skipped/blank/comment lines don't count).
- When `progress_every` is set and `counter % progress_every == 0`, do
  `print(".", end="", flush=True)`.
- Print a trailing `print()` (bare newline) once the seed-file loop ends, so
  a mid-line run of dots doesn't collide with the shell prompt or a
  subsequent log line. Flag `closed()` (Scrapy's spider-closed callback) as
  the alternate place to do this if the loop-end isn't reliably reached
  (e.g., exception mid-loop) — plan to add a minimal `closed()` override only
  if needed.

## Step 3 — manual verification

Per CLAUDE.md, no test suite covers `start()` today (see
`plan-for-dap-spider-unit-tests.md`, which explicitly scoped the spider
methods out). Verification plan:
- Run `python dap_spider.py tests/fixtures/regression_seeds.txt
  --progress-every 5` and confirm one dot per 5 seed lines, trailing newline
  present, existing logger output unaffected.
- Run with `--progress-every` omitted and confirm output is byte-identical
  to current behavior (no dots) — this is the regression check.
- Confirm existing `self.logger.info("seed [...]")` lines are still present
  and unchanged at `LOG_LEVEL=INFO`, since that's now the sole mechanism for
  seed visibility.

## Out of scope / follow-ups

- No automated test added for `start()` output — `start()` is an async
  generator driven by the Scrapy engine and isn't covered by the existing
  pure-function unit tests; adding fixtures/mocks for it would be a separate,
  larger effort (see `plan-for-dap-spider-unit-tests.md`'s "Out of scope").
- Seed echo as a dedicated, Scrapy-noise-free stdout stream (rather than
  mixed into INFO-level logging) was considered and dropped — revisit if the
  existing INFO logging turns out to be too noisy in practice.
