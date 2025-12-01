# Confluence Dashboard UX Improvements - Implementation Guide

**Date**: 2025-11-13
**Status**: Partially Implemented
**Scope**: User experience enhancements for the confluence multi-timeframe dashboard

## Summary

This document tracks the implementation of user-focused improvements to make the confluence dashboard more interpretable and actionable.

## Completed Improvements ✅

### 1. Heatmap Signal Labels with Zoom Controls
**Status**: ✅ IMPLEMENTED
**Files Modified**: `confluence.py`

**Changes Made**:
- Updated `_signals_heatmap_matrix()` to return both numeric values (Z) and text labels (TXT)
- Modified hovertemplate to show "Buy"/"Short"/"None" instead of numeric values (0/0.5/1)
- Added regime-shift markers (vertical dotted lines) when Buy/Short state flips
- Added quick zoom buttons (3M, 6M, 1Y, Max)
- Added rangeslider for easy date range selection
- Added rangebreaks to skip weekends
- Added spike lines on hover for better cross-referencing

**User Benefit**: Users can now instantly see what each signal means without decoding numbers, and can quickly zoom to relevant time periods.

## Pending Improvements 🔄

### 2. Sample-Aware Fallback and Color-Coded KPI Tiles
**Status**: 📋 READY TO IMPLEMENT
**Priority**: HIGH

**Problem**: When using exact tier matching (e.g., "Strong Buy"), cohorts can be thin, leading to unreliable statistics.

**Solution**: Auto-fallback to broader cohorts when sample size is too small.

#### Implementation Steps:

**A. Update `_expected_stats_from_state` function**:

```python
def _expected_stats_from_state(price: pd.Series, conf_df: pd.DataFrame, today: pd.Timestamp,
                               state_key: str = 'tier', horizons=(5,20,60),
                               min_samples: int = 40, fallback: str = 'dir') -> dict:
    """
    Build forward-return cohort using all past dates with SAME state as today.
    If sample size < min_samples, fallback to broader cohort (e.g., 'dir').
    """
    today = pd.to_datetime(today)
    if getattr(today, 'tz', None) is not None:
        today = today.tz_localize(None)

    def _align_bucket(x):
        return '>=75' if x >= 75 else '50-75' if x >= 50 else '<50'

    # Prepare optional bucket if needed later
    conf_df = conf_df.copy()
    conf_df['align_bucket'] = conf_df['alignment_pct'].apply(_align_bucket)

    chosen_key = state_key
    state_today = conf_df.loc[today, chosen_key]
    mask = conf_df[chosen_key].eq(state_today)

    # Fallback if too few samples
    if mask.sum() < min_samples:
        if fallback == 'dir' and 'dir' in conf_df.columns:
            chosen_key = 'dir'
            state_today = conf_df.loc[today, chosen_key]
            mask = conf_df[chosen_key].eq(state_today)
        elif 'align_bucket' in conf_df.columns:
            chosen_key = 'align_bucket'
            state_today = conf_df.loc[today, chosen_key]
            mask = conf_df[chosen_key].eq(state_today)

    fr = _forward_returns(price, horizons=horizons)
    cohorts = {h: fr.loc[mask, h].dropna() for h in fr.columns}

    stats = {}
    for h, ser in cohorts.items():
        if ser.empty:
            stats[h] = {'N': 0, 'Win%': 0.0, 'Mean': 0.0, 'Median': 0.0,
                        'P05': 0.0, 'P25': 0.0, 'P75': 0.0, 'P95': 0.0}
            continue
        stats[h] = {
            'N': int(len(ser)),
            'Win%': round(float((ser > 0).mean() * 100.0), 2),
            'Mean': round(float(ser.mean()), 2),
            'Median': round(float(ser.median()), 2),
            'P05': round(float(ser.quantile(0.05)), 2),
            'P25': round(float(ser.quantile(0.25)), 2),
            'P75': round(float(ser.quantile(0.75)), 2),
            'P95': round(float(ser.quantile(0.95)), 2),
        }

    return {
        'effective_key': chosen_key,
        'state_value': state_today,
        'by_horizon': stats,
        'cohorts': cohorts,
        'sample_size': int(next(iter(stats.values()))['N']) if stats else 0
    }
```

