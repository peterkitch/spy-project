# V8 operational master handoff + export guardrail

**Date:** 2026-05-15
**Scope:** narrow operational-list handoff. Make `master_tickers.txt` equal the operator-curated V8 universe, and add a small guardrail to `registry.export_active()` so future exports cannot silently reintroduce banned tickers. **No pipeline behavior change** (OnePass / ImpactSearch / StackBuilder / TrafficFlow / Confluence pipeline / yfinance / source-refresh / registry validation are NOT run).

## Counts

| Metric | Value |
|---|---|
| V8 parsed count                  | **37,270** |
| Ban-list parsed count            | **36,395** |
| `master_tickers.txt` post-write count | **37,270** |
| V8 set == master set             | ✅ `True` |
| Master set disjoint from banned set | ✅ `True` |
| `00-USD` absent from master      | ✅ |
| `^VIX` absent from master        | ✅ |
| `^SPX` absent from master        | ✅ |

## Source-file integrity (verified pre+post)

| File | SHA-256 | Status |
|---|---|---|
| `global_ticker_library/data/V8_Ticker.txt` | `00f26ce194ea6411b3e8413ac87e1e6f400a329fa2c2dadd9b62d0e83f3334e3` | **unchanged** |
| `global_ticker_library/curation/v8_removed_from_master_banlist.json` | unchanged | **unchanged** |
| `global_ticker_library/data/registry.db` | mtime `Dec 1 12:35` | **untouched** (the DB is never opened by this phase) |

## What was written

### 1. Operational master list (gitignored on disk, NOT tracked in git)

`project/global_ticker_library/data/master_tickers.txt` was overwritten with the parsed V8 universe **in V8 native order** (first-occurrence-kept dedup; not re-sorted). Format: single line, comma-separated, no spaces between tokens, no trailing newline — matches the existing `registry.export_active` write format. The file remains gitignored by `global_ticker_library/data/*.txt`; this phase does not change that.

  - Post-write size: **256,042 bytes**
  - Post-write SHA-256: `d8890b91be2368c271172d505d43b2b8a35726c1bdbaf78065a6ffd22c8a107b`

**Ordering choice.** V8 native order (first-occurrence-kept dedup) is preserved over an alphabetical re-sort. Downstream code in `registry.py:457-458` re-sorts case-insensitively the next time `export_active()` runs (`ORDER BY symbol COLLATE NOCASE`), so any sort variance is transient. Preserving the operator's order keeps the audit diff between V8_Ticker.txt and master_tickers.txt minimum (single-pass dedup + comma-join, no resort).

### 2. Tracked guardrail code

`project/global_ticker_library/gl_config.py` — adds a `BANLIST_FILE` constant pointing at `global_ticker_library/curation/v8_removed_from_master_banlist.json` (relative to the package). Includes a comment block explaining the guardrail's purpose.

`project/global_ticker_library/registry.py`:

  - New private helper `_load_master_export_banlist(banlist_path) -> Set[str]`:
    * `None` / missing file → empty set (backwards-compatible; no exclusions).
    * Valid JSON with `schema_version` starting with `"v8_removed_from_master_banlist"` and a list-typed `banned_removed_tickers` → uppercase set of those symbols.
    * Anything else → `ValueError`. **Fail-loud** so operators do not silently re-export banned symbols on a malformed file.
  - `export_active()` gains a new keyword arg `banlist_path: Optional[Path] = BANLIST_FILE`. After fetching active symbols from SQLite (unchanged query), the export now filters case-insensitively against the loaded ban-list before writing `master_tickers.txt`. The guardrail **never mutates** registry rows — symbols stay `active` in the DB.

### 3. Focused tests

