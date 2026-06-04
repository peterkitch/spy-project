# K=6 MTF Phase 5 Honest-Validation Report (205-secondary candidate)

**Date:** 2026-06-04

**Status:** OPERATOR-REVIEW REPORT PACKAGE (docs-only; derived from the accepted private 205-secondary candidate's validation sidecar; no compute, no promotion, no deploy)

**Author:** PRJCT9 sprint

**Scope:** Phase 5 honest-validation evidence package for the K=6 MTF 205-secondary private partial candidate (the accepted clean subset of the full-248 recook). This report mirrors the 8-ticker launch report structure scaled to 205 secondaries.

---

## 1. Executive summary

- 205 secondaries were validated under the locked walk-forward + Benjamini-Hochberg + empirical methodology; `validation_status=valid`.
- 88 secondaries are board_validated (BH-reported, `bh_q_value <= 0.05`).
- 117 secondaries are not_validated and remain displayed on the research board for transparency: 20 were tested but did not clear the BH gate, and 97 were not testable (`empirical_not_run`; sparse directional triggers).
- 43 secondaries were Stage-A excluded (drop-and-list) due to 22 unavailable source tickers; they are omitted from the ranked board and listed in the coverage section.
- There are exactly two validation outcomes (board_validated / not_validated). There is no near_threshold tier.

## 2. Evidence sources and artifact-run binding

All paths are project-relative.

**Ranking artifact (accepted 205-secondary candidate):**

- Path: `output/k6_mtf/20260604T110400Z_recook_full248_clean_csv/k6_mtf_ranking.json`
- Ranking `run_id`: `20260604T110400Z_recook_full248_clean_csv`

**Validation sidecar (`validation_status=valid`):**

- Path: `output/validation/20260604T120000Z_validation_full205/validation.json`
- SHA-256: `8e48fd56dc2c9f4f16598c2c01b71f2b87e691caf855b53c97fc704baf3871ef`
- Validation `run_id`: `20260604T120000Z_validation_full205`

**Paired machine-readable report manifest (for the promotion gate):**

- Path: `md_library/shared/2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.manifest.json`
- The manifest carries the report SHA-256, the sidecar SHA-256, the ranking/validation run ids, the counts, and the methodology fields. The promotion gate reads the manifest (not this Markdown prose) for its binding checks.

This report does NOT carry its own SHA-256 inside its body. The operator/gate computes the report file SHA-256 after the final byte content is fixed; the manifest records that value as `report_sha256`.

## 3. Methodology summary

Source: locked methodology at `md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md` (Sections 4, 6, 7, 8, 9, 10, 13.5, 15). Same engine and adapter as the 8-ticker launch report; only the universe scope differs.

- Walk-forward OOS evaluation; same-secondary buy-and-hold baseline; Benjamini-Hochberg primary multiple-comparisons control with Bonferroni supplementary disclosure; direction-preserving empirical permutation p-value + bootstrap Sharpe CI for BH-survivors plus borderline candidates; honest `empirical_not_run` for strategies outside the empirical subset; strict no-lookahead per-fold cutoffs.

## 4. Campaign parameters

All values extracted from the validation sidecar (not from memory).

| Parameter | Value |
|---|---|
| Validation `run_id` | `20260604T120000Z_validation_full205` |
| `producer_engine` | `k6_mtf` |
| `app_surface` | `run_directory` |
| `validation_contract_version` | `v1` |
| `validation_methodology_version` | `v1` |
| `validation_status` | `valid` |
| `data_available_through` | 2026-06-04 |
| In-sample window | 1927-12-30 -- 1933-01-11 |
| OOS window | 1933-01-12 -- 2026-02-05 |
| `walk_forward_n_folds` | 99 |
| `multiple_comparisons_control_method` | `benjamini_hochberg` |
| `multiple_comparisons_supplementary` | `bonferroni` |
| `multiple_comparisons_control_alpha` | 0.05 |
| `n_permutations` | 10000 |
| `n_bootstrap_samples` | 10000 |
| `bootstrap_ci_level` | 0.95 |
| `n_strategies_tested` | 205 |
| `n_strategies_reported` (board_validated) | 88 |
| `rng_seed` | null (not persisted in this legacy sidecar) |

## 5. Validation universe

The validated universe is the 205 ranked secondaries of the accepted private 205-secondary candidate. This is the clean subset of the full-248 recook after Stage-A exclusions; it is a deliberately partial universe (see Section 8). The full ranked list and every per-row metric live in the cited ranking artifact and validation sidecar; this report summarizes and excerpts them.

## 6. Results summary

**Survivorship (from the sidecar `survivorship_summary`):**

- `total_tested`: 205
- `total_reported_bh`: 88
- `total_empirical_validated`: 108
- `total_empirical_not_run`: 97
- `did_not_survive_bh`: 117
- `did_not_survive_empirical`: 1
- `did_not_survive_no_triggers`: 0
- `did_not_survive_insufficient_history`: 0

**Outcome partition (derived per-row from the sidecar):**

- board_validated: 88
- not_validated: 117 (= 20 validated-but-not-BH + 97 empirical_not_run)
- empirical_validated total: 108

### 6a. Board-validated survivors (88): representative rows

Top representative board_validated rows by aggregate Sharpe (full set in the sidecar):

| `strategy_id` | Aggregate Sharpe | BH q-value | Bonferroni p | Empirical p |
|---|---|---|---|---|
| `k6_mtf:TSLZ` | 6.8073 | 0.0016 | 0.0478 | 0.0002 |
| `k6_mtf:GDXD` | 6.6694 | 0.0000 | 0.0002 | 0.0001 |
| `k6_mtf:BITI` | 6.0882 | 0.0000 | 0.0002 | 0.0001 |
| `k6_mtf:ETHA` | 5.9745 | 0.0032 | 0.1208 | 0.0005 |
| `k6_mtf:ETHD` | 5.8082 | 0.0067 | 0.2869 | 0.0005 |
| `k6_mtf:CONL` | 5.7684 | 0.0002 | 0.0024 | 0.0001 |
| `k6_mtf:NVDU` | 5.5366 | 0.0006 | 0.0105 | 0.0002 |
| `k6_mtf:NVDD` | 5.5023 | 0.0006 | 0.0102 | 0.0002 |
| `k6_mtf:ETHU` | 5.3737 | 0.0021 | 0.0721 | 0.0002 |
| `k6_mtf:MSTX` | 5.2234 | 0.0150 | 0.7472 | 0.0014 |
| `k6_mtf:SGOV` | 5.1308 | 0.0000 | 0.0000 | 0.0001 |
| `k6_mtf:GGLL` | 5.0508 | 0.0175 | 0.9116 | 0.0029 |
| `k6_mtf:NVDL` | 5.0392 | 0.0002 | 0.0021 | 0.0001 |
| `k6_mtf:NVDX` | 4.9069 | 0.0021 | 0.0718 | 0.0006 |
| `k6_mtf:TSLQ` | 4.4037 | 0.0133 | 0.6242 | 0.0026 |

### 6b. Not_validated -- tested but did not clear BH (20)

These rows ran the empirical layer (`empirical_validation_status=validated`) but their BH q-value exceeded alpha. They remain displayed as not_validated for research transparency.

| `strategy_id` | Aggregate Sharpe | BH q-value | Empirical p |
|---|---|---|---|
| `k6_mtf:VTI` | 1.1543 | 0.0510 | 0.0093 |
| `k6_mtf:UNG` | 1.4351 | 0.0519 | 0.0180 |
| `k6_mtf:DFEN` | 2.1579 | 0.0543 | 0.0089 |
| `k6_mtf:MSTR` | 1.0128 | 0.0563 | 0.0155 |
| `k6_mtf:JNK` | 0.8314 | 0.0602 | 0.0054 |
| `k6_mtf:IVW` | 0.9229 | 0.0607 | 0.0097 |
| `k6_mtf:BITO` | 2.8522 | 0.0636 | 0.0116 |
| `k6_mtf:REK` | 1.1521 | 0.0643 | 0.0170 |
| `k6_mtf:^W5000` | 0.7745 | 0.0645 | 0.0310 |
| `k6_mtf:SCHX` | 1.4801 | 0.0668 | 0.0191 |
| `k6_mtf:DUSL` | 1.8001 | 0.0691 | 0.0219 |
| `k6_mtf:FDN` | 1.3837 | 0.0696 | 0.0219 |
| `k6_mtf:VCIT` | 0.1735 | 0.0700 | 0.0076 |
| `k6_mtf:AAPD` | 2.8896 | 0.0721 | 0.0208 |
| `k6_mtf:QQQE` | 1.5249 | 0.0778 | 0.0357 |
| `k6_mtf:SPXL` | 1.6488 | 0.0801 | 0.0490 |
| `k6_mtf:VEA` | 0.8145 | 0.0801 | 0.0151 |
| `k6_mtf:IVV` | 0.8331 | 0.0833 | 0.0153 |
| `k6_mtf:XLC` | 1.9446 | 0.0939 | 0.0439 |
| `k6_mtf:META` | 1.7538 | 0.0974 | 0.0638 |

### 6c. Not_validated -- not testable / sparse directional triggers (97)

These secondaries had too few pooled directional (Buy/Short) triggers to run the empirical permutation/bootstrap null (`empirical_validation_status=empirical_not_run`). Empirical p-value and bootstrap CI are N/A for these rows; BH q-value / Bonferroni render when present. They remain displayed as not_validated.

Secondaries (empirical_not_run):

`AGG, AGQ, AMZD, AMZU, ARKB, ARKK, BITU, BITX, BND, CONI, COST, CWEB, DUST, DX-Y.NYB, EEM, EEV, EFA, EMB, EWJ, EWY, FAS, FAZ, FBL, FBTC, FNGU, FXI, FXP, GGLS, GOOG, GOVT, IBIT, IEF, IEMG, INDA, ITA, ITOT, IWD, IWF, IWM, JBSS, KWEB, LABU, METD, MSFT, PDBC, PILL, QLD, QQQ, RXD, SBIT, SCHF, SHY, SLV, SMH, SPLG, SPY, SZK, TBT, TECL, TIP, TLT, TMF, TMV, TYD, TYO, TZA, UTSL, VEU, VGT, VNQ, VO, VT, VUG, VWO, VXUS, VYM, WEBS, XAR, XBI, XHE, XLE, XLK, XLP, XLY, XRT, ZSL, ^DJI, ^FTSE, ^FVX, ^GDAXI, ^GSPC, ^NDX, ^NYA, ^RUT, ^STOXX50E, ^TNX, ^XAU`

## 7. Stage-A exclusions (drop-and-list coverage)

43 secondaries were excluded upstream at Stage A due to 22 unavailable source tickers. They are NOT part of the ranked board and are listed here for coverage transparency only. Per operator-locked policy, frozen K=6 stacks are NOT recovered, rotated, remapped, or rewritten.

Unavailable source tickers (22): `011810.KS, AVLA.F, BCDMF, BLKC, BTA, CDTX, CFX.TO, CMLS, CTRA, DR8A.F, GCG-A.TO, JAMF, MIDZ, OMI, PCH, POH3.F, RPI-UN.TO, TEF, TGNA, TIG.AX, VRE, XCQ.F`

| Excluded secondary | Reason | Causes |
|---|---|---|
| AAPB | stage_a_unavailable:dead_no_history | DR8A.F (dead_no_history; member) DR8A.F[I] |
| AAPU | stage_a_unavailable:dead_no_history | DR8A.F (dead_no_history; member) DR8A.F[I] |
| CDTX | stage_a_unavailable:dead_no_history | CDTX (dead_no_history; secondary) |
| CURE | stage_a_unavailable:dead_no_history,not_current | CTRA (not_current; member) CTRA[D]; TIG.AX (dead_no_history; member) TIG.AX[D]; XCQ.F (not_current; member) XCQ.F[D] |
| DBA | stage_a_unavailable:dead_no_history | CFX.TO (dead_no_history; member) CFX.TO[D] |
| DRIP | stage_a_unavailable:not_current | VRE (not_current; member) VRE[I] |
| FNGD | stage_a_unavailable:dead_no_history | BCDMF (dead_no_history; member) BCDMF[I] |
| GUSH | stage_a_unavailable:not_current | 011810.KS (not_current; member) 011810.KS[D] |
| IEFA | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| IHI | stage_a_unavailable:not_current | CTRA (not_current; member) CTRA[D] |
| IXUS | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| JDST | stage_a_unavailable:insufficient_history | POH3.F (insufficient_history; member) POH3.F[I] |
| KRE | stage_a_unavailable:dead_no_history | OMI (dead_no_history; member) OMI[D] |
| MIDU | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[I] |
| MIDZ | stage_a_unavailable:not_current | MIDZ (not_current; secondary) |
| MSFD | stage_a_unavailable:dead_no_history | JAMF (dead_no_history; member) JAMF[I] |
| MSFU | stage_a_unavailable:dead_no_history | JAMF (dead_no_history; member) JAMF[D] |
| MSTZ | stage_a_unavailable:dead_no_history,insufficient_history | CMLS (dead_no_history; member) CMLS[D]; AVLA.F (insufficient_history; member) AVLA.F[I] |
| NAIL | stage_a_unavailable:dead_no_history | TGNA (dead_no_history; member) TGNA[I] |
| QUAL | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| RETL | stage_a_unavailable:not_current | XCQ.F (not_current; member) XCQ.F[D] |
| SCC | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[D] |
| SCHD | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| SCHG | stage_a_unavailable:dead_no_history,not_current | CTRA (not_current; member) CTRA[D]; TIG.AX (dead_no_history; member) TIG.AX[D]; XCQ.F (not_current; member) XCQ.F[D] |
| SDOW | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[I] |
| SDP | stage_a_unavailable:dead_no_history | BTA (dead_no_history; member) BTA[I] |
| SDS | stage_a_unavailable:dead_no_history | TEF (dead_no_history; member) TEF[D] |
| SOXL | stage_a_unavailable:dead_no_history | GCG-A.TO (dead_no_history; member) GCG-A.TO[D] |
| SSO | stage_a_unavailable:dead_no_history | TEF (dead_no_history; member) TEF[I] |
| SVXY | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| TSLL | stage_a_unavailable:not_current | BLKC (not_current; member) BLKC[D] |
| UDOW | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| URTY | stage_a_unavailable:not_current | XCQ.F (not_current; member) XCQ.F[D] |
| USMV | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| UUP | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[D] |
| VB | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[I] |
| VBK | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[I] |
| VIG | stage_a_unavailable:dead_no_history | TEF (dead_no_history; member) TEF[I] |
| WEBL | stage_a_unavailable:not_current | CTRA (not_current; member) CTRA[D] |
| XOP | stage_a_unavailable:dead_no_history | RPI-UN.TO (dead_no_history; member) RPI-UN.TO[I] |
| XSD | stage_a_unavailable:dead_no_history | GCG-A.TO (dead_no_history; member) GCG-A.TO[D] |
| XTN | stage_a_unavailable:dead_no_history | TIG.AX (dead_no_history; member) TIG.AX[D] |
| ^DJT | stage_a_unavailable:dead_no_history | PCH (dead_no_history; member) PCH[D] |

## 8. Interpretation

- The 205-secondary validation campaign is valid: the walk-forward grid completed across all folds, BH/Bonferroni ran on the full set, and the empirical layer ran for the BH-survivor plus borderline subset.
- 88 secondaries are the operator's honest board-validated evidence base. They are evidence, not predictions or guarantees of future performance.
- not_validated rows (both tested-but-not-BH and empirical_not_run) remain visible as research-ranked rows; the board never lets a high rank read as statistical validation.

## 9. Limitations and honesty notes

- **Accepted partial universe.** This candidate is the clean subset of the full-248 recook; the universe is deliberately partial.
- **Stage-A drop-and-list policy.** The 43 Stage-A exclusions are listed with causes and are not recovered or rotated.
- **not_validated rows remain visible.** They are disclosed for transparency, not promoted as board-validated.
- **empirical_not_run** rows reflect sparse directional triggers, a data characteristic, not a process failure.
- **rng_seed is null (not persisted in this legacy sidecar).** This sidecar predates rng_seed persistence in the validation contract; the seed used at run time was not persisted into this artifact. Future reruns persist it. This is honest disclosure, not missing evidence.
- **Survivorship / data-source disclosure** is consistent with the 8-ticker report: self-administered validation evidence over a yfinance-sourced universe; bounded to the tested universe and run; not a guarantee of future performance.
- **Report generation performs no promotion** and writes no frontend/public fixture.

## 10. Promotion-helper inputs (FUTURE; do NOT run from this PR)

When the operator later authorizes v2 public promotion (and Phase 5G data licensing is separately cleared), the promotion gate consumes the binding manifest plus this report and the sidecar. Required inputs (paths project-relative): the v2 fixture, this report path + its computed SHA-256, the paired report-manifest path, and the validation sidecar path + SHA-256. The gate verifies report <-> manifest <-> sidecar <-> fixture agreement and refuses on any mismatch. Public promotion remains a separate, explicit operator-authorized action; merging this report does not promote.

## 11. Final status

- Phase 5 honest-validation report for the 205-secondary candidate is prepared once this report and its manifest are merged.
- Public promotion remains separately gated and BLOCKED until the operator explicitly authorizes it AND Phase 5G data licensing is separately cleared.

End of report.
