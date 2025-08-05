DBG=True
from QuantConnect import Chart, Series, SeriesType
from AlgorithmImports import *
import pandas as pd
import numpy as np
from datetime import timedelta, datetime
from io import StringIO
from collections import deque

class _LogCollector:
    def __init__(self, algo, daily_cap: int = 300):
        self._a    = algo
        self._buf  = []
        self._cap  = daily_cap

    def note(self, msg: str):
        if len(self._buf) < self._cap:          # hard cap / day
            self._buf.append(msg)

    def dump(self, tag: str = "SUMMARY"):
        if self._buf:
            self._a.Debug(f"[{tag}] " + " | ".join(self._buf[: self._cap]))
        self._buf.clear()

class SymbolData:
    def __init__(self, algorithm, symbol):
        self.algorithm = algorithm
        self.symbol = symbol
        self.MAX_SMA_DAY = algorithm.MAX_SMA_DAY
        self.closes = deque(maxlen=self.MAX_SMA_DAY)
        self.returns = deque(maxlen=self.MAX_SMA_DAY)
        self.sma_n = np.zeros(self.MAX_SMA_DAY)
        self.sma_yesterday = np.zeros(self.MAX_SMA_DAY)
        self.grid = np.zeros((self.MAX_SMA_DAY, self.MAX_SMA_DAY))
        self.initialized = False
        self.warmup_complete = False
        self.cumulative_capture = 0.0
        self.signal_for_tomorrow = "None"
        # Add persistent cumulative arrays
        self.pairs = None
        self.buy_cumulative = None
        self.short_cumulative = None
        self.current_position = "None"
        self._yesterday_signal = "None"
        self.top_buy_pair = None
        self.top_short_pair = None
        self.buy_threshold = None
        self.short_threshold = None
        self.daily_top_buy_pairs = {}
        self.daily_top_short_pairs = {}
        self.in_universe = True 
        self.daily_signal_returns = deque(maxlen=252) 
        self.trade_flags = deque(maxlen=252)
        self.full_signal_returns = []  
        self.full_trade_flags = [] 
        self.last_trade_date = None  
        self.last_price = None 
        self.stale_days = 0  
        self.MAX_STALE_DAYS = 10  
        self.pre_1998_loaded = False
        self._checked_legacy = False  # Track if we've attempted CSV download
                    
    def _load_pre_1998_data(self):
        if self.algorithm.LiveMode:
            return

        def _try_download(url: str) -> str | None:
            try:
                txt = self.algorithm.Download(url)
                return txt if txt and not txt.startswith("404") else None
            except Exception:
                return None

        tic_raw = self.symbol.Value
        tic_git = tic_raw.replace('.', '-').replace('/', '-') 
        tic_sq  = tic_raw.lower().replace('/', '.').replace('-', '.') + ".us"
        urls = [
            f"https://raw.githubusercontent.com/peterkitch/"
            f"qc-historical-data/main/data/csv/{tic_git}_pre1998.csv",
            f"https://stooq.com/q/d/l/?s={tic_sq}&i=d"
        ]

        csv_content = next(filter(None, (_try_download(u) for u in urls)), None)
        if csv_content is None:            
            return

        try:
            import pandas as _pd
            from io import StringIO as _SIO

            df = _pd.read_csv(_SIO(csv_content))
            date_col = 'Date' if 'Date' in df.columns else df.columns[0]
            df[date_col] = _pd.to_datetime(df[date_col], errors='coerce')
            df = df.dropna(subset=[date_col]).sort_values(date_col)

            df = df[df[date_col] < '1998-01-01']
            if df.empty:
                return

            close_col = next(
                (c for c in ('Adj Close', 'adj close', 'Close', 'close') if c in df.columns),
                df.columns[-1]                               
            )
            closes = df[close_col].astype(float).tolist()

            for close in closes:
                self.closes.append(close)
                self.returns.append(
                    ((close / self.closes[-2] - 1.0) * 100.0)
                    if len(self.closes) > 1 and self.closes[-2] != 0 else 0.0
                )

            self.pre_1998_loaded = True
            self._bootstrap_from_history()
            # Ensure first live day has the correct prior-day signal
            self._yesterday_signal = self.signal_for_tomorrow

        except Exception as err:
            pass
            
    def _bootstrap_from_history(self):
        history_closes = list(self.closes) 
        history_returns = list(self.returns)
        
        self.closes.clear()
        self.returns.clear()
        self.sma_n[:] = 0.0
        self.sma_yesterday[:] = 0.0
        self.grid[:] = 0.0
        
        # Clear signal history arrays before reconstruction
        self.daily_signal_returns.clear()
        self.trade_flags.clear()
        self.full_signal_returns.clear()
        self.full_trade_flags.clear()
        
        class SimpleBar:
            def __init__(self, close, time):
                self.Close = close
                self.Time = time
                self.EndTime = time

        base_time = self.algorithm.Time - timedelta(days=len(history_closes))
        prev_signal = "None"  # Initialize signal tracker
        
        for idx, close in enumerate(history_closes):
            bar_time = base_time + timedelta(days=idx)
            fake_bar = SimpleBar(close, bar_time)
            
            # Set yesterday's signal so update() can properly calculate returns
            self._yesterday_signal = prev_signal
            self.update(fake_bar)
            
            # Store today's signal for tomorrow's iteration
            prev_signal = self.signal_for_tomorrow

        self.warmup_complete = len(self.closes) >= self.MAX_SMA_DAY
        # Remove the backfill call - no longer needed
    
    def update(self, bar):
        if (self.warmup_complete and
            hasattr(self.algorithm, '_date_filter') and self.algorithm._date_filter):
            start_d, end_d = self.algorithm._date_filter
            if bar.Time.date() < start_d or bar.Time.date() > end_d:
                return
        
        close_today = bar.Close

        if self.last_price is not None:
            if abs(close_today - self.last_price) < 1e-9:
                self.stale_days += 1
            else:
                self.stale_days = 0  
        self.last_price = close_today
        
        if self.stale_days >= self.MAX_STALE_DAYS and self.in_universe:
            self.in_universe = False
            self.algorithm.FlagForPermanentRemoval(self.symbol)
            return 

        if self.closes:
            prev = self.closes[-1]
            r = (close_today / prev - 1.0) * 100.0 if prev else 0.0
        else:
            r = 0.0

        old_return = self.returns[0] if len(self.returns) == self.MAX_SMA_DAY else 0.0
        self.closes.append(close_today)
        self.returns.append(r)
        
        self._update_smas(close_today)
        
        if not self.initialized and len(self.closes) >= 2:
            self.initialized = True
            # Initialize persistent arrays
            self._initialize_cumulative_arrays()

        if not self.warmup_complete and len(self.closes) >= 2:
            self.warmup_complete = True

        if self.initialized and len(self.closes) > 1:
            self._update_capture_grid(r, old_return)
            self._calculate_signals(bar.Time.date())
            
    def _update_smas(self, close_today):
        if len(self.closes) > 1:
            self.sma_yesterday = self.sma_n.copy()
            # Debug SMA state update timing
            if self.algorithm.DebugMode and self.symbol.Value == "VIK" and len(self.closes) <= 5:
                sma1_str = f"{self.sma_n[1]:.4f}" if len(self.sma_n) > 1 else "NA"
                self.algorithm.Debug(
                    f"[SMA-UPD-M] {self.algorithm.Time.date()} VIK "
                    f"closes_len={len(self.closes)} last_close={close_today:.4f} "
                    f"SMA[0]={self.sma_n[0]:.4f} SMA[1]={sma1_str}"
                )

        # --- use cumulative‑sum approach to MATCH single‑ticker math exactly ---
        closes_array = np.array(list(self.closes))
        cumsum = np.cumsum(np.insert(closes_array, 0, 0.0))
        max_k = min(len(self.closes), self.MAX_SMA_DAY)

        for k in range(1, max_k + 1):
            self.sma_n[k-1] = (cumsum[len(self.closes)] - cumsum[len(self.closes) - k]) / k
                
    def _initialize_cumulative_arrays(self):
        """Initialize the persistent cumulative arrays matching single-ticker format"""
        max_window = self.MAX_SMA_DAY
        self.pairs = np.array(
            [(i, j) for i in range(1, max_window+1) for j in range(1, max_window+1) if i != j],
            dtype=int
        )
        self.buy_cumulative = np.zeros(len(self.pairs))
        self.short_cumulative = np.zeros(len(self.pairs))
    
    def _update_capture_grid(self, new_return, old_return):
        if len(self.closes) < 2:
            return           

        w = min(len(self.closes) - 1, self.MAX_SMA_DAY)

        sma_y  = self.sma_yesterday[:w]
        diff   = sma_y[:, None] - sma_y[None, :] 
        buy_m  = diff > 0
        short_m = diff < 0
        np.fill_diagonal(buy_m,   False)         
        np.fill_diagonal(short_m, False)

        g = self.grid[:w, :w]                       

        g[buy_m]   += new_return
        g[short_m] -= new_return

        if len(self.returns) == self.MAX_SMA_DAY:
            g[buy_m]   -= old_return
            g[short_m] += old_return
            
        # Update persistent cumulative arrays
        if self.pairs is not None and len(self.closes) > 1:
            # Get yesterday's SMAs for signal determination
            i_indices = self.pairs[:, 0] - 1
            j_indices = self.pairs[:, 1] - 1
            
            # Use yesterday's SMAs (before today's update)
            if w > 1:
                yesterday_sma_i = self.sma_yesterday[i_indices]
                yesterday_sma_j = self.sma_yesterday[j_indices]
                
                # Only update pairs that are ready
                valid_mask = (i_indices < w) & (j_indices < w)
                buy_mask = valid_mask & (yesterday_sma_i > yesterday_sma_j)
                short_mask = valid_mask & (yesterday_sma_i < yesterday_sma_j)
                
                self.buy_cumulative[buy_mask] += new_return
                self.short_cumulative[short_mask] += -new_return
                            
    def _calculate_signals(self, trading_date):
        w = min(len(self.closes), self.MAX_SMA_DAY)
        if w < 2:
            return

        sma_now = self.sma_n[:w]
        diff    = sma_now[:, None] - sma_now[None, :]       
        buy_m   = diff > 0
        short_m = diff < 0
        np.fill_diagonal(buy_m,   False)
        np.fill_diagonal(short_m, False)

        cap_slice = self.grid[:w, :w]
        
        # Use persistent cumulative arrays
        if self.pairs is None:
            return
            
        # Only consider pairs that are ready
        ready_mask = (self.pairs[:, 0] <= w) & (self.pairs[:, 1] <= w)
        
        # Use the same pair selection logic as single-ticker
        best_buy_idx, best_short_idx = self._pick_best_pair(self.buy_cumulative, self.short_cumulative, self.pairs)
        
        best_buy_pair = tuple(self.pairs[best_buy_idx])
        best_short_pair = tuple(self.pairs[best_short_idx])
        best_buy_val = self.buy_cumulative[best_buy_idx]
        best_short_val = self.short_cumulative[best_short_idx]

        self.top_buy_pair   = best_buy_pair
        self.top_short_pair = best_short_pair
        self.daily_top_buy_pairs[trading_date]   = (best_buy_pair,  best_buy_val)
        self.daily_top_short_pairs[trading_date] = (best_short_pair, best_short_val)

        # Debug window contents before threshold calculation
        if self.algorithm.DebugMode and self.symbol.Value == "VIK":
            n1b, n2b = best_buy_pair
            n1s, n2s = best_short_pair
            max_window = max(n1b, n2b, n1s, n2s)
            lookback = list(self.closes)[-max_window:] if len(self.closes) >= max_window else list(self.closes)
            self.algorithm.Debug(
                f"[CHK-M] {trading_date} VIK closes_len={len(self.closes)} "
                f"last_{max_window}={[f'{x:.4f}' for x in lookback[-5:]]}... "
                f"buy_pair={best_buy_pair} short_pair={best_short_pair}"
            )

        self.buy_threshold   = self._calculate_crossing_price(*best_buy_pair)
        self.short_threshold = self._calculate_crossing_price(*best_short_pair)

        # Apply epsilon offset to avoid threshold ties (match single-ticker logic)
        if (self.buy_threshold is not None and 
            self.short_threshold is not None and
            self.buy_threshold <= self.short_threshold):
            
            mid = 0.5 * (self.buy_threshold + self.short_threshold)
            buf = mid * 0.00001  # 0.001%
            self.short_threshold = mid - buf
            self.buy_threshold = mid + buf

        n1b, n2b = best_buy_pair
        n1s, n2s = best_short_pair

        # Require a *profitable* pair (cum‑capture > 0) exactly as the single‑ticker
        buy_sig   = (
            len(self.closes) >= max(n1b, n2b)
            and self.sma_n[n1b-1] > self.sma_n[n2b-1]
            and best_buy_val   > 0           # positive cumulative capture
        )
        short_sig = (
            len(self.closes) >= max(n1s, n2s)
            and self.sma_n[n1s-1] < self.sma_n[n2s-1]
            and best_short_val > 0           # positive cumulative capture
        )

        if buy_sig and short_sig:
            self.signal_for_tomorrow = "Buy" if best_buy_val >= best_short_val else "Short"
        elif buy_sig:
            self.signal_for_tomorrow = "Buy"
        elif short_sig:
            self.signal_for_tomorrow = "Short"
        else:
            self.signal_for_tomorrow = "None"

        self.cumulative_capture = max(best_buy_val, best_short_val)

        # === DEBUG =========================================================
        if self.algorithm.DebugMode and self.symbol.Value in ("VIK", "UBER", "PLTR"):
            self.algorithm.Debug(
                f"[SIG] {trading_date} {self.symbol.Value} "
                f"BuyCap={best_buy_val:.6f} ShortCap={best_short_val:.6f} "
                f"TopBuy={best_buy_pair} TopShort={best_short_pair}"
            )
        # ====================================================================

        if len(self.returns) > 1:
            tr = self.returns[-1]
            signal_return = tr if self._yesterday_signal == "Buy" else -tr if self._yesterday_signal == "Short" else 0.0
            self.daily_signal_returns.append(signal_return)
            self.trade_flags.append(self._yesterday_signal != "None")
            self.full_signal_returns.append(signal_return)
            self.full_trade_flags.append(self._yesterday_signal != "None")
        else:
            self.daily_signal_returns.append(0.0)
            self.trade_flags.append(False)
            self.full_signal_returns.append(0.0)
            self.full_trade_flags.append(False)
        
        # _yesterday_signal is now updated centrally in OnEndOfDay (see OnePassSafe.OnEndOfDay)

        if self.signal_for_tomorrow in ("Buy", "Short"):
            self.last_trade_date = trading_date

        # ── first‑30‑day SMA diagnostics ──────────────────────────────────
        # Disabled for now - uncomment to debug specific symbols
        # if (self.symbol.Value == "COIN" and            # limit noise
        #     trading_date >= datetime(2021,4,14).date() and
        #     hasattr(self.algorithm, "_debug_sma_state")):
        #     self.algorithm._debug_sma_state(trading_date, self)
    
    def _calculate_crossing_price(self, n1, n2):
        if n1 == n2 or len(self.closes) < max(n1, n2):
            return None
            
        closes_list = list(self.closes)
        sum1 = sum(closes_list[-(n1-1):]) if n1 > 1 else 0
        sum2 = sum(closes_list[-(n2-1):]) if n2 > 1 else 0
        denom = n2 - n1
        
        if denom == 0:
            return None
            
        price = (n1 * sum2 - n2 * sum1) / denom
        
        # === DEBUG =========================================================
        if self.algorithm.DebugMode and self.symbol.Value in ("VIK", "UBER", "PLTR"):
            sma1 = self.sma_n[n1-1] if n1 <= len(self.sma_n) else float("nan")
            sma2 = self.sma_n[n2-1] if n2 <= len(self.sma_n) else float("nan")
            self.algorithm.Debug(
                f"[X-PX] {self.algorithm.Time.date()} {self.symbol.Value} "
                f"({n1},{n2}) SMA1={sma1:.6f} SMA2={sma2:.6f} → Thr={price:.10f}"
            )
        # ====================================================================
        
        return price if price > 0 else None
    
    def _pick_best_pair(self, buy_array, short_array, pairs, default_pair=None):
        """Match the single-ticker pair selection logic exactly"""
        if default_pair is None:
            default_pair = (self.MAX_SMA_DAY, self.MAX_SMA_DAY - 1)

        day_len = len(self.closes)
        ready_mask = (pairs[:, 0] <= day_len) & (pairs[:, 1] <= day_len)

        def _choose(cum_arr, is_buy):
            mask = ready_mask & (cum_arr > 0.0)
            if mask.any():
                return self._best_pair_index(cum_arr, pairs, mask)

            span_ready = (pairs[:, 0] + pairs[:, 1]).copy()
            span_ready[~ready_mask] = -1
            if (span_ready > 0).any():
                max_span = span_ready.max()
                cands = np.where(span_ready == max_span)[0]
                return int(max(cands, key=lambda k: (pairs[k][0], pairs[k][1])))

            hits = np.where(
                (pairs[:, 0] == default_pair[0]) &
                (pairs[:, 1] == default_pair[1])
            )[0]
            if len(hits):
                return int(hits[0])

            max_span = (pairs[:, 0] + pairs[:, 1]).max()
            cands = np.where((pairs[:, 0] + pairs[:, 1]) == max_span)[0]
            return int(max(cands, key=lambda k: (pairs[k][0], pairs[k][1])))

        buy_idx = _choose(buy_array, True)
        short_idx = _choose(short_array, False)
        
        return buy_idx, short_idx
    
    def _best_pair_index(self, cum_array, pairs, ready_mask=None):
        """Match the single-ticker best pair index logic"""
        if ready_mask is None:
            ready_mask = np.ones(len(pairs), dtype=bool)

        if ready_mask.any() and np.nanmax(cum_array[ready_mask]) > 0:
            mask = ready_mask
        else:
            mask = ready_mask & (cum_array > 0)

        if not mask.any():
            max_span = (pairs[:,0] + pairs[:,1]).max()
            candidates = np.where(pairs[:,0] + pairs[:,1] == max_span)[0]
            return int(max(candidates, key=lambda k: (pairs[k][0], pairs[k][1])))
        
        sub = np.where(mask)[0]
        max_val = cum_array[sub].max()
        candidates = sub[cum_array[sub] == max_val]
        
        if len(candidates) == 1:
            return int(candidates[0])
        
        return int(max(candidates,
                       key=lambda k: (pairs[k][0] + pairs[k][1],
                                      pairs[k][0],
                                      pairs[k][1])))
    
    def _backfill_signal_history(self):
        """Reconstruct historical signal returns for proper Sharpe calculation"""
        if len(self.closes) < 2:
            return
            
        # Start with clean signal history
        temp_yesterday_signal = "None"
        
        # Process each historical day
        for day_idx in range(1, len(self.closes)):
            # Yesterday's signal determines today's return capture
            tr = self.returns[day_idx]
            
            if temp_yesterday_signal == "Buy":
                sig_ret = tr
            elif temp_yesterday_signal == "Short":
                sig_ret = -tr
            else:
                sig_ret = 0.0
                
            # Store signal returns
            self.daily_signal_returns.append(sig_ret)
            self.trade_flags.append(temp_yesterday_signal != "None")
            self.full_signal_returns.append(sig_ret)
            self.full_trade_flags.append(temp_yesterday_signal != "None")
            
            # Update signal for next iteration
            # Use the already-calculated signal_for_tomorrow from the update() call
            temp_yesterday_signal = self.signal_for_tomorrow