**B. Update `create_expected_kpis` function**:

```python
def create_expected_kpis(exp: dict) -> html.Div:
    """
    KPI row for F+5 / F+20 / F+60 with sample-aware color coding.
    Green: N>=200; Yellow: 40<=N<200; Red: N<40. Border hue follows Median sign.
    """
    def _tile_style(s):
        N = s['N']; med = s['Median']
        base = '#2a2a2a'
        if N < 40:
            border = '#cc3300'
        elif N < 200:
            border = '#cccc00'
        else:
            border = '#2e8b57'
        # Turn border slightly red/green by median sign
        border = '#2e8b57' if med >= 0 else '#cc3300' if N >= 40 else border
        return {'backgroundColor': base, 'border': f'2px solid {border}',
                'borderRadius': '8px', 'padding': '12px'}

    tiles = []
    for h in ['F+5', 'F+20', 'F+60']:
        s = exp['by_horizon'][h]
        tiles.append(
            html.Div([
                html.H5(h, style={'color': '#80ff00', 'marginBottom': '4px'}),
                html.P(f"State: {exp['state_value']} ({exp['effective_key']})",
                       style={'color': '#aaa', 'margin': 0, 'fontSize': '12px'}),
                html.P(f"N: {s['N']}", style={'color': '#aaa', 'margin': 0}),
                html.P(f"Win%: {s['Win%']}%", style={'color': '#ccc', 'margin': 0}),
                html.P(f"Median: {s['Median']}%", style={'color': '#ccc', 'margin': 0}),
                html.P(f"[P05, P95]: [{s['P05']}%, {s['P95']}%]",
                       style={'color': '#888', 'margin': 0, 'fontSize': '12px'}),
            ], style=_tile_style(s))
        )

    return html.Div([
        html.H4("Expected Performance (cohorts auto‑broaden if samples are thin)",
                style={'color': '#80ff00', 'marginTop': '30px', 'marginBottom': '10px'}),
        html.Div(tiles, style={'display': 'grid',
                               'gridTemplateColumns': 'repeat(3, 1fr)', 'gap': '12px'})
    ])
```

**C. Update call in `create_confluence_performance_panel`**:

```python
# Change from:
exp = _expected_stats_from_state(price.reindex(conf_df.index).ffill(), conf_df, today, state_key='tier')

# To:
exp = _expected_stats_from_state(
    price.reindex(conf_df.index).ffill(), conf_df, today,
    state_key='tier', min_samples=40, fallback='dir'
)
```

**User Benefit**: Stable statistics even when exact tier matches are rare; visual confidence indicators via border colors.

---

### 3. Active Pair SMA Overlays on Individual Charts
**Status**: 📋 READY TO IMPLEMENT
**Priority**: MEDIUM

**Problem**: Users can't see WHY a particular bar is Buy/Short without mentally calculating SMAs.

**Solution**: Add dotted SMA lines showing the active pair that generated each signal.

#### Implementation:

In `create_individual_chart()`, after adding the main price and capture traces, add:

```python
# After the two main traces are added and subtitle computed:
if not is_virtual:
    try:
        sma_a, sma_b, pair_label = _build_dynamic_active_pair_smas(library, close)
        fig.add_trace(go.Scatter(
            x=close.index, y=sma_a, name='Active SMA A',
            line=dict(width=1, dash='dot', color='#ffaa00'), opacity=0.8, yaxis='y1',
            customdata=pair_label,
            hovertemplate='<b>%{x|%Y-%m-%d}</b><br>Pair: %{customdata}<br>SMA A: %{y:.2f}<extra></extra>'
        ))
        fig.add_trace(go.Scatter(
            x=close.index, y=sma_b, name='Active SMA B',
            line=dict(width=1, dash='dot', color='#ff66aa'), opacity=0.8, yaxis='y1',
            customdata=pair_label,
            hovertemplate='<b>%{x|%Y-%m-%d}</b><br>Pair: %{customdata}<br>SMA B: %{y:.2f}<extra></extra>'
        ))
    except Exception:
        pass  # Graceful degradation if dynamic pairs unavailable
```

