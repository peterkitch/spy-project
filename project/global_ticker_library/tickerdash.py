"""
Yahoo Finance Ticker Validator Dashboard
Interface for discovering and validating Yahoo Finance ticker symbols
"""
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Configure logging before other imports
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)

# Set up rotating file handler for application logs
logger = logging.getLogger("tickerdash")
logger.setLevel(logging.INFO)

# Create logs directory if it doesn't exist
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

# Add rotating file handler (2MB max, keep 3 backups)
fh = RotatingFileHandler(
    str(log_dir / "tickerdash.log"), 
    maxBytes=2_000_000, 
    backupCount=3,
    encoding='utf-8'
)
fh.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
fh.setFormatter(fmt)
logger.addHandler(fh)

# Also add console handler for warnings and errors only
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console_handler)

from dash import Dash, Input, Output, State, dcc, html, callback_context, no_update
import dash_bootstrap_components as dbc
import threading
import uuid
import re

# Ensure the project root (the parent of the package folder) is importable.
PKG_ROOT = Path(__file__).resolve().parents[1]  # .../project
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from global_ticker_library.gl_config import (
    DATA_DIR, MANUAL_FILE, MASTER_FILE, DASH_PORT, DASH_DEBUG, PROGRESS_FILE
)
from global_ticker_library.registry import (
    counts, export_active, init_db, upsert_candidates, 
    upsert_validation_results, get_recent_changes
)
from global_ticker_library.validator_yahoo import validate_symbols

# Initialize database
init_db()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Create Dash app with Bootstrap theme
app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = "Yahoo Finance Ticker Validator"

# === Minimal background job infrastructure for large manual batches ===
# Single-process, thread-based runner with a tiny in-memory progress store.
_JOB_LOCK = threading.Lock()
_JOB = {
    "id": None, "total": 0, "done": 0, "active": 0, "stale": 0, "invalid": 0, "unknown": 0,
    "status": "idle",  # idle|running|done|error|cancelled
    "message": "",
    "rate_limited": 0,
    "timeouts": 0,
    "chunk_size": 500,
    "cooldown_s": 0,
    "pass": 1,
    "gentle": False,
    "two_pass": True
}
_CHUNK_SIZE = 500  # validate+commit in small slices to keep UI responsive
_MIN_CHUNK = 100   # Minimum chunk size when adapting
_MAX_CHUNK = 500   # Maximum chunk size

def _chunked(seq, n):
    """Split sequence into chunks of size n"""
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

def _start_job(tokens, gentle=None, two_pass=True):
    """Initialize progress and spawn a worker thread."""
    with _JOB_LOCK:
        if _JOB.get("status") == "running":
            return False, "A validation job is already running. Click 'Cancel' to stop it."
        
        # Auto-enable gentle mode for large batches
        if gentle is None:
            gentle = len(tokens) > 5000
        
        run_id = str(uuid.uuid4())[:8]
        _JOB.update({
            "id": run_id, "total": len(tokens), "done": 0,
            "active": 0, "stale": 0, "invalid": 0, "unknown": 0,
            "status": "running", "message": "Starting...",
            "rate_limited": 0, "timeouts": 0,
            "chunk_size": 250 if gentle else _CHUNK_SIZE,
            "cooldown_s": 0, "pass": 1,
            "gentle": gentle, "two_pass": two_pass
        })
    
    t = threading.Thread(target=_worker, args=(run_id, tokens, gentle, two_pass), daemon=True)
    t.start()
    return True, run_id

def _snapshot():
    """Get a thread-safe copy of job state"""
    with _JOB_LOCK:
        return dict(_JOB)

def _cancel_job():
    """Request cancellation of running job"""
    with _JOB_LOCK:
        if _JOB.get("status") == "running":
            _JOB["status"] = "cancelled"
            _JOB["message"] = "Cancel requested. Stopping after current batch..."
            return True
        return False

