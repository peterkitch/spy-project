# V8 OnePass Gate Audit — 2026-05-16

**Verdict:** **PASS.** Sprint cursor advances from "V8 OnePass gate pending" to
"V8 OnePass gate PASSED." Next phase is **supervised ImpactSearch workbook
generation from the fresh V8 OnePass baseline.** StackBuilder remains
downstream and **MUST NOT** be run until ImpactSearch workbooks are fresh.

This is a **docs-only audit record**. No engine / pipeline / writer / network
fetch was invoked for this commit (see "Explicit no-production-activity
statement" below).

---

## 1. Audit subject

The fresh V8 OnePass workbook produced by a manual OnePass Dash-UI run that
the operator started 2026-05-15 ~19:12 PT and that completed 2026-05-16
~10:11 PT against the operator-curated V8 ticker universe (37,270 tickers).

Workbook + manifest in the canonical Documents checkout:

  - `project/output/onepass/onepass.xlsx`
  - `project/output/onepass/onepass.xlsx.manifest.json`

Both files are gitignored operational artifacts (rule
`project/.gitignore:10: output/`). They are **not** committed by this evidence
record.

The workbook was originally written by a parallel worktree
(`emdash/worktrees/spy-project/emdash/website-launch-ac8wf`, branch
`codex-audit-pr-274`, HEAD `887d882` recorded in the manifest's `git_commit`)
and mirrored byte-for-byte into the Documents checkout as a separate
reconciliation step before this audit. Source files were preserved; no
worktree state was modified.

## 2. Repo / cwd state at audit time

  - Documents checkout: `C:\Users\sport\Documents\PythonProjects\spy-project`
  - Branch: `main`
  - HEAD before this commit: `d7d626a` — *Sprint position handoff: V8 OnePass gate is the current cursor*
  - HEAD equals `origin/main` (verified via `git fetch origin main` + `git log -1 origin/main`)
  - Working tree: clean
  - Pinned interpreter:
    `C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe`
    (Python 3.12.2, NumPy 1.26.4, pandas 2.2.1, SciPy 1.13.1)

## 3. Verified findings (every figure derived live from the files)

### 3.1 File existence

| File | Exists | Bytes |
|---|---|---|
| `project/output/onepass/onepass.xlsx` | yes | 3,103,667 |
| `project/output/onepass/onepass.xlsx.manifest.json` | yes | 2,279 |
| `project/global_ticker_library/data/V8_Ticker.txt` | yes | 293,311 |
| `project/global_ticker_library/curation/v8_removed_from_master_banlist.json` | yes | 563,347 |

### 3.2 SHA-256

  - **Workbook:** `7bf83e85fb119e95ef0f4aa8a669268f32679dea4abb6c3a88f5bbf3d1a6f067`
  - **Manifest:** `f20ec3c5c175db9c642105971710c08d587dea49c5a727033f14d78d508bec3d`

### 3.3 Workbook structure

  - **Total row count:** 35,990
  - **Column count:** 15
  - **Column headers (in order):** `Primary Ticker`, `Trigger Days`, `Wins`,
    `Losses`, `Win Ratio (%)`, `Std Dev (%)`, `Sharpe Ratio`, `t-Statistic`,
    `p-Value`, `Significant 90%`, `Significant 95%`, `Significant 99%`,
    `Avg Daily Capture (%)`, `Total Capture (%)`, `Last Updated`
  - **Primary Ticker column name (verbatim):** `'Primary Ticker'`
  - Column set matches `ALL_COLS` defined at `project/onepass.py:2113-2119`.

### 3.4 Blank / NaN Primary Ticker scan — IMPORTANT RECONCILIATION

Two distinct read paths give two distinct answers because **the V8 universe
contains the literal tickers `NA` and `NAN`**, both of which pandas
auto-coerces to NaN under default Excel parsing.

| Read mode | Blank/NaN rows |
|---|---|
| `pd.read_excel(...)` — default `keep_default_na=True` | 2 (false positives) |
| `pd.read_excel(..., keep_default_na=False, na_values=[])` — strict | **0** |

Under the strict read:

  - **Pandas index 29,484 (Excel row 29,486):** Primary Ticker = `'NAN'`
    (literal string; legitimate V8 ticker). Trigger Days 6,218, Wins 2,983,
    Losses 3,235, Win Ratio 47.97%, Std Dev 0.8112, Sharpe -0.28,
    t-Stat 0.5429, p 0.5872, Avg Daily Capture 0.0056%, Total Capture 34.7289%,
    Last Updated 2026-05-16 10:11:47.
  - **Pandas index 31,595 (Excel row 31,597):** Primary Ticker = `'NA'`
    (literal string; legitimate V8 ticker). Trigger Days 810, Wins 362,
    Losses 448, Win Ratio 44.69%, Std Dev 10.6498, Sharpe -0.42,
    t-Stat -0.6929, p 0.4886, Avg Daily Capture -0.2593%,
    Total Capture -210.0101%, Last Updated 2026-05-16 10:11:47.

Both `NA` and `NAN` are present in `V8_Ticker.txt`. The workbook is correct;
the earlier "1 NaN row at index 31,595" claim was the artifact of pandas
default NaN coercion on a non-strict read and **does NOT reproduce under the
strict read**. Strict-mode blank/NaN count is zero.

**Downstream consumer note (carry forward):** any reader of this workbook —
including the existing ImpactSearch / StackBuilder / TrafficFlow chain — must
either pass `keep_default_na=False, na_values=[]` to `pd.read_excel` or
otherwise treat `'NA'` and `'NAN'` cell values as primary-ticker strings, not
as missing data. This is a property of the V8 universe, not a defect of this
run.

### 3.5 Ticker counts

| Quantity | Strict-read value |
|---|---|
| Rows with valid Primary Ticker | 35,990 |
| Unique Primary Ticker values | 35,990 |
| Duplicate rows | 0 |

### 3.6 Universe checks

  - **V8 universe size (`V8_Ticker.txt`):** 37,270
  - **Ban-list size (`v8_removed_from_master_banlist.json`):** 36,395
  - **Workbook tickers outside V8:** **0**
  - **Ban-list tickers present in workbook:** **0**

### 3.7 Missing V8 tickers — full reconciliation

  - **V8 tickers absent from workbook:** **1,280**  (37,270 - 35,990)
  - **Log path used:** `C:\Users\sport\emdash\worktrees\spy-project\emdash\website-launch-ac8wf\project\logs\onepass.log`
    (49,636,257 bytes, mtime 2026-05-16 10:11:46). The Documents-checkout
    `project/logs/onepass.log` is a 0-byte stale file from 2026-05-15 18:44
    that predates the run; the path-resolution guard fell back to the actual
    run log in the worktree.

| Skip category | Log pattern | Unique tickers |
|---|---|---|
| No-data skip | `No data for ticker <T>, skipping.` | 1,253 |
| Insufficient-history skip | `Insufficient days of data for <T>, skipping.` | 27 |
| Manifest_failed (rebuilt, present in workbook) | `[ONEPASS:manifest_failed] <T>:` | 1 (`TY.P`) |

  - **Accounted-for missing:** 1,253 + 27 = **1,280** (exact match).
  - **Unexplained missing under strict read:** **0**.
  - Representative no-data skips (5 of 1,253): `000075.KS`, `0011.HK`,
    `001140.KS`, `003560.KS`, `005390.KS`.
  - Representative insufficient-history skips (5 of 27): `1COV.DE`, `473A.F`,
    `59Q.F`, `7GL.F`, `AEBA.F`.
  - Single manifest_failed: `TY.P` — provenance manifest mismatch
    (`params.engine_version` missing in stored manifest vs current `1.0.0`),
    rebuild forced; TY.P is consequently present in the final workbook (the
    rebuild succeeded — `TY.P` is in the strict-read ticker set).

### 3.8 Manifest verification (approved provenance loader)

Verified via `project/provenance_manifest.py:load_verified_xlsx_artifact(
WB, strict=True)`:

  - `ok=True`, `legacy=False`
  - `mismatches=[]`
  - `warnings=[]`
  - `artifact_file_sha256` in manifest matches recomputed workbook SHA-256
    (`7bf83e85…`)
  - `producer_engine`: `onepass`
  - `engine_version`: `1.0.0`
  - `build_timestamp`: `2026-05-16T17:11:58.125872+00:00` (UTC)
  - `git_commit` (recorded by the run): `887d88250a3d953b92c926bee428104d214d88bb`
  - `git_dirty` (recorded by the run): `False`
  - `current_run_row_count`: 35,990 (matches DataFrame row count)
  - Loader mismatches / warnings: none.

## 4. Gate decision — PASS

| Criterion | Required | Observed | Pass |
|---|---|---|---|
| Workbook exists | yes | yes | ✓ |
| Manifest exists | yes | yes | ✓ |
| Workbook tickers outside V8 | 0 | 0 | ✓ |
| Ban-list tickers in workbook | 0 | 0 | ✓ |
| Unexplained missing V8 tickers | 0 | 0 (1,253 + 27 = 1,280 fully accounted) | ✓ |
| Unexplained blank / NaN Primary Ticker rows | 0 | 0 under strict read; the 2 default-read NaN rows are legitimate V8 tickers `NA` / `NAN` | ✓ |
| Manifest verification + hash match | pass | `load_verified_xlsx_artifact(strict=True)` → `ok=True`, hash matches | ✓ |

Gate verdict: **PASS.**

## 5. Reproducible audit command

The audit was executed as a single inline `python -c`-equivalent heredoc
invocation. No `.py` script was created in the repo. Reproducer block (paste
into a bash shell from the repo root):

```bash
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" - <<'PYEOF'
import hashlib, json, os, re, sys
from pathlib import Path
import pandas as pd

REPO = Path(r"C:/Users/sport/Documents/PythonProjects/spy-project")
WB   = REPO / "project/output/onepass/onepass.xlsx"
MF   = REPO / "project/output/onepass/onepass.xlsx.manifest.json"
V8   = REPO / "project/global_ticker_library/data/V8_Ticker.txt"
BL   = REPO / "project/global_ticker_library/curation/v8_removed_from_master_banlist.json"

# (1) hashes
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""): h.update(chunk)
    return h.hexdigest()
print("workbook sha256:", sha256(WB))
print("manifest sha256:", sha256(MF))

# (2) STRICT read — NA/NAN are valid V8 tickers
df = pd.read_excel(WB, engine="openpyxl", keep_default_na=False, na_values=[])
PRIM = "Primary Ticker"
print("row_count:", len(df), "columns:", list(df.columns))
print("blank rows under strict read:", int((df[PRIM].astype(str).str.strip() == "").sum()))

# (3) universe
v8 = {t.strip().upper() for t in V8.read_text(encoding="utf-8").replace("\n", ",").split(",") if t.strip()}
bl_obj = json.loads(BL.read_text(encoding="utf-8"))
bl_list = bl_obj.get("tickers") or bl_obj.get("banned") or (next((v for v in bl_obj.values() if isinstance(v,list)), []) if isinstance(bl_obj,dict) else bl_obj)
bl = {str(t).strip().upper() for t in bl_list if str(t).strip()}
wb = {str(t).strip().upper() for t in df[PRIM].tolist() if str(t).strip()}
print("V8 size:", len(v8), "ban-list size:", len(bl), "workbook unique:", len(wb))
print("outside V8:", len(wb - v8), "ban-list hits in workbook:", len(wb & bl))

# (4) log breakdown (uses worktree log if Documents log is 0-byte)
LOG = Path(r"C:/Users/sport/emdash/worktrees/spy-project/emdash/website-launch-ac8wf/project/logs/onepass.log")
nd, ins, mf = set(), set(), set()
nd_re = re.compile(r"No data for ticker (\S+), skipping")
in_re = re.compile(r"Insufficient days of data for (\S+), skipping")
mf_re = re.compile(r"\[ONEPASS:manifest_failed\] (\S+):")
for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
    m = nd_re.search(line);  m and nd.add(m.group(1).strip().upper())
    m = in_re.search(line);  m and ins.add(m.group(1).strip().upper())
    m = mf_re.search(line);  m and mf.add(m.group(1).strip().upper())
print("no_data:", len(nd), "insufficient:", len(ins), "manifest_failed:", sorted(mf))
print("unexplained missing:", len((v8 - wb) - nd - ins))

# (5) approved manifest loader
sys.path.insert(0, str(REPO / "project"))
from provenance_manifest import load_verified_xlsx_artifact
_, vr = load_verified_xlsx_artifact(WB, strict=True)
print("loader ok:", vr.ok, "legacy:", vr.legacy, "mismatches:", vr.mismatches, "warnings:", vr.warnings)
PYEOF
```

## 6. Explicit no-production-activity statement

This evidence record was produced entirely by **read-only parsing** of the
workbook, manifest, V8 list, ban-list, and the run's stdout-style log file.

The following were **NOT** invoked in producing this record:

  - OnePass (no `onepass.py` invocation; no signal-library write)
  - ImpactSearch (no workbook generation, no `process_primary_tickers` call,
    no `impactsearch_workbook_runner.py` run)
  - StackBuilder (no stack writes; locked-policy gate untouched)
  - TrafficFlow (no K-engine or MTF-bridge invocation)
  - Confluence pipeline runner / patch writer / promotion writer / daily-board
    automation writer (all guarded surfaces untouched)
  - `signal_engine_cache_refresher.py` (no source-cache refresh; no
    network fetch)
  - yfinance / any provider client (no HTTP traffic of any kind)
  - `registry.db`, `V8_Ticker.txt`, the ban-list, `master_tickers.txt` (none
    were modified; reads were read-only)

The workbook and manifest (`project/output/onepass/*`) are gitignored
operational artifacts and are **not** committed by this evidence record. No
temporary audit script was created in the repo.

## 7. Next step (one sentence)

The next operator-authorized phase is **supervised ImpactSearch workbook
generation from the fresh V8 OnePass baseline** — to be planned and executed
under the three-voice workflow (web Claude preflight → Codex audit → Claude
Code implement → Codex audit) before any StackBuilder, TrafficFlow, or
Confluence pipeline action.

**StackBuilder remains strictly downstream and must NOT be run until the
ImpactSearch workbooks consumed by `stackbuilder.py --prefer-impact-xlsx`
have been freshly regenerated against this V8 OnePass baseline.**