**User Benefit**: Direct visual confirmation of which SMA pair is driving each signal; helps build intuition about the strategy.

---

### 4. Loading Spinner and Color-Coded Diagnostics
**Status**: 📋 READY TO IMPLEMENT
**Priority**: MEDIUM

**Problem**: Large renders feel unresponsive; library status table requires reading text to understand state.

**Solution**: Add loading indicators and color-code table cells.

#### Implementation Steps:

**A. Wrap results container in loading spinner**:

In `app.layout`, replace:

```python
# Results Section
html.Div(id='results-container', style={'maxWidth': '1400px', 'margin': '0 auto'}),
```

With:

```python
# Results Section with loading indicator
dcc.Loading(
    id='analyze-loading',
    type='default',
    children=html.Div(id='results-container',
                      style={'maxWidth': '1400px', 'margin': '0 auto'})
),
```

**B. Add color coding to MP diagnostics table**:

In the `dash_table.DataTable(id='mp-library-matrix-table', ...)`, add to the existing parameters:

```python
style_data_conditional=[
    # Greenish for OK
    {'if': {'filter_query': 'contains({1d}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
    {'if': {'filter_query': 'contains({1wk}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
    {'if': {'filter_query': 'contains({1mo}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
    {'if': {'filter_query': 'contains({3mo}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
    {'if': {'filter_query': 'contains({1y}, "[OK]")'}, 'backgroundColor': '#1f3a1f'},
    # Reddish for STALE
    {'if': {'filter_query': 'contains({1d}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
    {'if': {'filter_query': 'contains({1wk}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
    {'if': {'filter_query': 'contains({1mo}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
    {'if': {'filter_query': 'contains({3mo}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
    {'if': {'filter_query': 'contains({1y}, "[STALE]")'}, 'backgroundColor': '#3a1f1f'},
]
```

**C. Add one-click copy for build commands**:

Right after the `html.Pre(id='mp-build-commands', ...)`, add:

```python
dcc.Clipboard(
    target_id='mp-build-commands',
    title='Copy',
    style={'float': 'right', 'marginTop': '-32px', 'marginRight': '6px'}
)
```

**User Benefit**: Visual feedback during long operations; instant recognition of library status; easy copy-paste of build commands.

---

## Optional Future Enhancements (Backlog)

### Auto-Apply Toggle
Add a checkbox next to "Run Multi-Primary Analysis" that automatically triggers "Apply to Analyze" when the run completes.

### Preset Primary Sets
Add local storage to save/load favorite primary ticker combinations with their invert/mute settings.

### User-Controlled Min Active Frames
Expose a slider (1–5, default 2) for `min_active` parameter in confluence calculations, allowing users to adjust strictness.

### Export Functionality
Add buttons to export current confluence state, KPI stats, or master chart as PNG/CSV.

## Testing Checklist

After implementing each improvement:

- [ ] Verify heatmap hovers show "Buy"/"Short"/"None" correctly
- [ ] Test zoom buttons (3M, 6M, 1Y, Max) on master panel
- [ ] Confirm regime-shift markers appear at state changes
- [ ] Check KPI tile border colors reflect sample size
- [ ] Verify cohort fallback triggers for thin samples
- [ ] Confirm SMA overlays appear on individual charts (non-virtual mode)
- [ ] Test loading spinner appears during analysis
- [ ] Verify diagnostics table cells are color-coded
- [ ] Test clipboard copy button for build commands

## Implementation Priority

1. **Heatmap improvements** ✅ - DONE
2. **Sample-aware KPIs** - HIGH (data quality/confidence)
3. **Loading spinner** - MEDIUM (UX polish)
4. **SMA overlays** - MEDIUM (educational value)
5. **Diagnostics colors** - LOW (nice-to-have)

## Notes

- All changes maintain backward compatibility
- No database or schema changes required
- Performance impact is minimal (all calculations already performed)
- Changes are localized to UI/presentation layer