def _worker(run_id, tokens, gentle=False, two_pass=True):
    """Background task with adaptive chunking and two-pass retry."""
    import random
    import time
    import collections
    
    logger.info("Job %s started | symbols=%d gentle=%s two_pass=%s", 
                run_id, len(tokens), gentle, two_pass)
    
    try:
        backoff = 0
        chunk_size = _JOB["chunk_size"]
        retry_pool = collections.deque()
        cooldown_until = 0
        
        # Normalize + de-dupe while preserving order
        seen, ordered = set(), []
        for s in tokens:
            s = s.strip().upper()
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
        
        def apply_cooldown(extra=0):
            nonlocal cooldown_until, backoff
            sleep_s = min(60, max(2, backoff)) + extra + random.randint(0, 2)
            cooldown_until = time.monotonic() + sleep_s
            with _JOB_LOCK:
                _JOB["cooldown_s"] = sleep_s
                _JOB["message"] = f"Cooling down for {sleep_s}s..."
            time.sleep(sleep_s)
            with _JOB_LOCK:
                _JOB["cooldown_s"] = 0
        
        # Two-pass loop
        for pass_no in ([1, 2] if two_pass else [1]):
            with _JOB_LOCK:
                _JOB["pass"] = pass_no
                if pass_no == 2:
                    _JOB["message"] = f"Pass 2: Retrying {len(retry_pool)} unknown symbols"
            
            # Source for this pass
            if pass_no == 1:
                source_symbols = ordered
            else:
                source_symbols = list(retry_pool)
                retry_pool.clear()
            
            if not source_symbols:
                break
            
            # Process chunks
            for chunk_idx, chunk in enumerate(_chunked(source_symbols, chunk_size)):
                # Check cancellation
                with _JOB_LOCK:
                    if _JOB["id"] != run_id or _JOB["status"] == "cancelled":
                        _JOB["message"] = "Cancelled"
                        return
                
                # Apply cooldown if needed
                if time.monotonic() < cooldown_until:
                    wait_time = cooldown_until - time.monotonic()
                    if wait_time > 0:
                        time.sleep(wait_time)
                
                # Upsert candidates
                upsert_candidates(chunk, f"MANUAL:DASH:P{pass_no}")
                
                # Validate with error tracking
                try:
                    results, agg = validate_symbols(chunk, gentle=gentle, progress=False, timeout=10)
                except Exception as e:
                    # If validation completely fails, mark all as unknown
                    results = []
                    agg = {"other": len(chunk)}
                    for sym in chunk:
                        results.append({
                            "symbol": sym,
                            "original": sym,
                            "status": "unknown",
                            "error_code": "other",
                            "error_msg": str(e)[:200],
                            "meta_exists": False,
                            "has_price": False
                        })
                
                # Persist results (now returns 6 values including n_unknown)
                n_act, n_stale, n_inv, n_unk, additions, removals = upsert_validation_results(results)
                
                # Collect unknowns for retry
                for r in results:
                    if r.get("status") == "unknown":
                        retry_pool.append(r["symbol"])
                
                # Update progress
                rate_hits = agg.get("rate_limit", 0)
                timeouts = agg.get("timeout", 0)
                
                with _JOB_LOCK:
                    _JOB["done"] += len(chunk)
                    _JOB["active"] += n_act
                    _JOB["stale"] += n_stale
                    _JOB["invalid"] += n_inv
                    _JOB["unknown"] = len(retry_pool) if pass_no == 1 else n_unk
                    _JOB["rate_limited"] += rate_hits
                    _JOB["timeouts"] += timeouts
                    _JOB["chunk_size"] = chunk_size
                    _JOB["message"] = f"Pass {pass_no}: Processed {_JOB['done']:,}/{_JOB['total']:,}"
                
                # Log chunk summary
                logger.info(
                    "chunk processed | pass=%d size=%d act=%d stale=%d inv=%d unk=%d rate=%d timeouts=%d",
                    pass_no, len(chunk), n_act, n_stale, n_inv, n_unk, rate_hits, timeouts
                )
                
                # Adapt chunk size and backoff based on errors
                if rate_hits > 0 or timeouts > 0:
                    # Increase backoff and reduce chunk size
                    backoff = min(max(2, backoff * 2 if backoff else 2), 60)
                    chunk_size = max(_MIN_CHUNK, chunk_size // 2)
                    with _JOB_LOCK:
                        _JOB["chunk_size"] = chunk_size
                    apply_cooldown()
                else:
                    # Gradually reduce backoff and increase chunk size
                    backoff = max(0, backoff - 1)
                    if backoff == 0 and chunk_size < _MAX_CHUNK:
                        chunk_size = min(_MAX_CHUNK, chunk_size + 50)
                        with _JOB_LOCK:
                            _JOB["chunk_size"] = chunk_size
                
                # Optional gentle mode delay
                if gentle:
                    time.sleep(0.2)
            
            # Break if no unknowns to retry
            if not retry_pool:
                break
        
        # Export only on successful completion
        n_exported = export_active()
        with _JOB_LOCK:
            _JOB["status"] = "done"
            if _JOB["unknown"] > 0:
                _JOB["message"] = f"Exported {n_exported:,} active symbols. {_JOB['unknown']} symbols unknown."
            else:
                _JOB["message"] = f"Exported {n_exported:,} active symbols to {MASTER_FILE.name}"
        
        logger.info("Job %s completed | exported=%d active=%d stale=%d invalid=%d unknown=%d",
                    run_id, n_exported, _JOB["active"], _JOB["stale"], _JOB["invalid"], _JOB["unknown"])
    
    except Exception as e:
        logger.error("Job %s failed | error=%s", run_id, str(e))
        with _JOB_LOCK:
            _JOB["status"] = "error"
            _JOB["message"] = f"Error: {str(e)[:180]}"

def create_cli_progress():
    """Create CLI progress display card"""
    try:
        if PROGRESS_FILE.exists():
            import json
            data = PROGRESS_FILE.read_text(encoding="utf-8")
            p = json.loads(data)
            status = p.get("status", "idle")
            
            if status == "running":
                phase = p.get("phase", "")
                
                # Check for new format
                if "overall_total" in p:
                    # New comprehensive format
                    done = p.get("overall_done", 0)
                    total = p.get("overall_total", 0)
                    message = p.get("message", "CLI run in progress...")
                    progress_pct = p.get("percent_complete", 0)
                    est_time = p.get("estimated_time_remaining", "")
                    
                    # Add time estimate to message if available
                    if est_time and est_time != "calculating...":
                        message = f"{message} - Est. {est_time} remaining"
                else:
                    # Old format compatibility
                    done = p.get("done", 0)
                    total = p.get("total", 0)
                    message = p.get("message", "CLI run in progress...")
                    if total > 0:
                        progress_pct = int((done / total) * 100)
                    else:
                        progress_pct = 0
                
                # Extract error metrics
                rate_limits = p.get("rate_limits", 0)
                timeouts = p.get("timeouts", 0)
                no_price = p.get("no_price_data", 0)
                other_errors = p.get("other_errors", 0)
                
                # For new format, use current_chunk/total_chunks
                if "current_chunk" in p:
                    batch_num = p.get("current_chunk", 0)
                    total_batches = p.get("total_chunks", 0)
                else:
                    batch_num = p.get("batch_number", 0)
                    total_batches = p.get("total_batches", 0)
                
                # Build comprehensive metrics display
                metrics_parts = []
                
                # Show cumulative results if available (new format)
                if "cumulative_active" in p:
                    cum_parts = []
                    cum_active = p.get("cumulative_active", 0)
                    cum_stale = p.get("cumulative_stale", 0)
                    cum_invalid = p.get("cumulative_invalid", 0)
                    cum_unknown = p.get("cumulative_unknown", 0)
                    
                    if cum_active > 0: cum_parts.append(f"✅ {cum_active:,} active")
                    if cum_stale > 0: cum_parts.append(f"⚠️ {cum_stale:,} stale")
                    if cum_invalid > 0: cum_parts.append(f"❌ {cum_invalid:,} invalid")
                    if cum_unknown > 0: cum_parts.append(f"❓ {cum_unknown:,} unknown")
                    
                    if cum_parts:
                        metrics_parts.append("Found: " + ", ".join(cum_parts))
                
                # Show error counts
                error_parts = []
                if rate_limits > 0:
                    error_parts.append(f"🚫 {rate_limits} rate limits")
                if timeouts > 0:
                    error_parts.append(f"⏱️ {timeouts} timeouts")
                if no_price > 0:
                    error_parts.append(f"📉 {no_price} no price")
                if other_errors > 0:
                    error_parts.append(f"❌ {other_errors} errors")
                
                if error_parts:
                    metrics_parts.append("Issues: " + ", ".join(error_parts))
                
                # Show batch progress
                if batch_num > 0 and total_batches > 0:
                    metrics_parts.append(f"📦 Chunk {batch_num}/{total_batches}")
                elif "current_chunk" in p and "total_chunks" in p:
                    metrics_parts.append(f"📦 Chunk {p['current_chunk']}/{p['total_chunks']}")
                
                metrics_display = " | ".join(metrics_parts) if metrics_parts else ""
                
                alert_content = [
                    html.Div([
                        html.Span("🔄 ", style={"fontSize": "1.2em"}),
                        html.B("CLI Running: "),
                        progress_msg
                    ]),
                    dbc.Progress(value=progress_pct if total > 0 else 50, 
                               animated=True, striped=True, className="mt-2")
                ]
                
                if metrics_display:
                    alert_content.append(html.Div(metrics_display, className="mt-2", 
                                                 style={"fontSize": "0.9em", "color": "#666"}))
                
                return dbc.Alert(alert_content, color="info", dismissable=False)
                
            elif status == "complete":
                # Use cumulative results if available (new format)
                if "cumulative_active" in p:
                    active = p.get("cumulative_active", 0)
                    stale = p.get("cumulative_stale", 0)
                    invalid = p.get("cumulative_invalid", 0)
                    unknown = p.get("cumulative_unknown", 0)
                else:
                    # Fall back to old format
                    active = p.get("active", 0)
                    stale = p.get("stale", 0)
                    invalid = p.get("invalid", 0)
                    unknown = p.get("unknown", 0)
                    
                message = p.get("message", "Validation complete")
                
                # Add database status if available
                status_parts = []
                if "db_active" in p:
                    status_parts.append(f"Database now has {p['db_active']:,} active, {p.get('db_unknown', 0):,} unknown")
                
                return dbc.Alert([
                    html.Div([
                        html.Span("✅ ", style={"fontSize": "1.2em"}),
                        html.B("CLI Complete: "),
                        message
                    ]),
                    html.Div([
                        dbc.Badge(f"Processed: {active:,} active", color="success", className="me-1"),
                        dbc.Badge(f"{stale:,} stale", color="warning", className="me-1"),
                        dbc.Badge(f"{invalid:,} invalid", color="danger", className="me-1"),
                        dbc.Badge(f"{unknown:,} unknown", color="secondary", className="me-1"),
                    ], className="mt-2"),
                    html.Div(status_parts[0], className="mt-2", style={"fontSize": "0.9em"}) if status_parts else None
                ], color="success", dismissable=True)
                
            elif status == "error":
                message = p.get("message", "An error occurred")
                return dbc.Alert([
                    html.Span("❌ ", style={"fontSize": "1.2em"}),
                    html.B("CLI Error: "),
                    message
                ], color="danger", dismissable=True)
                
    except Exception:
        pass  # Silently fail if can't read progress
    
    return html.Div()  # Empty div if no progress

def create_stats_cards():
    """Create statistics display cards"""
    import json
    
    # Try to read live counts from progress file first
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE) as f:
                progress = json.load(f)
                if progress.get("status") == "running" and "db_candidates" in progress:
                    # Use live database counts from progress
                    c = {
                        'candidate': progress.get('db_candidates', 0),
                        'active': progress.get('db_active', 0),
                        'stale': progress.get('db_stale', 0),
                        'invalid': progress.get('db_invalid', 0),
                        'unknown': progress.get('db_unknown', 0),
                    }
                else:
                    c = counts()
        else:
            c = counts()
    except:
        c = counts()
    
    if 'unknown' not in c:
        c['unknown'] = 0
    
    return dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H4(f"{c['candidate']:,}", className="text-info"),
                    html.P("Candidates", className="mb-0")
                ])
            ])
        ], width=2),
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H4(f"{c['active']:,}", className="text-success"),
                    html.P("Active Symbols", className="mb-0")
                ])
            ])
        ], width=2),
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H4(f"{c['stale']:,}", className="text-warning"),
                    html.P("Stale Symbols", className="mb-0")
                ])
            ])
        ], width=2),
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H4(f"{c['invalid']:,}", className="text-danger"),
                    html.P("Invalid Symbols", className="mb-0")
                ])
            ])
        ], width=2),
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H4(f"{c.get('unknown', 0):,}", className="text-secondary"),
                    html.P("Unknown Symbols", className="mb-0")
                ])
            ])
        ], width=2),
    ], className="mb-4", justify="center")