class OnePassSafe(QCAlgorithm):

    TRADE_LEAD_MINUTES = 1
    
    def Initialize(self):
        self.multi_ticker_mode = self.GetParameter("multi_mode", "false").lower() == "true"
        
        raw_symbol = self.GetParameter("symbol", "").strip()
        if not raw_symbol and not self.multi_ticker_mode:

            raise ArgumentException(
                "Parameter 'symbol' must be supplied in the QC parameter panel "
                "when multi_mode = false"
            )

        self.primary_ticker = (raw_symbol or "SPY").upper()
        
        self.MAX_SMA_DAY = int(self.GetParameter("MAX_SMA_DAY", "114"))
        self.TARGET_WEIGHT = float(self.GetParameter("target_weight", "0.375"))
        self.target_gross_exposure = float(self.GetParameter("target_gross_exposure", "0.375"))
        self.bid_ask_max_spread_bps = float(self.GetParameter("bid_ask_max_spread_bps", "20"))
        self.reverse_all = self.GetParameter("reverse_all", "false").lower() == "true"
        self.coarse_interval = int(self.GetParameter("coarse_interval_days", "1"))
        self.leader_cutoff = int(self.GetParameter("leader_pool_size", "1000"))
        self.min_dollar_volume = float(self.GetParameter("min_dollar_volume", "1000000"))
        self._grace_days = int(self.GetParameter("grace_days", "3"))
        self._last_good_tick = {}  # Symbol → date of last pass
        self.sort_by = self.GetParameter("sort_by", "sharpe_ratio").lower().strip()
        if self.sort_by not in {"sharpe_ratio", "cumulative_capture", "running_sharpe", "win_ratio", "weighted_win_ratio", "avg_cap"}:
            self.sort_by = "sharpe_ratio"
        self.trade_recency_days = int(self.GetParameter("trade_recency_days", "90"))
        self.block_shorts = self.GetParameter("block_shorts", "false").lower() == "true"
        self.block_buys = self.GetParameter("block_buys", "false").lower() == "true"
        self.DebugMode = self.GetParameter("verbose", "false").lower() == "true"
        self._log = _LogCollector(self)
        self._last_coarse_date = None
        self._cached_coarse = []
        self.min_price = float(self.GetParameter("min_price", "0.50"))
        self.max_price = float(self.GetParameter("max_price", "20000"))
        self.min_win_obs = int(self.GetParameter("min_win_obs", "15"))
        # Configurable minimum observations for Sharpe ratio calculation
        self.min_sharpe_obs = int(self.GetParameter("min_sharpe_obs", "10"))
        # Bayesian prior parameters (virtual trades)
        self.bayes_alpha = float(self.GetParameter("bayes_alpha", "20"))
        self.bayes_beta = float(self.GetParameter("bayes_beta", "20"))

        # Position rebalancing with configurable drift tolerance
        position_rebalance_raw = (self.GetParameter("position_rebalance", "0") or "0").lower().strip()
        self.position_rebalance_days = 0 if position_rebalance_raw in {"0", "false", ""} else int(position_rebalance_raw)
        self._last_rebalance_date = None
        self.rebalance_tolerance = float(self.GetParameter("rebalance_tol", "0.15"))  # Exposed as parameter
        # Lifetime quality gate parameters
        self.lifetime_quality_gate = self.GetParameter("lifetime_quality_gate", "false").lower() == "true"
        self.lifetime_min_trades = int(self.GetParameter("lifetime_min_trades", "50"))
        self.lifetime_pf_min = float(self.GetParameter("lifetime_pf_min", "1.2"))
        self.lifetime_sharpe_min = float(self.GetParameter("lifetime_sharpe_min", "0.0"))
        self.load_legacy_data = self.GetParameter("load_legacy", "false").lower() == "true"

        self._date_filter = None
        self.backfill_days = int(self.GetParameter("backfill_days", "0"))
        date_range = self.GetParameter("date_range", "false").strip().lower()
        if date_range and date_range != "false":
            try:
                start_s, end_s = date_range.split(":")
                start_d = datetime.strptime(start_s.strip(), "%Y-%m-%d").date()
                end_d = datetime.strptime(end_s.strip(), "%Y-%m-%d").date()
                if end_d < start_d:
                    start_d, end_d = end_d, start_d
                self._date_filter = (start_d, end_d)
                # Start algorithm earlier to get historical data for warmup
                start_pad = start_d - timedelta(days=self.backfill_days)
                self.SetStartDate(start_pad.year, start_pad.month, start_pad.day)
                self.SetEndDate(end_d.year, end_d.month, end_d.day)
                self.Debug(f"[DATE_RANGE] Active: {start_d} to {end_d} (with {self.backfill_days} day backfill)")
            except Exception as err:
                self.Debug(f"[DATE_RANGE] Error parsing '{date_range}': {err}")
        
        if self.multi_ticker_mode:
            self.symbol_data = {}             
            self.leader_symbols = set()       
            self._daily_consolidators = {}     
            self._schedule_symbol     = None  
            self._pending_cleanup = []
            # ── SMA‑debug helpers ────────────────────────────────
            self._coin_start_date = None   # first trading day we see COIN
            self._coin_day_count  = 0     
            
            ban_raw = (self.GetParameter("ban") or "").upper()
            self.banned_ticks = {t.strip() for t in ban_raw.split(",") if t.strip()}

            self.static_universe = self.GetParameter("static_universe", "")
            self.static_tickers = []

            if self.static_universe and self.static_universe.lower() != "false":
                self.static_tickers = [t.strip().upper() 
                                    for t in self.static_universe.split(",") 
                                    if t.strip()]
                
                # use the smaller of (a) the user's desired portfolio size
                # (b) the number of static tickers supplied
                user_port_size = int(self.GetParameter("portfolio_size", "120"))
                self.portfolio_size = max(1, min(user_port_size, len(self.static_tickers)))
            else:
                self.portfolio_size = max(1, int(self.GetParameter("portfolio_size", "120")))
                
            def _fmt_dollars(val: float) -> str:
                if val >= 1e9: return f"${val/1e9:.1f}B"
                if val >= 1e6: return f"${val/1e6:.1f}M"
                if val >= 1e3: return f"${val/1e3:.0f}K"
                return f"${val:.0f}"

            dv_txt = _fmt_dollars(self.min_dollar_volume)
            price_txt = f"${self.min_price:g}-${self.max_price:g}"

            self.Debug(f"[UNIVERSE_CRITERIA] Portfolio={self.portfolio_size}, "
                    f"Leader pool={self.leader_cutoff}, Update every {self.coarse_interval} days, "
                    f"MinDV={dv_txt}, Price={price_txt}")
            if self.static_universe:
                if self.static_tickers:
                    self.Debug(f"[STATIC_UNIVERSE] Using tickers: {self.static_tickers}")
                
            self.position_size_per_symbol = self.target_gross_exposure / self.portfolio_size

        if self._date_filter is None:
            if self.multi_ticker_mode or not self.primary_ticker.endswith(("USD","USDT")):
                self.SetStartDate(1998, 1, 1)
            else:
                self.SetStartDate(2010, 1, 1)
            
        self.SetCash(100_000)
        self.SetTimeZone("America/New_York")
        self.Settings.DailyPreciseEndTime = True

        if not self.multi_ticker_mode:
            self.daily_closes = []
            self.daily_returns = []
            self.cumulative_combined_capture_value = 0.0
            self.capture_ex_ante = 0.0
            self.active_position_for_today = "None"
            self.daily_top_buy_pairs = {}
            self.daily_top_short_pairs = {}
            self.historical_positions = {}
            self.current_position = "None"
            self.trade_count = 0
            self._trade_day_count = 0
            self.yesterdays_buy_pair = None
            self.yesterdays_short_pair = None
            self._yesterday_signal = "None"
            self.initialized = False
            self.warmup_complete = False
            self.data_transition_date = self.StartDate
            self.pre_1998_loaded = False
            self.actual_start_date = self.data_transition_date
            self.csv_rows_loaded = 0
            # Add date-based logging helpers
            self._st_log_start = datetime(2021, 4, 14).date()
            self._st_first_seen = None

        if self.multi_ticker_mode:

            self.UniverseSettings.Resolution = Resolution.Daily
            self.UniverseSettings.DataNormalizationMode = DataNormalizationMode.Adjusted
            self.UniverseSettings.FilterFineData = True # 
            self.UniverseSettings.FilterNonTradableSymbols = True 
            self.AddUniverse(self.CoarseSelectionFunction)
            self.SetBenchmark("SPY")
            self.initialized = True
            self.warmup_complete = True
        else:
            if self.primary_ticker.endswith(("USD", "USDT")):
                sec = self.AddCrypto(self.primary_ticker, Resolution.Minute, Market.Coinbase)
                self.symbol = sec.Symbol
                sec.SetLeverage(1)
                self.SetBenchmark(self.symbol)
            else:
                qc_ticker = "SPY" if self.primary_ticker == "SPX" else self.primary_ticker
                # Use minute resolution so the manual TradeBarConsolidator receives data
                sec = self.AddEquity(qc_ticker, Resolution.Minute)
                self.symbol = sec.Symbol
                sec.SetLeverage(1)
                sec.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
                self.SetBenchmark("SPY")

            self.Securities[self.symbol].PlotPriceSeries = False
            self.LoadHistoricalData()    
            self.WarmUpArraysAfterLoad()     

            self.buy_threshold = None
            self.short_threshold = None
            self.top_buy_pair = None
            self.top_short_pair = None
            self.signal_source_pair = None

        if not self.multi_ticker_mode:
            chart = Chart(f"{self.primary_ticker} Daily Close")
            chart.AddSeries(Series("Close", SeriesType.Line, "$", color=Color.Blue))
            self.AddChart(chart)

            capture_chart = Chart("Cumulative Combined Capture %")
            capture_chart.AddSeries(Series("Combined Capture", SeriesType.Line, "%", color=Color.Green))
            capture_chart.AddSeries(Series("ExAnte", SeriesType.Line, "%", color=Color.Blue))
            self.AddChart(capture_chart)

        equity_chart = Chart("Portfolio Value")
        equity_chart.AddSeries(Series("Total Value", SeriesType.Line, "$", color=Color.Orange))
        self.AddChart(equity_chart)
        
        if self.multi_ticker_mode:

            spy_sec               = self.AddEquity("SPY", Resolution.Daily)
            self._schedule_symbol = spy_sec.Symbol    
            
            self.Schedule.On(
                self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 2), 
                self.RankUniverse
            )
            
            self.Schedule.On(
                self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 1),
                self.EvaluateAndTradeMulti
            )
            
            # Force daily consolidators to emit at 15:59 (match Project 9)
            self.Schedule.On(
                self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 1),
                lambda: [c.Scan(self.Time) for c in self._daily_consolidators.values() if hasattr(c, 'Scan')]
            )
            
        else:

            if self.primary_ticker.endswith(("USD", "USDT")):

                self.Consolidate(self.symbol, Resolution.Daily, self.OnDailyBar)
                self.Schedule.On(
                    self.DateRules.EveryDay(self.symbol),
                    self.TimeRules.BeforeMarketClose(self.symbol, self.TRADE_LEAD_MINUTES),
                    self.EvaluateAndTrade
                )
            else:

                # Use manual consolidator to match Project 9's 15:59 approach
                from QuantConnect.Data.Consolidators import TradeBarConsolidator
                
                self.dailyConsolidator = TradeBarConsolidator(timedelta(days=1))
                self.dailyConsolidator.DataConsolidated += lambda s, bar: self.OnDailyBar(bar)
                self.SubscriptionManager.AddConsolidator(self.symbol, self.dailyConsolidator)
                
                def _on_close_bar():
                    self.EvaluateAndTrade()             
                    self.EmitConsolidatorBar()         

                self.Schedule.On(
                    self.DateRules.EveryDay(self.symbol),
                    self.TimeRules.BeforeMarketClose(self.symbol, 1), 
                    _on_close_bar
                )

    def CoarseSelectionFunction(self, coarse):
        if self.static_tickers:

            symbols = []
            for ticker in dict.fromkeys(self.static_tickers):
                try:

                    if not self.Securities.ContainsKey(ticker):
                        # Need minute resolution for the manual daily consolidator
                        equity = self.AddEquity(ticker, Resolution.Minute)
                        equity.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
                        symbols.append(equity.Symbol)

                        self.Debug(f"[STATIC_UNIVERSE] Added {ticker} to universe")
                    else:

                        for kvp in self.Securities:
                            if kvp.Key.Value == ticker:
                                symbols.append(kvp.Key)
                                break
                except Exception as err:
                    self.Debug(f"[UNIVERSE] Could not add {ticker}: {err}")

            if not getattr(self, "_static_universe_logged", False):
                self.Debug(f"[STATIC_UNIVERSE] Created universe with {len(symbols)} symbols: {[s.Value for s in symbols]}")
                self._static_universe_logged = True

            return symbols

        if not self.multi_ticker_mode:
            return []

        if self._last_coarse_date is not None and self._cached_coarse:
            days = (self.Time.date() - self._last_coarse_date).days
            if days < self.coarse_interval:
                return self._cached_coarse
        
        min_price = self.min_price
        max_price = self.max_price
        
        # Dynamic dollar volume based on year
        yr = self.Time.year
        if self.min_dollar_volume > 0:
            min_dv = self.min_dollar_volume          # user-supplied constant
        else:                                         # dynamic legacy rule
            if   yr < 2000: min_dv = 5_000_000
            elif yr < 2002: min_dv = 3_000_000
            elif yr < 2004: min_dv = 2_000_000
            else:           min_dv = 1_000_000
        
        min_year_for_fundamentals = 2004
        need_fundamentals = self.Time.year >= min_year_for_fundamentals
        today = self.Time.date()
        
        survivors = []
        for c in coarse:
            # Check if banned
            if c.Symbol.Value in self.banned_ticks:
                continue
                
            # Check if passes all criteria today
            if ((not need_fundamentals or c.HasFundamentalData)
                and min_price < c.Price < max_price
                and c.DollarVolume >= min_dv):
                # Passed today - update last good date
                self._last_good_tick[c.Symbol] = today
                survivors.append(c)
                continue
            
            # Didn't pass today - check if within grace period
            last_ok = self._last_good_tick.get(c.Symbol)
            if last_ok and (today - last_ok).days <= self._grace_days:
                survivors.append(c)
        
        # Sort by dollar volume
        survivors.sort(key=lambda c: c.DollarVolume, reverse=True)
        
        # Hard cap at 2X leader pool to prevent excessive downloads
        max_universe = self.leader_cutoff * 2 if self.leader_cutoff > 0 else 600
        selection = [c.Symbol for c in survivors[:max_universe]]
        
        if selection:
            self._cached_coarse = selection
            self._last_coarse_date = self.Time.date()
            self.Debug(f"[UNIVERSE_UPDATE] {self.Time:%Y-%m-%d} Selected {len(selection)} symbols "
                      f"(min_price=${min_price}, max_price=${max_price}, min_dv=${min_dv/1e6:.1f}M)")
        else:
            self.Debug(f"[UNIVERSE] {self.Time:%Y-%m-%d} coarse slice empty; will retry next day")

        return selection
    
    def OnSecuritiesChanged(self, changes):
        if not self.multi_ticker_mode:
            for security in changes.AddedSecurities:
                if security.Symbol == self.symbol:
                    self._last_position_qty = 0
            return
        
        # Collect all new symbols first
        added_symbols = []
        for security in changes.AddedSecurities:
            symbol = security.Symbol
            if symbol.Value in self.banned_ticks:
                continue
            if symbol in self.symbol_data:
                self.symbol_data[symbol].in_universe = True
                continue
            self.symbol_data[symbol] = SymbolData(self, symbol)
            added_symbols.append(symbol)
            
            # Add/refresh minute subscription immediately (deduplicates logic with _ensure_minute_subscription)
            self._ensure_minute_subscription(symbol)
        
        # Batch fetch history for ALL new symbols at once
        if added_symbols:
            try:
                # Match single-ticker history loading for parity
                bars_needed = self.MAX_SMA_DAY + 6  # Exactly match single-ticker
                hist = self.History(added_symbols, bars_needed, Resolution.Daily)
                
                if not hist.empty:
                    # Process each symbol's data from the batch result
                    for symbol in added_symbols:
                        if symbol in hist.index.levels[0]:
                            symbol_hist = hist.loc[symbol].sort_index()
                            
                            # Temporarily disable date filter for historical load
                            original_filter = self._date_filter
                            self._date_filter = None
                            
                            # Vectorized extraction for performance – keep EXACT parity with single‑ticker
                            close_col = None
                            for col in ["Adj Close", "adj close", "Close", "close"]:
                                if col in symbol_hist.columns:
                                    close_col = col
                                    break
                            if close_col is None:
                                self.Debug(f"[HISTORY] ERROR: Close column not found for {symbol.Value} "
                                           f"in {list(symbol_hist.columns)} – skipping symbol")
                                continue
                            closes = symbol_hist[close_col].astype(float).values
                            # Debug probe #1
                            if self.DebugMode:
                                self.Debug(f"[HIST COL] {symbol.Value} using '{close_col}' cols={list(symbol_hist.columns)}")
                            times = symbol_hist.index.to_pydatetime() if hasattr(symbol_hist.index, 'to_pydatetime') else list(symbol_hist.index)
                            
                            bar_count = 0
                            for close_price, time_val in zip(closes, times):
                                if close_price > 0:
                                    bar = type("Bar", (), {
                                        "Close": close_price,
                                        "Time": time_val,
                                        "EndTime": time_val
                                    })()
                                    self.symbol_data[symbol].update(bar)
                                    bar_count += 1
                            
                            self._date_filter = original_filter

                            if bar_count >= 2:
                                self._log.note(f"Batch load: {symbol.Value} got {bar_count} bars")
                                # Debug probe #2
                                if self.DebugMode and symbol.Value == "VIK":
                                    v = closes[:5] if len(closes) >= 5 else closes
                                    self.Debug(f"[SNAP] {symbol.Value} first5={','.join(f'{x:.4f}' for x in v)}")
                                    # Extra validation‑print to confirm parity with single‑ticker
                                    if len(closes) >= 2 and closes[0] != 0:
                                        first_ret = (closes[1] / closes[0] - 1) * 100
                                        self.Debug(f"[VALIDATE] {symbol.Value} first_return={first_ret:.4f}%")
                            
                            # Reset symbol state after loading
                            data = self.symbol_data[symbol]
                            data.current_position = "None"
                            data._prev_signal = "None"
                            
                            # Load pre-1998 data immediately for proper ranking
                            if self.load_legacy_data and not data.pre_1998_loaded and not data._checked_legacy:
                                data._checked_legacy = True
                                data._load_pre_1998_data()
                                
            except Exception as err:
                self.Debug(f"[BATCH HISTORY] Error: {str(err)[:200]}")

        # Setup consolidators for all added symbols
        for symbol in added_symbols:
            if symbol not in self._daily_consolidators:
                # Use manual consolidator to match Project 9's 15:59 daily close
                from QuantConnect.Data.Consolidators import TradeBarConsolidator
                consolidator = TradeBarConsolidator(timedelta(days=1))
                consolidator.DataConsolidated += lambda s, bar, sym=symbol: self.OnSymbolData(sym, bar)
                self.SubscriptionManager.AddConsolidator(symbol, consolidator)
                self._daily_consolidators[symbol] = consolidator
        
        # Handle removed securities
        for security in changes.RemovedSecurities:
            symbol = security.Symbol
            if symbol in self.symbol_data:
                self.symbol_data[symbol].in_universe = False
            if self.Securities.ContainsKey(symbol):
                self._pending_cleanup.append(symbol)
    
    def OnSymbolData(self, symbol, bar):
        if symbol in self.symbol_data:
            # Debug probe #3
            if self.DebugMode and symbol.Value == "VIK":
                self.Debug(f"[CONS] {bar.EndTime.date()} VIK 15:59={bar.Close:.4f}")
                self.symbol_data[symbol].update(bar)
    
    def RankUniverse(self) -> None:
        if getattr(self, '_last_rank', None) == self.Time.date():
            return     
        self._last_rank = self.Time.date()
        
        total_symbols = len(self.symbol_data)
        
        active_symbols = [sym for sym in self.symbol_data if self.Securities.ContainsKey(sym)]
        ready_cnt = sum(self.symbol_data[sym].warmup_complete for sym in active_symbols)
        

        if len(self.static_tickers) == 1:
            min_required = 1  
        else:
            min_required = max(3, int(len(active_symbols) * 0.2))  
        
        if ready_cnt < min_required:
            self.leader_symbols = set()

            if self.Time.day == 1:
                self._log.note(f"Warmup incomplete: {ready_cnt}/{len(active_symbols)} ready, need {min_required}")
            return

        table = []
        
        # Quick pre-filter to avoid calculating metrics for all symbols
        preliminary_candidates = []
        for sym, d in self.symbol_data.items():
            if sym == self._schedule_symbol or not (d.warmup_complete and d.in_universe):
                continue
            if d.last_trade_date is not None and (self.Time.date() - d.last_trade_date).days > self.trade_recency_days:
                continue
            preliminary_candidates.append((sym, d))
        
        # If we have way more candidates than needed, do a quick pre-sort
        if len(preliminary_candidates) > self.leader_cutoff * 2:
            # Quick sort by cumulative capture
            preliminary_candidates.sort(key=lambda x: x[1].cumulative_capture, reverse=True)
            preliminary_candidates = preliminary_candidates[:self.leader_cutoff * 2]
        
        # Pre-calculate universe mean for avg_cap empirical Bayes
        universe_avgs = []
        if self.sort_by == "avg_cap":
            for sym, d in preliminary_candidates:
                sig_rets = np.array(d.daily_signal_returns)[np.array(d.trade_flags)]
                min_obs = 5 if len(self.static_tickers) == 1 else self.min_win_obs
                if sig_rets.size >= min_obs:
                    universe_avgs.append(sig_rets.mean())
            
        for sym, d in preliminary_candidates:
            single_static = len(self.static_tickers) == 1
            # ────────────────── RUNNING‑SHARPE ──────────────────
            if self.sort_by == "running_sharpe":
                # Use ALL returns, not just trading days
                all_rets = np.array(d.full_signal_returns) / 100.0
                min_obs = 5 if single_static else 60
                if len(all_rets) < min_obs:
                    metric_value = 0.0 if single_static else float("-inf")
                else:
                    rf_daily = 0.05 / 252
                    mu = all_rets.mean()
                    sigma = all_rets.std(ddof=1)
                    metric_value = np.sqrt(252) * (mu - rf_daily) / sigma if sigma > 0 else float("-inf")

            # ───────────────────── WIN‑RATIO / WEIGHTED‑WIN‑RATIO ─────────────────────
            elif self.sort_by in {"win_ratio", "weighted_win_ratio"}:
                sig_rets = np.array(d.daily_signal_returns)[np.array(d.trade_flags)]
                min_obs = 5 if single_static else self.min_win_obs
                
                if sig_rets.size < min_obs:
                    metric_value = 0.0 if single_static else float("-inf")
                else:
                    wins = int((sig_rets > 0).sum())
                    losses = int((sig_rets < 0).sum())
                    
                    if self.sort_by == "win_ratio":
                        # Simple win ratio - no Bayesian shrinkage
                        metric_value = wins / (wins + losses) if (wins + losses) > 0 else 0.0
                    else:  # weighted_win_ratio
                        # Keep Bayesian shrinkage for weighted version
                        alpha = self.bayes_alpha + wins
                        beta = self.bayes_beta + losses
                        post_mean = alpha / (alpha + beta)
                        
                        # Apply profit factor weighting
                        avg_win = sig_rets[sig_rets > 0].mean() if wins else 0.0
                        avg_loss = abs(sig_rets[sig_rets < 0].mean()) if losses else 1.0
                        pf = avg_win / avg_loss if avg_loss > 0 else avg_win
                        metric_value = post_mean * pf
                        
            # ───────────────────── AVG_CAP ─────────────────────
            elif self.sort_by == "avg_cap":
                sig_rets = np.array(d.daily_signal_returns)[np.array(d.trade_flags)]
                min_obs = 5 if single_static else self.min_win_obs
                
                if sig_rets.size < min_obs:
                    metric_value = 0.0 if single_static else float("-inf")
                else:
                    raw_avg = sig_rets.mean()
                    
                    # Use pre-calculated universe mean for empirical Bayes
                    if len(universe_avgs) >= 10:
                        prior_mean = np.mean(universe_avgs)
                        # Shrinkage factor: more observations = less shrinkage
                        shrinkage = sig_rets.size / (sig_rets.size + 30)
                        metric_value = shrinkage * raw_avg + (1 - shrinkage) * prior_mean
                    else:
                        # Not enough symbols - mild shrink toward zero
                        shrinkage = sig_rets.size / (sig_rets.size + 30)
                        metric_value = shrinkage * raw_avg
                        
                    # Optional: Apply lifetime quality gate
                    if self.lifetime_quality_gate:
                        lifetime_rets = np.array(d.full_signal_returns)[np.array(d.full_trade_flags)]
                        if lifetime_rets.size >= self.lifetime_min_trades:
                            # Calculate lifetime profit factor
                            lifetime_wins = lifetime_rets[lifetime_rets > 0]
                            lifetime_losses = lifetime_rets[lifetime_rets < 0]
                            
                            if lifetime_losses.size > 0:
                                lifetime_pf = lifetime_wins.sum() / abs(lifetime_losses.sum())
                            else:
                                lifetime_pf = float('inf') if lifetime_wins.size > 0 else 0
                                
                            # Calculate lifetime Sharpe
                            lifetime_sharpe = (lifetime_rets.mean() / lifetime_rets.std(ddof=1) * 
                                            np.sqrt(252)) if lifetime_rets.std() > 0 else 0
                            
                            # Apply quality gate
                            if lifetime_pf < self.lifetime_pf_min or lifetime_sharpe < self.lifetime_sharpe_min:
                                metric_value = float("-inf")  # Exclude from leaders

            # ───────────────────── SHARPE‑RATIO ─────────────────────
            else:  # sharpe_ratio
                sig_rets = np.array(d.daily_signal_returns)[np.array(d.trade_flags)]
                min_obs = 5 if single_static else self.min_sharpe_obs
                
                if sig_rets.size >= min_obs:
                    rets = sig_rets / 100.0
                    rf_daily = 0.05 / 252
                    mu = rets.mean()
                    sigma = rets.std(ddof=1)
                    metric_value = np.sqrt(252) * (mu - rf_daily) / sigma if sigma > 0 else float("-inf")
                else:
                    # Use cumulative capture as fallback for insufficient observations
                    metric_value = d.cumulative_capture

            table.append((sym, metric_value, d.cumulative_capture, d.signal_for_tomorrow))


        if self.sort_by == "cumulative_capture":
            table.sort(key=lambda x: (x[2], x[1]), reverse=True)
        else:
            # Better tie-breaking: metric, then capture, then symbol name
            table.sort(key=lambda x: (x[1], x[2], x[0].Value), reverse=True)

        IS_SHARPE_LIKE = {"sharpe_ratio", "running_sharpe", "win_ratio", "weighted_win_ratio", "avg_cap"}

        leader_pool = [
            sym for sym, metric, capture, _ in table
            if (metric > float("-inf") if self.sort_by in IS_SHARPE_LIKE
                else capture > 0)
        ][: self.leader_cutoff if self.leader_cutoff > 0 else None]
        
        # Safety net: if no leaders selected with Sharpe, fall back to cumulative capture
        if not leader_pool and self.sort_by in IS_SHARPE_LIKE:
            table.sort(key=lambda x: (x[2], x[0].Value), reverse=True)  # sort by capture
            leader_pool = [
                sym for sym, _, capture, _ in table if capture > 0
            ][: self.leader_cutoff if self.leader_cutoff > 0 else None]
        
        leaders = leader_pool[: self.portfolio_size]

        # Diagnostic for win_ratio gaps
        if self.DebugMode and self.sort_by in {"win_ratio", "weighted_win_ratio"}:
            valid_metrics = sum(m > float('-inf') for _, m, _, _ in table)
            self.Debug(f"[{self.Time.date()}] metric>-inf: {valid_metrics}/{len(table)} | leaders: {len(leaders)}")

        self.leader_symbols = set(leaders)
        
        # In multi-ticker backtests we skip the expensive pre-1998 bootstrap
        # It can still be used in single-ticker mode where performance is fine
        # Pre-1998 data now loaded in OnSecuritiesChanged for all symbols
        
        # Skip monthly debug print for single-ticker runs
        if len(self.leader_symbols) == 1:
            return
            
        if self.Time.day == 1 and len(table) > 0:
            eligible = leader_pool if 'leader_pool' in locals() else []
            in_cont = len(eligible)
            
            if self.sort_by == "cumulative_capture":
                top_5_data = sorted(
                    [(sym, capture) for sym, _, capture, _ in table],
                    key=lambda x: x[1],
                    reverse=True
                )[:5]
                leader_info = [f"{sym.Value}({capture:.2f}%)" for sym, capture in top_5_data]
                metric_tag = "CumCapture"
            else:
                top_5_data = sorted(
                    [(sym, metric) for sym, metric, _, _ in table],
                    key=lambda x: x[1],
                    reverse=True
                )[:5]
                leader_info = [f"{sym.Value}({metric:.2f})" for sym, metric in top_5_data]
                metric_tag = {
                    "running_sharpe": "RunSharpe",
                    "sharpe_ratio": "Sharpe", 
                    "win_ratio": "WinRatio",
                    "weighted_win_ratio": "WeightedWR",
                    "avg_cap": "AvgCap"
                }.get(self.sort_by, "Metric")
            
            date_tag = self.Time.strftime("%Y-%m")
            self.Debug(
                f"[{date_tag}] Leaders N={in_cont} ({metric_tag}): " + ", ".join(leader_info)
            )

    def _ensure_minute_subscription(self, sym):
        cfg = self.SubscriptionManager.SubscriptionDataConfigService.GetSubscriptionDataConfigs(sym)
        minute_cfgs = [c for c in cfg if c.Resolution == Resolution.Minute]
        if not minute_cfgs:
            # brand-new minute feed – make sure it is *Adjusted*
            sec = self.AddEquity(sym.Value, Resolution.Minute)
            sec.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        else:
            # minute feed already exists - ensure it's using adjusted mode
            # Remove the old subscription and add a new one with correct settings
            self.RemoveSecurity(sym)
            sec = self.AddEquity(sym.Value, Resolution.Minute)
            sec.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
            # Re-add daily subscription too
            daily_sec = self.AddEquity(sym.Value, Resolution.Daily)
            daily_sec.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
    
    def EvaluateAndTradeMulti(self) -> None:
        # Diagnostic to verify _yesterday_signal state
        if self.DebugMode and self.Time.date() <= datetime(2021,4,20).date():
            for sym in self.leader_symbols:
                if sym in self.symbol_data:
                    data = self.symbol_data[sym]
                    self.Debug(f"[PRE-TRADE] {self.Time:%Y-%m-%d} {sym.Value} _yesterday_signal={data._yesterday_signal}")
        
        if not self.leader_symbols:
            return

        if not all(self.symbol_data[s].warmup_complete for s in self.leader_symbols):
            return

        for sym in list(self.leader_symbols):
            data = self.symbol_data[sym]

            sec = self.Securities[sym]
            
            # --- get the identical 15:59 price that single‑ticker uses ---
            hist = self.History(sym, 1, Resolution.Minute)

            if self.DebugMode and sym.Value in ("VIK", "UBER", "PLTR"):
                self.Debug(
                    f"[M-MIN] {self.Time.date()} {sym.Value} hist_15:59={hist.iloc[-1]['close'] if not hist.empty else 'NA'}"
                )
                
            if hist.empty:
                minute_price = sec.Close  # fallback to daily close
            else:
                minute_price = float(hist.iloc[-1]['close'])
            if minute_price <= 0:
                continue
            # Debug probe #4
            if self.DebugMode and sym.Value == "VIK":
                self.Debug(f"[M-PX] {self.Time.date()} VIK minPrice={minute_price:.4f}")
            
            if not sec.IsTradable:
                if self.Time.day == 1:
                    self._log.note(f"Skip non-tradable {sym.Value}")
                continue

            if not self._check_spread(sym):
                continue

            # Use the signal calculated from yesterday's close for today's trading
            plan = data._yesterday_signal
            
            # Match single-ticker logic EXACTLY: just follow the plan without threshold validation
            sig = plan if plan in ("Buy", "Short") else "None"
            
            if self.reverse_all:
                sig = "Buy" if sig == "Short" else "Short" if sig == "Buy" else "None"
            if (sig == "Short" and self.block_shorts) or (sig == "Buy" and self.block_buys):
                sig = "None"
                
            if sig == "Buy":
                target =  self.position_size_per_symbol
            elif sig == "Short":
                target = -self.position_size_per_symbol
            else:                                    
                target = 0.0

            cur_qty = self.Portfolio[sym].Quantity
            # Debug probe #5
            if self.DebugMode and sym.Value == "VIK":
                self.Debug(f"[THR] {self.Time.date()} plan={plan} sig={sig} "
                        f"buyTh={data.buy_threshold} shortTh={data.short_threshold}")

            if sig == "None":
                if cur_qty != 0:
                    self.Liquidate(sym, tag="Liquidated")
            else:
                need_flip = (cur_qty > 0 and sig == "Short") or (cur_qty < 0 and sig == "Buy") or cur_qty == 0
                if need_flip:
                    if cur_qty != 0:
                        self.Liquidate(sym, tag="Liquidated")
                    self.SetHoldings(
                        sym, target,
                        tag=f"{sig} from {data.top_buy_pair if sig=='Buy' else data.top_short_pair}"
                    )
                    
            # Get the specific capture values for each pair to match single-ticker logging
            # Use the bar date (which is what _calculate_signals uses as the key)
            trading_date = self.Time.date()
            if trading_date in data.daily_top_buy_pairs:
                buy_cap = data.daily_top_buy_pairs[trading_date][1]
            else:
                buy_cap = 0.0
            if trading_date in data.daily_top_short_pairs:
                short_cap = data.daily_top_short_pairs[trading_date][1]
            else:
                short_cap = 0.0
            self._log_daily(
                sym=sym,
                price=minute_price,
                plan=plan,
                sig=sig,
                y_sig=getattr(data, '_yesterday_signal', 'None'),
                t_sig=data.signal_for_tomorrow,
                buy_pair=data.top_buy_pair,
                buy_cap=buy_cap,
                short_pair=data.top_short_pair,
                short_cap=short_cap
            )
            
            # Signal already updated in _calculate_signals for all symbols

        # Periodic position rebalancing (only for drifted positions with valid signals)
        if self.position_rebalance_days > 0:
            if (self._last_rebalance_date is None or 
                (self.Time.date() - self._last_rebalance_date).days >= self.position_rebalance_days):
                
                rebalance_count = 0
                for sym in self.leader_symbols:
                    if sym == self._schedule_symbol or not self.Portfolio[sym].Invested:
                        continue
                    
                    data = self.symbol_data.get(sym)
                    if not data:
                        continue
                    
                    # Check if signal is still valid (prevents resize-then-liquidate)
                    sig = data.signal_for_tomorrow
                    if self.reverse_all:
                        sig = "Buy" if sig == "Short" else "Short" if sig == "Buy" else "None"
                    if (sig == "Short" and self.block_shorts) or (sig == "Buy" and self.block_buys):
                        sig = "None"
                    if sig == "None":
                        continue  # Skip rebalance if position will be liquidated
                    
                    # Calculate current vs target weight
                    current_value = self.Portfolio[sym].HoldingsValue
                    total_value = self.Portfolio.TotalPortfolioValue
                    current_weight = current_value / total_value if total_value > 0 else 0
                    
                    # Target based on signal (not current position)
                    target_weight = self.position_size_per_symbol if sig == "Buy" else -self.position_size_per_symbol
                    
                    # Only rebalance if drifted beyond tolerance
                    weight_drift = abs(current_weight - target_weight) / abs(target_weight) if target_weight != 0 else 0
                    if weight_drift > self.rebalance_tolerance:
                        pair_info = data.top_buy_pair if sig == "Buy" else data.top_short_pair
                        tag = f"Rebalance {pair_info}" if pair_info else "Rebalance"
                        
                        self.SetHoldings(sym, target_weight, tag=tag)
                        rebalance_count += 1
                        
                        if self.DebugMode:
                            self._log.note(f"Rebal {sym.Value}: {current_weight:.1%}→{target_weight:.1%} drift={weight_drift:.1%}")
                
                if rebalance_count > 0:
                    self.Debug(f"[REBALANCE] {self.Time.date()}: Adjusted {rebalance_count} positions (tol={self.rebalance_tolerance:.0%})")
                self._last_rebalance_date = self.Time.date()
        
        # Liquidate symbols that lost leader status
        for sym in list(self.Portfolio.Keys):
            if sym != self._schedule_symbol and self.Portfolio[sym].Invested and sym not in self.leader_symbols:
                if self.Securities[sym].IsTradable:
                    self.Liquidate(sym, tag="No longer a leader")
                else:
                    if self.Time.day == 1:
                        self._log.note(f"Cannot liquidate non-tradable {sym.Value}")
    
    def _check_spread(self, symbol):
        security = self.Securities[symbol]
        
        if not self.LiveMode:
            return True
            
        bid = security.BidPrice
        ask = security.AskPrice
        
        if bid == 0 or ask == 0:
            self._log.note(f"No quote: {symbol.Value}")
            return False 
            
        mid = (bid + ask) / 2
        spread_bps = ((ask - bid) / mid) * 10000
        
        if spread_bps > self.bid_ask_max_spread_bps:
            self._log.note(f"Wide spread: {symbol.Value} {spread_bps:.0f}bps")
        
        return spread_bps <= self.bid_ask_max_spread_bps
    
    def LoadHistoricalData(self):
        try:
            url = f"https://raw.githubusercontent.com/peterkitch/qc-historical-data/main/data/csv/{self.primary_ticker.replace('.', '-')}_pre1998.csv"
            
            csv_content = self.Download(url)
            
            if not csv_content or csv_content.startswith("404:"):
                bars_needed = self.MAX_SMA_DAY + 6
                hist = self.History(self.symbol, bars_needed, Resolution.Daily)

                if hist.empty:
                    return

                closes = (hist.loc[self.symbol]["close"]
                          if isinstance(hist.index, pd.MultiIndex) else hist["close"]).astype(float)
                self.daily_closes = closes.tolist()
                self.daily_returns = [0.0] + list(np.diff(self.daily_closes) / self.daily_closes[:-1] * 100)
                self.actual_start_date = (hist.index[0] if not isinstance(hist.index, pd.MultiIndex)
                          else hist.index.get_level_values(1)[0])
                self.csv_rows_loaded = len(self.daily_closes)
                self.Debug(f"Loaded {len(self.daily_closes)} daily bars starting {self.actual_start_date.date()}")
                # Debug probe #2 single-ticker
                if self.DebugMode and self.primary_ticker == "VIK" and len(self.daily_closes) >= 5:
                    v = self.daily_closes[:5]
                    self.Debug(f"[SNAP] {self.primary_ticker} first5={','.join(f'{x:.4f}' for x in v)}")

            df = pd.read_csv(StringIO(csv_content))

            date_col = 'Date' if 'Date' in df.columns else df.columns[0]
            df = df.sort_values(date_col)
            
            close_col = None
            for col_name in ['Adj Close', 'adj close', 'Close', 'close']:
                if col_name in df.columns:
                    close_col = col_name
                    break
                    
            if close_col is None:
                return
            
            full_closes = df[close_col].astype(float).tolist()
            full_returns = [0.0] + list(np.diff(full_closes) / full_closes[:-1] * 100)
            
            self.daily_closes = full_closes
            self.daily_returns = full_returns
            
            self.actual_start_date = pd.to_datetime(df.iloc[0][date_col])
            self.pre_1998_loaded = True
            self.csv_rows_loaded = len(full_closes)
            
        except Exception as exc:
            pass

    def WarmUpArraysAfterLoad(self):
        if len(self.daily_closes) < 2:
            self.sma_matrix = None
            self.initialized = False
            self.warmup_complete = False
            self.pairs = None
            self.buy_cumulative = None
            self.short_cumulative = None
            return
            
        n = len(self.daily_closes)
        self.sma_matrix = np.full((n, self.MAX_SMA_DAY), np.nan)

        self.ComputeSMAMatrix(np.array(self.daily_closes))
        self.InitializeCumulativeArrays()
        self.ReconstructHistoricalSignals()
        self.warmup_complete = True
        self.initialized = True

    def InitializeCumulativeArrays(self):
        closes = np.array(self.daily_closes)
        returns = np.array(self.daily_returns)
        
        num_days = len(closes)
        
        if num_days < 2 or len(returns) < 2:
            return

        max_window = self.MAX_SMA_DAY
        self.pairs = np.array(
            [(i, j) for i in range(1, max_window+1) for j in range(1, max_window+1) if i != j],
            dtype=int
        )
        self.buy_cumulative = np.zeros(len(self.pairs))
        self.short_cumulative = np.zeros(len(self.pairs))

        i_idx = self.pairs[:,0] - 1
        j_idx = self.pairs[:,1] - 1

        for day in range(1, num_days):
            if day >= len(returns):
                break
            sma_i = self.sma_matrix[day-1, i_idx]
            sma_j = self.sma_matrix[day-1, j_idx]
            valid = ((day - 1) >= i_idx) & ((day - 1) >= j_idx) & np.isfinite(sma_i) & np.isfinite(sma_j)
            buy_mask = valid & (sma_i > sma_j)
            short_mask = valid & (sma_i < sma_j)
            self.buy_cumulative[buy_mask] += returns[day]
            self.short_cumulative[short_mask] += -returns[day]

        self.initialized = True

    def ComputeSMAMatrix(self, closes):
        num_days = len(closes)
        self.sma_matrix[:num_days,:] = np.nan
        cumsum = np.cumsum(np.insert(closes, 0, 0))
        
        for i in range(1, min(self.MAX_SMA_DAY+1, num_days+1)):
            idx = np.arange(i-1, num_days)
            self.sma_matrix[idx, i-1] = (cumsum[idx+1] - cumsum[idx+1 - i]) / i

    def ReconstructHistoricalSignals(self):
        closes = np.array(self.daily_closes)
        returns = np.array(self.daily_returns)
        num_days = len(closes)
        
        first_trade_day = None
        first_trade_info = None
        
        i_indices = self.pairs[:, 0] - 1
        j_indices = self.pairs[:, 1] - 1

        temp_buy_cumulative = np.zeros(len(self.pairs))
        temp_short_cumulative = np.zeros(len(self.pairs))
        
        for day_idx in range(num_days):
            if day_idx > 0 and len(self.pairs) > 0:
                row = day_idx - 1
                sma_i = self.sma_matrix[row, i_indices]
                sma_j = self.sma_matrix[row, j_indices]
                ready = (row >= i_indices) & (row >= j_indices) & np.isfinite(sma_i) & np.isfinite(sma_j)
                buy_mask_today = ready & (sma_i > sma_j)
                short_mask_today = ready & (sma_i < sma_j)

                buy_cands = np.where(buy_mask_today)[0]
                short_cands = np.where(short_mask_today)[0]

                if buy_cands.size:
                    buy_idx = buy_cands[np.argmax(temp_buy_cumulative[buy_cands])]
                else:
                    buy_idx = self._pick_best_pair(temp_buy_cumulative, temp_short_cumulative, self.pairs)[0]

                if short_cands.size:
                    short_idx = short_cands[np.argmax(temp_short_cumulative[short_cands])]
                else:
                    short_idx = self._pick_best_pair(temp_buy_cumulative, temp_short_cumulative, self.pairs)[1]
                
                buy_val = temp_buy_cumulative[buy_idx]
                short_val = temp_short_cumulative[short_idx]
                top_buy = tuple(self.pairs[buy_idx])
                top_short = tuple(self.pairs[short_idx])
            else:
                if len(self.pairs)>0:
                    max_sum = np.argmax(self.pairs[:,0] + self.pairs[:,1])
                    top_buy = top_short = tuple(self.pairs[max_sum])
                else:
                    top_buy = top_short = (self.MAX_SMA_DAY, self.MAX_SMA_DAY-1)
                buy_val = short_val = 0.0

            trade_date = (self.actual_start_date + timedelta(days=day_idx)).date()
            self.daily_top_buy_pairs[trade_date] = (top_buy, buy_val)
            self.daily_top_short_pairs[trade_date] = (top_short, short_val)
            
            if day_idx > 0:
                sma_i_day = self.sma_matrix[day_idx-1, i_indices]
                sma_j_day = self.sma_matrix[day_idx-1, j_indices]
                valid_mask = ((day_idx - 1) >= i_indices) & ((day_idx - 1) >= j_indices) & np.isfinite(sma_i_day) & np.isfinite(sma_j_day)
                buy_mask = valid_mask & (sma_i_day > sma_j_day)
                short_mask = valid_mask & (sma_i_day < sma_j_day)
                temp_buy_cumulative[buy_mask] += returns[day_idx]
                temp_short_cumulative[short_mask] += -returns[day_idx]

        for day_idx in range(1, num_days):
            prev = day_idx - 1
            trade_date_prev = (self.actual_start_date + timedelta(days=prev)).date()
            buy_pair, buy_cap = self.daily_top_buy_pairs[trade_date_prev]
            short_pair, shr_cap = self.daily_top_short_pairs[trade_date_prev]
            
            n1b,n2b = buy_pair
            n1s,n2s = short_pair

            if prev >= max(n1b-1,n2b-1,n1s-1,n2s-1):
                buy_sma1 = self.sma_matrix[prev,n1b-1]
                buy_sma2 = self.sma_matrix[prev,n2b-1]
                short_sma1 = self.sma_matrix[prev,n1s-1]
                short_sma2 = self.sma_matrix[prev,n2s-1]
                
                buy_leg_ready = (prev >= max(n1b-1,n2b-1) and
                                np.isfinite(buy_sma1) and np.isfinite(buy_sma2))
                short_leg_ready = (prev >= max(n1s-1,n2s-1) and
                                np.isfinite(short_sma1) and np.isfinite(short_sma2))

                buy_sig = buy_leg_ready and (buy_sma1 > buy_sma2)
                short_sig = short_leg_ready and (short_sma1 < short_sma2)
                
                if buy_sig and short_sig:
                    pos = "Buy" if buy_cap > shr_cap else "Short"
                elif buy_sig:
                    pos = "Buy"
                elif short_sig:
                    pos = "Short"
                else:
                    pos = "None"
                
                if day_idx < num_days - 1 and self.actual_start_date:
                    next_trade_date = (self.actual_start_date + timedelta(days=day_idx + 1)).date()
                    self.historical_positions[next_trade_date] = {
                        'position': pos,
                        'source_pair': (
                            buy_pair if pos == "Buy"
                            else (short_pair if pos == "Short" else None)
                        )
                    }
                
                if first_trade_day is None and pos != "None":
                    first_trade_day = day_idx
                    first_trade_info = f"{pos} using pair {buy_pair if pos == 'Buy' else short_pair}"
                    
                if day_idx == num_days-1:
                    self.active_position_for_today = pos
                    if pos == "Buy":
                        self.signal_source_pair = buy_pair
                    elif pos == "Short":
                        self.signal_source_pair = short_pair
                    else:
                        self.signal_source_pair = None

        self.buy_cumulative = temp_buy_cumulative
        self.short_cumulative = temp_short_cumulative

    def UpdateCumulativeArrays(self, new_return):
        if self.pairs is None:
            return
            
        closes = np.array(self.daily_closes)
        
        rows_needed = len(closes)
        if self.sma_matrix is None:
            return
        if self.sma_matrix.shape[0] < rows_needed:
            extra = rows_needed - self.sma_matrix.shape[0]
            self.sma_matrix = np.vstack([
                self.sma_matrix,
                np.full((extra, self.MAX_SMA_DAY), np.nan)
            ])

        current_max_window = min(self.MAX_SMA_DAY, len(closes))
        if self.pairs is None:
            existing_max = 0
        else:
            existing_max = max(self.pairs[:, 0].max(), self.pairs[:, 1].max())

        if current_max_window > existing_max:
            new_pairs = np.array([(i, j) for i in range(1, current_max_window+1) 
                                 for j in range(1, current_max_window+1) if i != j], dtype=int)
            
            new_buy_cumulative = np.zeros(len(new_pairs))
            new_short_cumulative = np.zeros(len(new_pairs))
            
            if self.pairs is not None:
                old_idx_map = {tuple(p): k for k, p in enumerate(self.pairs)}
                for idx, pair in enumerate(new_pairs):
                    k = old_idx_map.get(tuple(pair))
                    if k is not None:
                        new_buy_cumulative[idx] = self.buy_cumulative[k]
                        new_short_cumulative[idx] = self.short_cumulative[k]
            
            self.pairs = new_pairs
            self.buy_cumulative = new_buy_cumulative
            self.short_cumulative = new_short_cumulative
        
        new_sma_row = np.empty(self.MAX_SMA_DAY)
        cumsum = np.cumsum(np.insert(closes, 0, 0))
        
        windows = np.arange(1, self.MAX_SMA_DAY + 1)
        valid_windows = windows[windows <= len(closes)]
        new_sma_row[valid_windows - 1] = (cumsum[-1] - cumsum[-1 - valid_windows]) / valid_windows
        new_sma_row[windows > len(closes)] = np.nan
        
        yesterday_idx = len(closes) - 2
        day_count = len(self.daily_closes)
        
        i_indices = self.pairs[:, 0] - 1
        j_indices = self.pairs[:, 1] - 1
        
        yesterday_sma_i = self.sma_matrix[yesterday_idx, i_indices]
        yesterday_sma_j = self.sma_matrix[yesterday_idx, j_indices]
        
        valid_mask = (yesterday_idx >= i_indices) & (yesterday_idx >= j_indices)
        buy_mask = valid_mask & np.isfinite(yesterday_sma_i) & np.isfinite(yesterday_sma_j) & (yesterday_sma_i > yesterday_sma_j)
        short_mask = valid_mask & np.isfinite(yesterday_sma_i) & np.isfinite(yesterday_sma_j) & (yesterday_sma_i < yesterday_sma_j)
        
        self.buy_cumulative[buy_mask] += new_return
        self.short_cumulative[short_mask] += -new_return
        
        row_idx = len(closes) - 1
        if self.sma_matrix.shape[0] <= row_idx:
            self.sma_matrix = np.vstack([
                self.sma_matrix,
                np.full((1, self.MAX_SMA_DAY), np.nan)
            ])
        
        self.sma_matrix[row_idx, :] = new_sma_row      # no rolling

        # Debug SMA state update timing
        if self.DebugMode and self.primary_ticker == "VIK" and len(self.daily_closes) <= 5:
            sma1_str = f"{new_sma_row[1]:.4f}" if len(new_sma_row) > 1 else "NA"
            self.Debug(
                f"[SMA-UPD-S] {self.Time.date()} VIK "
                f"closes_len={len(self.daily_closes)} "
                f"SMA[0]={new_sma_row[0]:.4f} SMA[1]={sma1_str}"
            )
    
    def EmitConsolidatorBar(self):
        if hasattr(self, 'dailyConsolidator'):
            self.dailyConsolidator.Scan(self.Time)
    
    def OnDailyBar(self, bar):
        if self.multi_ticker_mode:
            return
            
        # Debug probe #3 single-ticker
        if self.DebugMode and self.primary_ticker == "VIK":
            self.Debug(f"[DAILY] {bar.EndTime.date()} VIK single={bar.Close:.4f}")
            
        if self.pre_1998_loaded and self.actual_start_date and bar.Time < self.actual_start_date:
            return
        
        if self.warmup_complete and self._date_filter:
            start_d, end_d = self._date_filter
            if bar.Time.date() < start_d or bar.Time.date() > end_d:
                return
            
        _t0 = self.Time
        self.SetDateTime(bar.Time)
        
        trading_date = bar.EndTime.date()
        
        if len(self.daily_closes) == 0:
            if not self.pre_1998_loaded:
                self.actual_start_date = bar.Time
        
        if len(self.daily_closes) == 0:
            self.Plot("Cumulative Combined Capture %", "ExAnte", 0.0)
            self.Plot("Cumulative Combined Capture %", "Combined Capture", 0.0)
            self.Plot("Portfolio Value", "Total Value", 100000)
            
        ret = 0.0
        if len(self.daily_closes) > 0:
            ret = (bar.Close / self.daily_closes[-1] - 1) * 100
            
        self.Plot("Portfolio Value", "Total Value", self.Portfolio.TotalPortfolioValue)
        
        self.daily_closes.append(bar.Close)
        self.daily_returns.append(ret)
        
        if self.initialized and self.pairs is not None and len(self.daily_closes) > 1:
            yesterday_signal = self._yesterday_signal
            
            if yesterday_signal == "Buy":
                self.capture_ex_ante += ret
            elif yesterday_signal == "Short":
                self.capture_ex_ante += -ret
            
            self.cumulative_combined_capture_value = self.capture_ex_ante
                  
            self.Plot("Cumulative Combined Capture %", "ExAnte", self.capture_ex_ante)
            self.Plot("Cumulative Combined Capture %", "Combined Capture",
                      self.cumulative_combined_capture_value)
            
            self.UpdateCumulativeArrays(ret)

        self.Plot(f"{self.primary_ticker} Daily Close","Close",bar.Close)

        if not self.initialized and len(self.daily_closes) >= 2:
            n = len(self.daily_closes)
            self.sma_matrix = np.full((n, self.MAX_SMA_DAY), np.nan)
            self.ComputeSMAMatrix(np.array(self.daily_closes))
            
            self.InitializeCumulativeArrays()
            self.ReconstructHistoricalSignals()
            
            self.initialized = True
            self.warmup_complete = True

        if self.initialized and self._trade_day_count == 0:
            self.Plot("Cumulative Combined Capture %", "ExAnte", 0.0)
            self.Plot("Cumulative Combined Capture %", "Combined Capture", 0.0)

        if self.initialized:
            self.CalculateDailyThresholdsAndSignals(trading_date)
            
            # Call debugger with date test instead of _trade_day_count
            if self._st_log_start <= trading_date < self._st_log_start + timedelta(days=30):
                self._debug_sma_state(trading_date)

        self._trade_day_count += 1
        
        self._yesterday_signal = self.active_position_for_today
        
        self.SetDateTime(_t0)

    def CalculateDailyThresholdsAndSignals(self, trading_date):
        if self.pairs is None or self.buy_cumulative is None or self.short_cumulative is None:
            return
            
        closes_len = len(self.daily_closes)
        row = closes_len - 1
        i_idx = self.pairs[:, 0] - 1
        j_idx = self.pairs[:, 1] - 1

        sma_i = self.sma_matrix[row, i_idx]
        sma_j = self.sma_matrix[row, j_idx]

        ready = (row >= i_idx) & (row >= j_idx) & np.isfinite(sma_i) & np.isfinite(sma_j)
        buy_mask_today = ready & (sma_i > sma_j)
        short_mask_today = ready & (sma_i < sma_j)

        best_buy_idx, best_short_idx = self._pick_best_pair(self.buy_cumulative, self.short_cumulative, self.pairs)
        
        self.top_buy_pair = tuple(self.pairs[best_buy_idx])
        self.top_short_pair = tuple(self.pairs[best_short_idx])
        buy_val = self.buy_cumulative[best_buy_idx]
        short_val = self.short_cumulative[best_short_idx]

        self.daily_top_buy_pairs[trading_date] = (self.top_buy_pair, buy_val)
        self.daily_top_short_pairs[trading_date] = (self.top_short_pair, short_val)

        closes = np.array(self.daily_closes)
        n1b,n2b = self.top_buy_pair
        n1s,n2s = self.top_short_pair
        # Debug window contents before threshold calculation
        if self.DebugMode and self.primary_ticker == "VIK":
            max_window = max(n1b, n2b, n1s, n2s)
            lookback = closes[-max_window:] if len(closes) >= max_window else closes
            self.Debug(
                f"[CHK-S] {trading_date} VIK closes_len={len(closes)} "
                f"last_{max_window}={[f'{x:.4f}' for x in lookback[-5:]]}... "
                f"buy_pair=({n1b},{n2b}) short_pair=({n1s},{n2s})"
            )

        self.buy_threshold = self.calculate_crossing_price(n1b,n2b,closes)
        self.short_threshold = self.calculate_crossing_price(n1s,n2s,closes)
        
        if (self.buy_threshold is not None and
            self.short_threshold is not None and
            self.buy_threshold <= self.short_threshold):
            
            mid = 0.5 * (self.buy_threshold + self.short_threshold)
            buf = mid * 0.00001
            self.short_threshold = mid - buf
            self.buy_threshold = mid + buf

        closes_len = len(closes)

        buy_leg_ready = (closes_len >= max(n1b, n2b) and
                        np.isfinite(self.sma_matrix[-1, n1b-1]) and
                        np.isfinite(self.sma_matrix[-1, n2b-1]))

        short_leg_ready = (closes_len >= max(n1s, n2s) and
                        np.isfinite(self.sma_matrix[-1, n1s-1]) and
                        np.isfinite(self.sma_matrix[-1, n2s-1]))

        buy_sig   = False
        short_sig = False

        if buy_leg_ready or short_leg_ready:
            b_first, b_second = self.sma_matrix[-1, n1b-1], self.sma_matrix[-1, n2b-1]
            s_first, s_second = self.sma_matrix[-1, n1s-1], self.sma_matrix[-1, n2s-1]

            buy_sig   = buy_leg_ready   and (b_first > b_second) and self._cap_ok(best_buy_idx,  True)
            short_sig = short_leg_ready and (s_first < s_second) and self._cap_ok(best_short_idx, False)

        if np.array_equal(self.top_buy_pair, self.top_short_pair) and (buy_sig or short_sig):
            if buy_sig:
                self.active_position_for_today = "Buy"
                self.signal_source_pair        = self.top_buy_pair
            else:
                self.active_position_for_today = "Short"
                self.signal_source_pair        = self.top_short_pair
        elif buy_sig and short_sig:
            self.active_position_for_today = "Short" if short_val > buy_val else "Buy"
            self.signal_source_pair        = (self.top_short_pair
                                              if self.active_position_for_today == "Short"
                                              else self.top_buy_pair)
        elif buy_sig:
            self.active_position_for_today = "Buy"
            self.signal_source_pair        = self.top_buy_pair
        elif short_sig:
            self.active_position_for_today = "Short"
            self.signal_source_pair        = self.top_short_pair
        else:
            self.active_position_for_today = "None"
            self.signal_source_pair        = None

        self._idx_top_buy = int(best_buy_idx)
        self._idx_top_short = int(best_short_idx)
        
        self.yesterdays_buy_pair = self.top_buy_pair
        self.yesterdays_short_pair = self.top_short_pair

    def calculate_crossing_price(self, n1, n2, closes):
        if n1==n2 or len(closes)<max(n1,n2):
            return None
        sum1 = np.sum(closes[-(n1-1):]) if n1>1 else 0
        sum2 = np.sum(closes[-(n2-1):]) if n2>1 else 0
        denom = n2 - n1
        if denom==0:
            return None
        price = (n1*sum2 - n2*sum1) / denom
        return price if price>0 else None

    def OnOrderEvent(self, order_event: OrderEvent) -> None:
        if order_event.Status != OrderStatus.Filled:
            return

        order  = self.Transactions.GetOrderById(order_event.OrderId)
        symbol = order.Symbol
        qty    = order_event.FillQuantity
        price  = order_event.FillPrice
        tag = order.Tag or ""
        last_qty = getattr(self, "_last_position_qty", 0)
        if (last_qty > 0 and qty < 0 and abs(qty) == last_qty) or \
           (last_qty < 0 and qty > 0 and qty == abs(last_qty)):
            tag = "Liquidated"

        self._last_position_qty = self.Portfolio[symbol].Quantity

    def EvaluateAndTrade(self):
        if self.Time < self.data_transition_date:
            return
            
        if not hasattr(self, "_first_trade_date"):
            self._first_trade_date = self.Time.date()
            
        if not self.initialized or not self.warmup_complete:
            return
            
        if not self.Securities[self.symbol].HasData:
            return

        last_min = self.History(self.symbol, 1, Resolution.Minute)

        if self.DebugMode and self.primary_ticker in ("VIK", "UBER", "PLTR"):
            self.Debug(
                f"[S-MIN] {self.Time.date()} {self.primary_ticker} hist_15:59={last_min.iloc[-1]['close'] if not last_min.empty else 'NA'}"
            )
            
        if last_min.empty:
            minute_price = self.Securities[self.symbol].Close
        else:
            minute_price = float(last_min.iloc[-1]['close'])
        # Debug probe #4 single-ticker
        if self.DebugMode and self.primary_ticker == "VIK":
            self.Debug(f"[S-PX] {self.Time.date()} VIK minPrice={minute_price:.4f}")

        plan = self._yesterday_signal        
        raw  = self._determine_signal_from_price(minute_price)

        signal = plan if plan in ("Buy", "Short") else "None"

        if hasattr(self, 'reverse_all') and self.reverse_all:
            signal = "Buy" if signal == "Short" else "Short" if signal == "Buy" else "None"
        
        if signal == "Short" and self.block_shorts:
            signal = "None"
        elif signal == "Buy" and self.block_buys:
            signal = "None"

        # Debug probe #5 single-ticker
        if self.DebugMode and self.primary_ticker == "VIK":
            self.Debug(f"[THR] {self.Time.date()} plan={plan} sig={signal} "
                    f"buyTh={self.buy_threshold} shortTh={self.short_threshold}")
                    
        if signal == self.current_position:
            return

        if self.current_position != "None":
            self.Liquidate(self.symbol)

        if signal in ["Buy", "Short"]:
            weight = self.TARGET_WEIGHT if signal == "Buy" else -self.TARGET_WEIGHT
            
            tag_txt = f"{signal} from {self.signal_source_pair}"
            
            self.SetHoldings(self.symbol, weight, tag=tag_txt)
            
        if signal != self.current_position:
            self.current_position = signal
            self.active_position_for_today = signal   
            self.trade_count += 1
            
        if self.initialized:
            buy_cap = self.buy_cumulative[self._idx_top_buy] if hasattr(self, '_idx_top_buy') else 0.0
            short_cap = self.short_cumulative[self._idx_top_short] if hasattr(self, '_idx_top_short') else 0.0
            self._log_daily(
                sym=self.symbol,
                price=minute_price,
                plan=plan,
                sig=signal,
                y_sig=self._yesterday_signal,
                t_sig=self.active_position_for_today,
                buy_pair=self.top_buy_pair,
                buy_cap=buy_cap,
                short_pair=self.top_short_pair,
                short_cap=short_cap
            )                   

    def _determine_signal_from_price(self, price: float) -> str:
        if not hasattr(self, 'buy_threshold') or not hasattr(self, 'short_threshold'):
            return "None"

        if self.buy_threshold is None or self.short_threshold is None:
            return "None"
        
        buy_hit   = price >= self.buy_threshold
        short_hit = price <= self.short_threshold
        
        if buy_hit and short_hit:
            if hasattr(self, '_idx_top_buy') and hasattr(self, '_idx_top_short'):
                buy_cap = self.buy_cumulative[self._idx_top_buy]
                short_cap = self.short_cumulative[self._idx_top_short]
                return "Buy" if buy_cap >= short_cap else "Short"
            else:
                return "None"
                
        if buy_hit:
            return "Buy"
        if short_hit:
            return "Short"
        return "None"
    
    def _pick_best_pair(
        self,
        buy_array:   np.ndarray,
        short_array: np.ndarray,
        pairs:       np.ndarray,
        default_pair: tuple[int, int] = None
    ) -> tuple[int, int]:

        if default_pair is None:
            default_pair = (self.MAX_SMA_DAY, self.MAX_SMA_DAY - 1)

        day_len    = len(self.daily_closes)
        ready_mask = (pairs[:, 0] <= day_len) & (pairs[:, 1] <= day_len)

        def _choose(cum_arr: np.ndarray, is_buy: bool) -> int:
            mask = ready_mask & (cum_arr > 0.0)
            if mask.any():
                return self._best_pair_index(cum_arr, pairs, mask)

            span_ready = (pairs[:, 0] + pairs[:, 1]).copy()
            span_ready[~ready_mask] = -1
            if (span_ready > 0).any():
                max_span = span_ready.max()
                cands = np.where(span_ready == max_span)[0]
                return int(max(cands, key=lambda k: (pairs[k][0], pairs[k][1])))

            hits = np.where(
                (pairs[:, 0] == default_pair[0]) &
                (pairs[:, 1] == default_pair[1])
            )[0]
            if len(hits):
                return int(hits[0])

            max_span = (pairs[:, 0] + pairs[:, 1]).max()
            cands = np.where((pairs[:, 0] + pairs[:, 1]) == max_span)[0]
            return int(max(cands, key=lambda k: (pairs[k][0], pairs[k][1])))

        buy_idx = _choose(buy_array, True)
        short_idx = _choose(short_array, False)
        
        return buy_idx, short_idx
    
    def _cap_ok(self, idx: int, is_buy: bool) -> bool:
        """
        True  → cumulative capture for the pair is positive  
        False → pair not yet profitable or index out of range
        """
        if not hasattr(self, 'buy_cumulative') or not hasattr(self, 'short_cumulative'):
            return False
        arr = self.buy_cumulative if is_buy else self.short_cumulative
        return 0 <= idx < len(arr) and arr[idx] > 0.0

    def _log_daily(self, *, sym, price, plan, sig, y_sig, t_sig, buy_pair, buy_cap, short_pair, short_cap):
        """Write one Debug/Note line per symbol per day."""
        # Only log the specific date range we're debugging
        if not (datetime(2021,4,14).date() <= self.Time.date() <= datetime(2021,5,3).date()):
            return
            
        # Get thresholds for debugging
        buy_thr = "None"
        short_thr = "None"
        if hasattr(self, 'symbol_data') and sym in self.symbol_data:
            data = self.symbol_data[sym]
            buy_thr = f"{data.buy_threshold:.2f}" if data.buy_threshold else "None"
            short_thr = f"{data.short_threshold:.2f}" if data.short_threshold else "None"
        elif hasattr(self, 'buy_threshold'):
            buy_thr = f"{self.buy_threshold:.2f}" if self.buy_threshold else "None"
            short_thr = f"{self.short_threshold:.2f}" if self.short_threshold else "None"
            
        # Format pairs consistently as arrays to match single-ticker output
        # Handle numpy arrays, tuples, and None
        if buy_pair is not None and hasattr(buy_pair, '__len__') and len(buy_pair) >= 2:
            buy_pair_str = f"[{buy_pair[0]} {buy_pair[1]}]"
        else:
            buy_pair_str = "None"
            
        if short_pair is not None and hasattr(short_pair, '__len__') and len(short_pair) >= 2:
            short_pair_str = f"[{short_pair[0]} {short_pair[1]}]"
        else:
            short_pair_str = "None"
        
        msg = (f"{self.Time:%Y-%m-%d} {sym.Value} {price:.2f}  "
               f"plan={plan} sig={sig}  "
               f"Y:{y_sig}→T:{t_sig}  "
               f"B:{buy_pair_str}/{buy_cap:.1f}  "
               f"S:{short_pair_str}/{short_cap:.1f}  "
               f"BT:{buy_thr} ST:{short_thr}")
        self._log.note(msg)

    def _best_pair_index(self, cum_array: np.ndarray, pairs: np.ndarray, ready_mask: np.ndarray = None) -> int:
        if ready_mask is None:
            ready_mask = np.ones(len(pairs), dtype=bool)

        if ready_mask.any() and np.nanmax(cum_array[ready_mask]) > 0:
            mask = ready_mask
        else:
            mask = ready_mask & (cum_array > 0)

        if not mask.any():
            max_span = (pairs[:,0] + pairs[:,1]).max()
            candidates = np.where(pairs[:,0] + pairs[:,1] == max_span)[0]
            return int(max(candidates, key=lambda k: (pairs[k][0], pairs[k][1])))
        
        sub = np.where(mask)[0]
        max_val = cum_array[sub].max()
        candidates = sub[cum_array[sub] == max_val]
        
        if len(candidates) == 1:
            return int(candidates[0])
        
        return int(max(candidates,
                       key=lambda k: (pairs[k][0] + pairs[k][1],
                                      pairs[k][0],
                                      pairs[k][1])))

    def OnEndOfDay(self, symbol=None):
        self._log.dump(f"{self.Time:%Y-%m-%d}")
        
        # Update yesterday's signal for ALL symbols AFTER trading (match single-ticker timing)
        if self.multi_ticker_mode:
            for sym, data in self.symbol_data.items():
                data._yesterday_signal = data.signal_for_tomorrow
        
        if hasattr(self, '_pending_cleanup'):
            for sym in self._pending_cleanup:
                if self.Securities.ContainsKey(sym) and not self.Portfolio[sym].Invested:
                    self.RemoveSecurity(sym)
                    # Also remove from symbol_data to free memory
                    if sym in self.symbol_data:
                        del self.symbol_data[sym]
                    # Remove from daily consolidators
                    if sym in self._daily_consolidators:
                        del self._daily_consolidators[sym]
            self._pending_cleanup.clear()
    
    def FlagForPermanentRemoval(self, symbol):
        clean_ticker = symbol.Value.replace('.', '').replace('-', '').strip()
        self.banned_ticks.add(clean_ticker)
        
        self.leader_symbols.discard(symbol)
        
        if symbol not in self._pending_cleanup:
            self._pending_cleanup.append(symbol)
            
        if self.Time.day == 1:
            self._log.note(f"Flagged {symbol.Value} for permanent removal (stale)")

    # ===== DEBUG HELPERS ======================================================
    def _debug_sma_state(self, trading_date, symbol_data=None):
        """
        Dump a concise SMA/leader‑pair snapshot for the first 30 trading days.
        Works in BOTH modes:
          • single‑ticker → use self.daily_* structures
          • multi‑ticker  → pass in the SymbolData instance you're analysing
        """
        # ── source detection ────────────────────────────────────────────────
        if self.multi_ticker_mode and symbol_data:
            d        = symbol_data
            closes   = list(d.closes)
            sma_arr  = d.sma_n
            symbol   = d.symbol.Value
            buy_pair = d.daily_top_buy_pairs.get(trading_date, (None, 0.0))
            shr_pair = d.daily_top_short_pairs.get(trading_date, (None, 0.0))
            # update COIN‑day counter only once
            if symbol == "COIN":
                if self._coin_start_date is None:
                    self._coin_start_date = trading_date
                self._coin_day_count = (trading_date - self._coin_start_date).days + 1
                day_no = self._coin_day_count
            else:
                day_no = 0
        else:   # single‑ticker mode
            closes   = self.daily_closes
            if not closes:
                return
            
            # Date-based filtering for single-ticker
            if trading_date < self._st_log_start:
                return
            
            # Establish day-1 on first call
            if self._st_first_seen is None:
                self._st_first_seen = trading_date
            
            day_no = (trading_date - self._st_first_seen).days + 1  # 1-based counter
            if day_no > 30:
                return
                
            sma_arr  = self.sma_matrix[len(closes)-1]
            symbol   = self.primary_ticker
            buy_pair = self.daily_top_buy_pairs.get(trading_date, (None, 0.0))
            shr_pair = self.daily_top_short_pairs.get(trading_date, (None, 0.0))

        # only first 30 trading days
        if day_no == 0 or day_no > 30:
            return

        self.Debug(f"\n{'='*75}\n[SMA‑DEBUG] {trading_date}  {symbol}  Day #{day_no}")
        self.Debug(f"Last 5 closes: {[f'{c:.2f}' for c in closes[-5:]]}")
        # print a few key SMAs
        pivots = (1,2,3,5,8,13,21)
        sma_txt = ", ".join(
            f"SMA({n})={sma_arr[n-1]:.2f}" for n in pivots
            if n <= len(closes) and n <= len(sma_arr) and not np.isnan(sma_arr[n-1])
        )
        self.Debug("Key SMAs : " + sma_txt)

        # leader pairs
        bp, bcap = buy_pair
        sp, scap = shr_pair
        
        # Convert to tuple if numpy array
        if isinstance(bp, np.ndarray):
            bp = tuple(bp)
        if isinstance(sp, np.ndarray):
            sp = tuple(sp)
            
        if bp is not None:
            n1,n2 = bp; self.Debug(f" Buy leader {bp} cap={bcap:.2f}%  Δ={sma_arr[n1-1]-sma_arr[n2-1]:.2f}")
        if sp is not None:
            n1,n2 = sp; self.Debug(f"Short leader {sp} cap={scap:.2f}%  Δ={sma_arr[n1-1]-sma_arr[n2-1]:.2f}")
        # tiny capture‑grid sneak peek (first 5×5) for multi‑ticker
        if self.multi_ticker_mode and symbol_data and day_no <= 10:
            g = symbol_data.grid
            w = min(5, g.shape[0])
            self.Debug("Capture‑grid (top 5×5):")
            for i in range(w):
                self.Debug("  " + " ".join(f"{g[i,j]:6.1f}" for j in range(w)))
        self.Debug('='*75)
    
    def OnEndOfAlgorithm(self):
        self._log.dump("FINAL")
        
        trades = sum(1 for o in self.Transactions.GetOrders() if o.Status == OrderStatus.Filled)
        ret = (self.Portfolio.TotalPortfolioValue / 100_000 - 1) * 100
        self.Debug(f"[FINAL] Trades: {trades} | Return: {ret:.2f}% | "
                   f"Value: ${self.Portfolio.TotalPortfolioValue:,.2f}")
