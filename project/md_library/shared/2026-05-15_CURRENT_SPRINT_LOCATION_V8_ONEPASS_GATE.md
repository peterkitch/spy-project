# Current sprint location — V8 OnePass gate

**Date:** 2026-05-15
**Scope:** sprint-position handoff doc. Read this first in any fresh terminal that resumes work on this repo.

> **Pin this:** the sprint is **upstream** at a V8 OnePass gate. It is **not** at "Phase 6I-57 supervised ImpactSearch workbook generation." Earlier sprint narrative treated 6I-57 as the immediate next action; the operator pivoted to V8 universe hygiene first. **Do not resume downstream until the audit below lands.**

## TL;DR

  - `origin/main` is at `0b1d37a` — *V8 operational master handoff + export_active ban-list guardrail*.
  - The operator-curated **V8 ticker universe (37,270 tickers)** is now the operational master. A **ban-list of 36,395 tickers** (master − V8) is tracked. `registry.export_active()` filters the ban-list out of the operational `master_tickers.txt` on write.
  - The operator manually **deleted the prior OnePass workbook** (copying it to Desktop first) and **started or is starting a fresh manual V8 OnePass run** through the Dash UI.
  - **The immediate gate is that fresh OnePass run + a read-only audit of its output.** Every downstream phase (ImpactSearch workbook → StackBuilder → TrafficFlow K → MTF bridge → Confluence MTF → website) is paused behind this gate.

## What is true on `origin/main`

  - **HEAD:** `0b1d37a776a0fb62bf655125508b0f15dab38b21`.
  - **Tracked V8 universe:** `project/global_ticker_library/data/V8_Ticker.txt` — 37,270 unique tickers, SHA-256 `00f26ce194ea6411b3e8413ac87e1e6f400a329fa2c2dadd9b62d0e83f3334e3`.
  - **Tracked ban-list:** `project/global_ticker_library/curation/v8_removed_from_master_banlist.json` — 36,395 tickers absent from V8, schema `v8_removed_from_master_banlist_v1`.
  - **Guardrail:** `project/global_ticker_library/registry.py` — `_load_master_export_banlist()` + `export_active(..., banlist_path=BANLIST_FILE)`. Missing ban-list = no exclusions (backwards-compat). Malformed ban-list = `ValueError` (fail-loud). Never mutates registry rows; filters the exported file only.
  - **14 focused tests** at `project/test_scripts/test_registry_export_active_banlist.py` (`tmp_path` only, no real-data dependency). Cover missing-file / `None` / malformed / case-insensitive / no-DB-mutation / format-pinning behavior.