`project/test_scripts/test_registry_export_active_banlist.py` — 14 tests, all `tmp_path`-based with synthetic in-memory-style SQLite registries:

  1. `test_export_active_without_banlist_exports_all_active` — backwards compatibility: when ban-list file is absent, all active rows are exported.
  2. `test_export_active_banlist_none_param_exports_all` — explicit `banlist_path=None` also exports all.
  3. `test_export_active_banlist_filters_banned_active_symbols` — banned-and-active rows are filtered out; non-banned active rows remain; sample banned tickers `00-USD` / `^VIX` / `^SPX` absent from output.
  4. `test_export_active_banlist_case_insensitive_match` — DB-row case (`vix`) and ban-list case (`VIX`) match correctly.
  5. `test_export_active_banlist_does_not_mutate_registry_status` — re-queries the DB after export and verifies every row's `status` is unchanged.
  6. `test_helper_load_master_export_banlist_missing_file_returns_empty` — fail-safe behavior for missing file.
  7. `test_helper_load_master_export_banlist_none_returns_empty` — fail-safe behavior for `None`.
  8. `test_helper_load_master_export_banlist_returns_upper_set` — normalization (`upper()` + `strip()`), drops empty strings.
  9. `test_helper_load_master_export_banlist_rejects_unknown_schema` — unknown `schema_version` raises `ValueError`.
  10. `test_helper_load_master_export_banlist_rejects_missing_list` — missing `banned_removed_tickers` key raises `ValueError`.
  11. `test_helper_load_master_export_banlist_rejects_non_object_root` — non-object JSON root raises `ValueError`.
  12. `test_export_active_with_malformed_banlist_raises` — end-to-end: a malformed ban-list raises BEFORE the master file is written (no partial / banned export possible).
  13. `test_production_banlist_path_loads_when_present` — production-state smoke; skips cleanly when the tracked banlist JSON is not staged (cacheless Codex worktree).
  14. `test_export_active_writes_comma_separated_no_trailing_newline` — pins the existing `master_tickers.txt` format (comma-separated, no trailing newline).

### 4. This evidence doc

## Verification (pinned spyproject2 interpreter)

  - `pytest test_scripts/test_registry_export_active_banlist.py -q` → **14 passed in 0.42s** ✅
  - `py_compile gl_config.py registry.py` → clean ✅
  - `git diff --check` → clean ✅
  - V8_Ticker.txt SHA-256: unchanged ✅
  - Ban-list JSON SHA-256: unchanged ✅
  - registry.db mtime: untouched (`Dec 1 12:35`) ✅
  - master_tickers.txt set == V8 set ✅
  - master_tickers.txt set disjoint from banned set ✅

## What this is NOT

  - **Not** a registry-write phase. `registry.db` is never opened by this commit's code path. The operational master list was overwritten as a normal file write (the DB was not consulted for this handoff because V8 is itself the authoritative source).
  - **Not** an OnePass / ImpactSearch / StackBuilder / TrafficFlow / Confluence pipeline / yfinance / source-refresh / writer invocation. The Phase 6I-56 ImpactSearch workbook runner was not invoked. Production roots (`cache/results`, `cache/status`, `output/research_artifacts`, `output/stackbuilder`, `signal_library/data/stable`, `price_cache/daily`, `output/impactsearch`) untouched.
  - **Not** a one-way commitment. Any future operator decision to re-curate V8 should also update or rescind the ban-list. The ban-list is a review artifact and rescinding any entry is a one-line edit + re-run of `export_active()`.

## Why this is safe

  - The operational master file is **gitignored** (`global_ticker_library/data/*.txt`); the on-disk overwrite is invisible to git and reversible by re-running `export_active()` from the DB (which would write `master_tickers.txt = {db.tickers WHERE status='active'} - banlist`).
  - The guardrail is **opt-in via file presence**: if `v8_removed_from_master_banlist.json` is absent in any future deployment / worktree, `export_active()` behaves exactly as it always did.
  - The guardrail **never** mutates SQLite registry rows. Symbols the operator removes from V8 stay `active` in `registry.db` until a separate explicit registry-mutation phase changes them; the guardrail only filters the exported file. This means: rescinding the ban-list (e.g. deleting the file) immediately restores those tickers to the next `export_active()` output.

## Next operational action

The operational master is now V8. Future scraper / batch / dashboard runs that call `registry.export_active()` will inherit the guardrail automatically; no further configuration is required. If a future curation pass needs to remove additional tickers, append them to `banned_removed_tickers` in the same JSON file (or land a new tracked ban-list JSON and update `BANLIST_FILE` in `gl_config.py`).

No pipeline phase needs to fire as a direct consequence of this handoff. Phase 6I-57 (supervised ImpactSearch workbook generation for SPY, AAPL, JNJ, WMT, HD, MCD) remains the next operational event in the website-launch sprint and is unaffected by this V8 handoff — those 6 tickers are all present in V8.