def create_manual_input_section():
    """Create manual input interface"""
    return dbc.Card([
        dbc.CardHeader(html.H5("Manual Ticker Input")),
        dbc.CardBody([
            dcc.Textarea(
                id="manual-textarea",
                style={"width": "100%", "height": "150px"},
                placeholder="Enter Yahoo Finance ticker symbol(s) separated by commas, spaces, or newlines"
            ),
            html.Div(className="mt-3"),
            dbc.ButtonGroup([
                dbc.Button("Validate & Export", id="btn-validate", color="primary", size="lg"),
                dbc.Button("Cancel", id="btn-cancel", color="warning", outline=True, size="lg", className="ms-2"),
                dbc.Button("Clear", id="btn-clear", color="secondary", size="lg", className="ms-2"),
            ]),
            dbc.Progress(id="progress-bar", value=0, label="", striped=True, animated=True, className="mt-3", style={"display": "none"}),
            html.Small(id="progress-text", className="text-muted"),
            html.Div(id="validation-status", className="mt-3"),
            dcc.Interval(id="progress-interval", interval=800, n_intervals=0, disabled=True),
        ])
    ])

def create_recent_changes_section():
    """Create recent changes display with scrollable lists"""
    # Fetch up to 1000 recent changes
    changes = get_recent_changes(limit=1000)
    
    # Get the full lists (up to 1000 each)
    additions = changes.get("additions", [])
    removals = changes.get("removals", [])
    
    # Create list items for all symbols
    additions_items = [html.Li(sym, style={"fontSize": "0.9em"}) for sym in additions]
    removals_items = [html.Li(sym, style={"fontSize": "0.9em"}) for sym in removals]
    
    return dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.H6("Recent Additions"),
                    html.Small(f" ({len(additions)} symbols)", className="text-muted")
                ]),
                dbc.CardBody([
                    html.Div([
                        html.Ul(
                            additions_items if additions_items else [html.Li("None")],
                            style={"paddingLeft": "20px", "marginBottom": "0"}
                        )
                    ], style={
                        "height": "250px", 
                        "overflowY": "auto",  # Changed from "hidden" to "auto" for scrollbar
                        "overflowX": "hidden",
                        "border": "1px solid #dee2e6",
                        "borderRadius": "4px",
                        "padding": "10px",
                        "backgroundColor": "#f8f9fa"
                    })
                ])
            ])
        ], width=6),
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.H6("Recent Removals"),
                    html.Small(f" ({len(removals)} symbols)", className="text-muted")
                ]),
                dbc.CardBody([
                    html.Div([
                        html.Ul(
                            removals_items if removals_items else [html.Li("None")],
                            style={"paddingLeft": "20px", "marginBottom": "0"}
                        )
                    ], style={
                        "height": "250px",
                        "overflowY": "auto",  # Changed from "hidden" to "auto" for scrollbar
                        "overflowX": "hidden",
                        "border": "1px solid #dee2e6",
                        "borderRadius": "4px",
                        "padding": "10px"
                    })
                ])
            ])
        ], width=6),
    ])

