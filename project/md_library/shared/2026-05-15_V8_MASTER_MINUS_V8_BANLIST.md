# V8 master-minus-V8 ban-list

**Date:** 2026-05-15
**Scope:** narrow ban-list creation only. No pipeline behavior change.

> This phase does not replace the operational master list. It only records tickers present in the prior master but absent from V8 so they are not silently reintroduced later.

## Inputs

| File | Bytes | SHA-256 |
|---|---|---|
| `project/global_ticker_library/data/master_tickers.txt` | 527,554 | `fc4e026aa8e9d7b0c5c8bb5ffaeb5997dccbb18a56b9401e3f4cf7a7574816e2` |
| `project/global_ticker_library/data/V8_Ticker.txt` | 293,311 | `00f26ce194ea6411b3e8413ac87e1e6f400a329fa2c2dadd9b62d0e83f3334e3` |

Both files were parsed with the same one-shot rule: split on `[\s,]+`, strip whitespace, uppercase, drop empties, dedupe. `master_tickers.txt` is `,`-separated with no spaces; `V8_Ticker.txt` is `, `-separated. The split regex handles both.

Neither input file was modified by this phase.

## Counts

| Metric | Value |
|---|---|
| `master_count` | **73,665** |
| `v8_count` | **37,270** |
| `overlap_count` | **37,270** |
| `banned_removed_count` (master − V8) | **36,395** |
| `v8_new_count` (V8 − master) | **0** |

**Key observation:** V8 is a strict subset of master. The operator-curated V8 list removed 36,395 tickers and added zero. That confirms the V8 file was curated against the existing master universe (not, e.g., regenerated independently and merged) and gives a clean ban-list semantics: any ticker the future builder might suggest that lies in `banned_removed_tickers` was explicitly excluded by the V8 curation pass.

## Outputs

Two files added (no other repo state touched):

- `project/global_ticker_library/curation/v8_removed_from_master_banlist.json` — schema `v8_removed_from_master_banlist_v1`. Carries the 36,395-entry alphabetical ban-list, source-file SHA-256s for reproducibility, and the five canonical counts plus `v8_new_tickers` (empty) for completeness.
- `project/md_library/shared/2026-05-15_V8_MASTER_MINUS_V8_BANLIST.md` — this evidence doc.

One narrow `.gitignore` exception (`!global_ticker_library/curation/v8_removed_from_master_banlist.json`) was added to `project/.gitignore` so this specific JSON is tracked under the existing `*.json` ignore rule. The root rule (`*.json` in repo-root `.gitignore` line 2) still ignores every other JSON in `global_ticker_library/`.

## First / last 20 `banned_removed_tickers`

**First 20 (alphabetical):**

```
00-USD, 0731.HK, 0812.HK, 0DOG-USD, 0X0-USD, 0X023909-USD, 0XBTC-USD,
0XGAS-USD, 0XL-USD, 0XY-USD, 1-USD, 1000X-USD, 101M-USD, 102280.KS,
10SET-USD, 1815.TWO, 1ART-USD, 1CAT-USD, 1EARTH-USD, 1FLR-USD
```

**Last 20 (alphabetical):**

```
^NYA, ^NZ50, ^OMX, ^OSEAX, ^RUT, ^SPAHLVCP, ^SPX, ^SSMI, ^STI, ^STOXX50E,
^TA125.TA, ^TNX, ^TWII, ^TYX, ^VIX, ^XAX, ^XDA, ^XDB, ^XDE, ^XDN
```

## Exact one-shot script (kept inline; not committed as helper code)

Run from `project/` with the pinned `spyproject2` interpreter at `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`:

```python
import re, json, datetime, pathlib, hashlib

def parse(path):
    text = pathlib.Path(path).read_text(encoding='ascii')
    tokens = [t.strip().upper() for t in re.split(r'[\s,]+', text) if t.strip()]
    return tokens, set(tokens)

m_path = 'global_ticker_library/data/master_tickers.txt'
v_path = 'global_ticker_library/data/V8_Ticker.txt'
m_tokens, m_set = parse(m_path)
v_tokens, v_set = parse(v_path)
overlap = m_set & v_set
banned  = sorted(m_set - v_set)
v8_new  = sorted(v_set - m_set)

payload = {
    'schema_version': 'v8_removed_from_master_banlist_v1',
    'source_master_file': m_path,
    'source_v8_file': v_path,
    'source_master_sha256': hashlib.sha256(pathlib.Path(m_path).read_bytes()).hexdigest(),
    'source_v8_sha256':     hashlib.sha256(pathlib.Path(v_path).read_bytes()).hexdigest(),
    'created_utc': datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'reason_code': 'absent_from_operator_curated_v8',
    'master_count': len(m_set),
    'v8_count': len(v_set),
    'overlap_count': len(overlap),
    'banned_removed_count': len(banned),
    'v8_new_count': len(v8_new),
    'v8_new_tickers': v8_new,
    'banned_removed_tickers': banned,
}
pathlib.Path('global_ticker_library/curation/v8_removed_from_master_banlist.json').write_text(
    json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8',
)
```

Output of the script (captured 2026-05-15):

```
master_count: 73665
v8_count: 37270
overlap_count: 37270
banned_removed_count: 36395
v8_new_count: 0
```

## Out-of-scope items (none performed)

No edits to `master_tickers.txt`, `V8_Ticker.txt`, `registry.db`, or any other `global_ticker_library/` file. No OnePass / ImpactSearch / StackBuilder / TrafficFlow / Confluence pipeline / yfinance / source-refresh / registry-validation / writer invocation. The Phase 6I-56 ImpactSearch workbook runner was not invoked. Production roots (`cache/results`, `cache/status`, `output/research_artifacts`, `output/stackbuilder`, `signal_library/data/stable`, `price_cache/daily`, `output/impactsearch`) untouched.

## What this is NOT

  - **Not** a V8 adoption planner. The operational master list (`master_tickers.txt`) is unchanged.
  - **Not** a registry update. `registry.db` is unchanged.
  - **Not** an authorization to drive a ticker-builder run against V8.
  - **Not** a deletion list. The 36,395 entries are an exclusion record for review; future curation can amend or rescind any of them with a separate phase.

The ban-list is a **review artifact**. Any decision to apply it — to gate `master_tickers.txt`, to filter future builder universes, to wire into the OnePass / ImpactSearch / StackBuilder chain — is out of scope for this phase and requires a separate authorized prompt.