## What is true locally (not in git)

  - `project/global_ticker_library/data/master_tickers.txt` was overwritten with V8 in V8 native order (comma-only separator, no trailing newline; matches `registry.export_active`'s existing write format). Verified: parses to 37,270 unique uppercase tickers, set-equal to V8, disjoint from the ban-list, sample banned tickers absent (`00-USD`, `^VIX`, `^SPX`), all 6 prior pilot tickers present (`SPY`, `AAPL`, `JNJ`, `WMT`, `HD`, `MCD`). The file remains **gitignored** by `project/.gitignore:83 global_ticker_library/data/*.txt`.
  - `V8_Ticker.txt` and `master_tickers.txt` are **set-equal** but **not byte-identical**: V8 uses `, ` (comma + space), master uses `,` (comma-only). Expected.
  - `registry.db` is **untouched** (mtime `Dec 1 12:35`; never opened by any V8 phase). Operator-removed tickers remain `active` in the DB; the guardrail filters them only at export time. Rescinding the ban-list (deleting the JSON) would immediately restore those tickers on the next `export_active()` call.

## What the operator did manually

  - Copied the prior `output/onepass/onepass.xlsx` to Desktop.
  - **Deleted** `output/onepass/onepass.xlsx` AND `output/onepass/onepass.xlsx.manifest.json`.
  - Started (or is starting) a **fresh V8 OnePass run** via the OnePass Dash UI with the V8 universe pasted/uploaded into the UI.
  - Read-only verification (2026-05-15) confirmed `output/onepass/` is empty.

## Why OnePass needs the universe pasted (not auto-loaded)

OnePass does **not** auto-load `master_tickers.txt` as its processing universe. The active OnePass code consumes `shared_symbols.py` only for **symbol resolution / alias mapping**, not for batch enumeration. The OnePass Dash UI is the operator-facing entry point and accepts tickers via paste/upload. A non-Dash CLI driver analogous to `impactsearch_workbook_runner.py` does **not** exist for OnePass today; if it ever does, that is a separately authorized future phase, not in scope right now.

## Immediate next gate — read-only OnePass audit

**Step 1 — determine whether the fresh OnePass run completed.** Do not invoke OnePass yourself. Do not assume completion.

Probe:

  - `output/onepass/onepass.xlsx` — exists? size? mtime?
  - `output/onepass/onepass.xlsx.manifest.json` — exists?
  - Any `logs/` artifacts dated after the operator's manual start?

If the workbook is absent or its mtime predates the manual start, the run is **still in progress or not yet started**. Wait or ask the operator. **Stop.**

**Step 2 — if (and only if) the run completed, audit the output with the pinned interpreter** (`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

  - Open the workbook (`pandas.read_excel`).
  - **Row count.**
  - **Unique ticker count.** (Counting `Primary Ticker` uppercased + stripped.)
  - **First / last 10 rows** for shape sanity.
  - **`set(rows.Primary_Ticker.upper()) ⊆ V8_set`** — should be `True`.
  - **`set(rows.Primary_Ticker.upper()).isdisjoint(banlist_set)`** — should be `True` (proves the V8 paste matched the tracked V8 file).
  - **Column-schema deviation** from `ALL_COLS` at `onepass.py:2113-2119`.
  - **Sidecar verification:** read `onepass.xlsx.manifest.json`. Confirm `producer_engine == "onepass"`, `engine_version`, and that the manifest hash matches the workbook bytes (`provenance_manifest.load_verified_xlsx_artifact` is the approved load path).
  - **Failure / skipped ticker summary** if surfaced. Likely sources:
      * Any execution-log file under `logs/` produced by the OnePass run.
      * Progress-tracker JSON under `cache/status/` (if applicable).
      * Stdout / Dash callback `recent_errors` if captured.
  - **Group failures by error code.** Specifically watch for `[ONEPASS:no_data]` lines like `[ONEPASS:no_data] 000075.KS: yfinance returned empty data after N attempts`. Group by error code, count occurrences, show first ~10 example tickers per code.

**Step 3 — report the audit to the operator.** Then stop. Do **not** continue to ImpactSearch / StackBuilder / TrafficFlow / Confluence pipeline / website export without explicit operator authorization for the next phase.

## Known observation during the V8 OnePass run

Some tickers will fail with `[ONEPASS:no_data]` or similar yfinance-side errors at this scale (37,270 tickers). Expected, but not enough to auto-act on.

**Do NOT remove failing tickers from V8 mid-run.** That is a curation decision for the operator, not a Claude Code decision.

A future phase **may** want to land a **OnePass failure review ledger** so repeated `no_data` / delisted / stale tickers can be reviewed and either re-curated into V8 or rolled into the ban-list. That's a separate scoped phase; not in scope right now.

## Strategic correction (durable framing)

  - **Stop drifting downstream.** Earlier sprint narrative kept treating "Phase 6I-57 supervised ImpactSearch workbook generation" for the 6-ticker pilot (SPY, AAPL, JNJ, WMT, HD, MCD) as the immediate next action. That direction is **paused**, not next.
  - The current sprint is **upstream** at the V8 OnePass gate. Everything downstream of OnePass is blocked behind a clean V8 OnePass run + audit.
  - **OnePass is T-1 prep data, not the live website overlay.** OnePass produces the signal libraries that ImpactSearch (and downstream) consume; it does **not** drive the user-facing layer.
  - Keep future work **narrow, direct, and understandable.** No multi-phase plans. No "while we're here" refactors. The downstream chain comes later, one phase at a time, in the order the operator authorizes.

## What's still valid as scaffolding (do not redo this work)

These already-merged phases built infrastructure that will be re-used once the V8 OnePass gate passes. They are **not** pending work, just reference:

  - **Phase 6I-56** (`7e23031`) — safe non-Dash ImpactSearch workbook runner (`project/impactsearch_workbook_runner.py`). Dry-run by default; `--write` + `--allow-network-fetch` required for actual workbook generation. Atomic XLSX write preserves ImpactSearch append/dedupe semantics. Primary signal-library availability scan + ban-list-style fields on the per-row + manifest output.
  - **Phase 6I-55a** (`83ba5b5`) — read-only ImpactSearch / primary-universe readiness planner.
  - **Phase 6I-54b** (`63b06c9`) — `price_cache/daily/{SPY,AAPL,JNJ,WMT,HD,MCD}.csv` written.
  - **Phase 6I-50 → 6I-55** — large-universe launch planner, rollout batch planner, locked StackBuilder rollout policy, pilot batch preflight, price-cache rebuild planner.

None of these need to be re-done. They wait behind the V8 OnePass gate.

## No-production-activity contract for this handoff doc

Writing this doc + updating the auto-memory was a docs-only operation. No OnePass / ImpactSearch / StackBuilder / TrafficFlow / Confluence pipeline / yfinance / source-refresh / registry-validation / writer invocation. The Phase 6I-56 ImpactSearch workbook runner was not invoked. Production roots untouched. The operator's in-progress OnePass run (if any) is theirs to drive; this doc does not start or interrupt it.