# App layout
app.layout = dbc.Container([
    dbc.Row([
        dbc.Col([
            html.H1("Yahoo Finance Ticker Validator", className="text-center mb-4"),
            html.Hr(),
        ])
    ]),
    
    # Global validation progress bar (shows when validation is running)
    html.Div(id="global-progress-bar", children=[]),
    
    # CLI Progress (if running)
    html.Div(id="cli-progress", children=create_cli_progress()),
    
    # Statistics cards
    html.Div(id="stats-cards", children=create_stats_cards()),
    
    # Manual input section
    create_manual_input_section(),
    
    html.Hr(className="my-4"),
    
    # Recent changes
    html.Div(id="recent-changes", children=create_recent_changes_section()),
    
    # File locations info
    dbc.Card([
        dbc.CardBody([
            html.H6("Output File:"),
            html.P(f"Master List: {MASTER_FILE}", className="mb-0 small"),
        ])
    ], className="mt-4"),
    
    # Auto-refresh interval
    dcc.Interval(id="refresh-interval", interval=10000, n_intervals=0),  # 10 seconds
    
], fluid=True, className="py-4")

# Callbacks
@app.callback(
    [Output("validation-status", "children"),
     Output("manual-textarea", "value"),
     Output("progress-interval", "disabled"),
     Output("progress-bar", "style")],
    [Input("btn-validate", "n_clicks"),
     Input("btn-clear", "n_clicks")],
    [State("manual-textarea", "value")],
    prevent_initial_call=True
)
def handle_manual_input(validate_clicks, clear_clicks, text):
    """Start async validation job or clear input; keep UI responsive."""
    ctx = callback_context
    if not ctx.triggered:
        return "", text, True, {"display": "none"}

    button_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if button_id == "btn-clear":
        return "", "", True, {"display": "none"}

    if button_id == "btn-validate" and text:
        tokens = re.findall(r"[A-Z0-9.^=\-]+", (text or "").upper())
        tokens = [t.strip().upper() for t in tokens if t and 1 <= len(t) <= 22]

        if not tokens:
            return dbc.Alert("No valid ticker symbols found", color="warning"), text, True, {"display": "none"}

        ok, run_id = _start_job(tokens)
        if not ok:
            return dbc.Alert(run_id, color="warning"), text, True, {"display": "none"}

        return (
            dbc.Alert(f"Started validation job {run_id} for {len(tokens):,} symbols. Progress below...", color="info"),
            "",
            False,  # enable progress polling
            {"display": "block"}  # show progress bar
        )

    return "", text, True, {"display": "none"}

@app.callback(
    [Output("cli-progress", "children"),
     Output("stats-cards", "children"),
     Output("recent-changes", "children"),
     Output("global-progress-bar", "children")],
    Input("refresh-interval", "n_intervals")
)
def refresh_display(n):
    """Refresh CLI progress, statistics, recent changes, and progress bar"""
    import json
    
    # Create progress bar if validation is running
    progress_bar = []
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE) as f:
                p = json.load(f)
                if p.get("status") == "running":
                    # Use new format if available
                    if "percent_complete" in p:
                        percent = p.get("percent_complete", 0)
                        message = p.get("message", "Processing...")
                        est_time = p.get("estimated_time_remaining", "")
                    else:
                        # Old format
                        done = p.get("done", 0)
                        total = p.get("total", 1)
                        percent = (done / total * 100) if total > 0 else 0
                        message = p.get("message", "Processing...")
                        est_time = ""
                    
                    # Build progress label
                    label = f"{percent:.1f}%"
                    if est_time and est_time != "calculating...":
                        label += f" - {est_time} remaining"
                    
                    progress_bar = dbc.Progress(
                        value=percent,
                        label=label,
                        striped=True,
                        animated=True,
                        color="success" if percent >= 75 else "warning" if percent >= 50 else "info",
                        style={"height": "30px", "fontSize": "14px"},
                        className="mb-3"
                    )
    except:
        pass
    
    return create_cli_progress(), create_stats_cards(), create_recent_changes_section(), progress_bar

@app.callback(
    [Output("progress-bar", "value"),
     Output("progress-bar", "label"),
     Output("progress-text", "children"),
     Output("validation-status", "children", allow_duplicate=True),
     Output("progress-interval", "disabled", allow_duplicate=True),
     Output("progress-bar", "style", allow_duplicate=True)],
    Input("progress-interval", "n_intervals"),
    prevent_initial_call=True
)
def poll_progress(_):
    """Poll background job progress and update the progress bar."""
    snap = _snapshot()
    total = max(1, snap.get("total", 1))
    done = min(snap.get("done", 0), total)
    pct = int(done * 100 / total) if total else 0
    label = f"{pct}%"
    
    # Build comprehensive progress text
    txt_parts = [
        f"Processed {done:,}/{total:,}",
        f"Active: {snap.get('active',0)}",
        f"Stale: {snap.get('stale',0)}",
        f"Invalid: {snap.get('invalid',0)}",
        f"Unknown: {snap.get('unknown',0)}"
    ]
    
    # Add diagnostic info if relevant
    if snap.get('rate_limited', 0) > 0:
        txt_parts.append(f"Rate limited: {snap.get('rate_limited',0)}")
    if snap.get('timeouts', 0) > 0:
        txt_parts.append(f"Timeouts: {snap.get('timeouts',0)}")
    if snap.get('cooldown_s', 0) > 0:
        txt_parts.append(f"Cooldown: {snap.get('cooldown_s',0)}s")
    if snap.get('chunk_size', 0) != _CHUNK_SIZE:
        txt_parts.append(f"Chunk: {snap.get('chunk_size',_CHUNK_SIZE)}")
    if snap.get('pass', 1) > 1:
        txt_parts.append(f"Pass: {snap.get('pass',1)}")
    
    txt = " • ".join(txt_parts)

    if snap.get("status") in ("done", "error", "cancelled"):
        color = "success" if snap["status"] == "done" else ("warning" if snap["status"] == "cancelled" else "danger")
        
        # Add Retry Unknown button if there are unknowns
        if snap["status"] == "done" and snap.get("unknown", 0) > 0:
            msg = dbc.Alert([
                html.Div(snap.get("message", snap["status"].title())),
                html.Hr(),
                dbc.Button(f"Retry {snap.get('unknown', 0)} Unknown Symbols", 
                          id="btn-retry-unknown", color="info", size="sm")
            ], color=color, dismissable=True)
        else:
            msg = dbc.Alert(snap.get("message", snap["status"].title()), color=color, dismissable=True)
        
        return pct, label, txt, msg, True, {"display": "none"}  # stop polling and hide progress bar

    # still running
    return pct, label, txt, no_update, False, {"display": "block"}

@app.callback(
    [Output("validation-status", "children", allow_duplicate=True),
     Output("progress-interval", "disabled", allow_duplicate=True)],
    Input("btn-cancel", "n_clicks"),
    prevent_initial_call=True
)
def cancel_job(n_clicks):
    """Request cancellation of the running job."""
    if not n_clicks:
        return no_update, no_update
    if _cancel_job():
        return dbc.Alert("Cancel requested. The job will stop after the current batch.", color="warning"), False
    return dbc.Alert("No active job to cancel.", color="secondary"), True

@app.callback(
    [Output("validation-status", "children", allow_duplicate=True),
     Output("progress-interval", "disabled", allow_duplicate=True),
     Output("progress-bar", "style", allow_duplicate=True)],
    Input("btn-retry-unknown", "n_clicks"),
    prevent_initial_call=True
)
def retry_unknown(n_clicks):
    """Retry unknown symbols from previous run."""
    if not n_clicks:
        return no_update, no_update, no_update
    
    # Get unknown symbols from database
    from global_ticker_library.registry import get_symbols_by_status
    unknown_symbols = get_symbols_by_status("unknown", limit=100000)
    
    if not unknown_symbols:
        return dbc.Alert("No unknown symbols to retry.", color="info"), True, {"display": "none"}
    
    # Start job with gentle mode for retries
    ok, run_id = _start_job(unknown_symbols, gentle=True, two_pass=False)
    
    if not ok:
        return dbc.Alert(run_id, color="warning"), True, {"display": "none"}
    
    return (
        dbc.Alert(f"Started retry job {run_id} for {len(unknown_symbols):,} unknown symbols.", color="info"),
        False,  # enable progress polling
        {"display": "block"}  # show progress bar
    )

def main():
    """Run the dashboard"""
    print(f"\n{'='*60}")
    print("Starting Yahoo Finance Ticker Validator Dashboard")
    print(f"{'='*60}")
    print(f"Dashboard URL: http://127.0.0.1:{DASH_PORT}")
    print("Press Ctrl+C to stop")
    print(f"{'='*60}\n")
    
    app.run_server(debug=DASH_DEBUG, port=DASH_PORT, host="127.0.0.1")

if __name__ == "__main__":
    main()