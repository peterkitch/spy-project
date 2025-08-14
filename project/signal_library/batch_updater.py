#!/usr/bin/env python3
"""
Enhanced Signal Library Batch Updater - Phase 3 Fix
Improved error handling and retry logic for transient failures
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project directory to path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

from onepass import (
    normalize_ticker, fetch_data, process_onepass_tickers,
    perform_incremental_update, load_signal_library,
    save_signal_library
)
from signal_library_utils import (
    evaluate_library_acceptance, write_changelog,
    save_signal_library_v3, load_signal_library_v3,
    SIGNAL_LIBRARY_VERSION
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default watchlist file
WATCHLIST_FILE = "watchlist.json"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds

class EnhancedBatchUpdater:
    """Enhanced batch updater with retry logic and better error handling"""
    
    def __init__(self, watchlist: Optional[List[str]] = None, 
                 max_workers: int = 4,
                 use_v3_format: bool = False,  # Default to V2 for compatibility
                 max_retries: int = MAX_RETRIES):
        """
        Initialize enhanced batch updater
        
        Args:
            watchlist: List of tickers to update (or loads from file)
            max_workers: Maximum parallel workers
            use_v3_format: Use Parquet+NPZ format (Phase 3) - requires pyarrow
            max_retries: Maximum retries for transient failures
        """
        self.watchlist = watchlist or self.load_watchlist()
        self.max_workers = max_workers
        self.use_v3_format = use_v3_format
        self.max_retries = max_retries
        self.results = []
        
        # Check if pyarrow is available for V3 format
        if self.use_v3_format:
            try:
                import pyarrow
            except ImportError:
                logger.warning("pyarrow not installed, falling back to V2 format")
                self.use_v3_format = False
        
    def load_watchlist(self) -> List[str]:
        """Load watchlist from file"""
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, 'r') as f:
                data = json.load(f)
                return data.get('tickers', [])
        else:
            # Default watchlist
            return ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 
                   'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'BTC-USD', 'ETH-USD']
    
    def save_watchlist(self) -> None:
        """Save current watchlist to file"""
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump({
                'tickers': self.watchlist,
                'last_updated': datetime.now().isoformat()
            }, f, indent=2)
    
    def fetch_data_with_retry(self, ticker: str) -> Optional[object]:
        """Fetch data with retry logic for transient failures"""
        for attempt in range(self.max_retries):
            try:
                df = fetch_data(ticker)
                if not df.empty:
                    return df
                    
                logger.warning(f"Empty data for {ticker} on attempt {attempt + 1}")
                
            except Exception as e:
                logger.warning(f"Fetch failed for {ticker} on attempt {attempt + 1}: {e}")
                
                if attempt < self.max_retries - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    logger.error(f"All fetch attempts failed for {ticker}")
                    raise
                    
        return None
    
    def update_single_ticker(self, ticker: str) -> Dict:
        """Update a single ticker with smart incremental/rebuild logic and retry"""
        result = {
            'ticker': ticker,
            'status': 'pending',
            'action': None,
            'duration': 0,
            'details': {},
            'retries': 0
        }
        
        start_time = time.time()
        
        try:
            # Normalize ticker
            ticker = normalize_ticker(ticker)
            result['ticker'] = ticker  # Update with normalized version
            
            # Check if library exists
            if self.use_v3_format:
                existing_library = load_signal_library_v3(ticker)
            else:
                existing_library = load_signal_library(ticker)
            
            # Fetch current data with retry
            current_df = self.fetch_data_with_retry(ticker)
            if current_df is None or current_df.empty:
                result['status'] = 'error'
                result['details']['error'] = 'No data available after retries'
                return result
            
            result['details']['data_days'] = len(current_df)
            
            if existing_library:
                # Evaluate acceptance
                acceptance_level, integrity_status, message = evaluate_library_acceptance(
                    existing_library, current_df
                )
                
                result['details']['acceptance'] = acceptance_level
                result['details']['integrity'] = integrity_status
                
                if acceptance_level == 'STRICT':
                    # No update needed
                    result['action'] = 'skip'
                    result['status'] = 'success'
                    result['details']['message'] = 'Already up to date'
                    
                elif integrity_status == 'NEW_DATA' and acceptance_level in ['ALL_BUT_LAST', 'HEADTAIL_FUZZY', 'HEADTAIL']:
                    # Try incremental update
                    logger.info(f"Attempting incremental update for {ticker}")
                    
                    # Retry incremental update if it fails transiently
                    for attempt in range(2):  # Limited retries for incremental
                        try:
                            updated_library = perform_incremental_update(ticker, existing_library, current_df)
                            
                            if updated_library:
                                # Save updated library
                                if self.use_v3_format:
                                    save_signal_library_v3(ticker, updated_library)
                                else:
                                    save_signal_library(
                                        ticker,
                                        updated_library['daily_top_buy_pairs'],
                                        updated_library['daily_top_short_pairs'],
                                        updated_library['primary_signals'],
                                        current_df,
                                        updated_library.get('accumulator_state')
                                    )
                                
                                result['action'] = 'incremental'
                                result['status'] = 'success'
                                result['details']['days_added'] = (
                                    len(updated_library['primary_signals']) - 
                                    len(existing_library['primary_signals'])
                                )
                                
                                # Write to changelog
                                write_changelog(ticker, 'incremental_update', {
                                    'days_added': result['details']['days_added'],
                                    'acceptance_level': acceptance_level
                                })
                                break
                            else:
                                # Incremental failed, will fall through to rebuild
                                acceptance_level = 'REBUILD'
                                break
                                
                        except Exception as e:
                            logger.warning(f"Incremental update attempt {attempt + 1} failed for {ticker}: {e}")
                            if attempt == 0:
                                time.sleep(1)
                                continue
                            else:
                                acceptance_level = 'REBUILD'
                
                if acceptance_level == 'REBUILD' or result['action'] is None:
                    # Full rebuild required
                    logger.info(f"Full rebuild required for {ticker}: {message}")
                    
                    # Retry rebuild if it fails
                    for attempt in range(self.max_retries):
                        try:
                            metrics = process_onepass_tickers([ticker], use_existing_signals=False)
                            
                            if metrics:
                                result['action'] = 'rebuild'
                                result['status'] = 'success'
                                result['details']['reason'] = message
                                result['details']['sharpe'] = metrics[0].get('Sharpe Ratio', 0)
                                result['details']['trigger_days'] = metrics[0].get('Trigger Days', 0)
                                result['retries'] = attempt
                                
                                # Write to changelog
                                write_changelog(ticker, 'full_rebuild', {
                                    'reason': message,
                                    'acceptance_level': acceptance_level,
                                    'retries': attempt
                                })
                                break
                            else:
                                raise ValueError("Metrics generation failed")
                                
                        except Exception as e:
                            logger.warning(f"Rebuild attempt {attempt + 1} failed for {ticker}: {e}")
                            if attempt < self.max_retries - 1:
                                time.sleep(RETRY_DELAY * (attempt + 1))
                                continue
                            else:
                                result['status'] = 'error'
                                result['details']['error'] = f"Rebuild failed after {self.max_retries} attempts: {str(e)}"
                    
            else:
                # No existing library, build from scratch
                logger.info(f"Building new Signal Library for {ticker}")
                
                # Retry new build if it fails
                for attempt in range(self.max_retries):
                    try:
                        metrics = process_onepass_tickers([ticker], use_existing_signals=False)
                        
                        if metrics:
                            result['action'] = 'new'
                            result['status'] = 'success'
                            result['details']['sharpe'] = metrics[0].get('Sharpe Ratio', 0)
                            result['details']['trigger_days'] = metrics[0].get('Trigger Days', 0)
                            result['details']['win_ratio'] = metrics[0].get('Win Ratio (%)', 0)
                            result['retries'] = attempt
                            
                            # Write to changelog
                            write_changelog(ticker, 'new_library', result['details'])
                            break
                        else:
                            raise ValueError("Metrics generation failed")
                            
                    except Exception as e:
                        logger.warning(f"New build attempt {attempt + 1} failed for {ticker}: {e}")
                        if attempt < self.max_retries - 1:
                            time.sleep(RETRY_DELAY * (attempt + 1))
                            continue
                        else:
                            result['status'] = 'error'
                            result['details']['error'] = f"New build failed after {self.max_retries} attempts: {str(e)}"
            
        except Exception as e:
            logger.error(f"Unexpected error updating {ticker}: {e}")
            result['status'] = 'error'
            result['details']['error'] = str(e)
        
        finally:
            result['duration'] = time.time() - start_time
        
        return result
    
    def run(self, parallel: bool = True) -> List[Dict]:
        """
        Run batch update on all tickers
        
        Args:
            parallel: Use parallel processing
        
        Returns:
            List of update results
        """
        logger.info(f"Starting enhanced batch update for {len(self.watchlist)} tickers")
        logger.info(f"Format: {'V3 (Parquet+NPZ)' if self.use_v3_format else 'V2 (Pickle)'}")
        logger.info(f"Max retries per ticker: {self.max_retries}")
        
        start_time = time.time()
        
        if parallel and self.max_workers > 1:
            # Parallel execution
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self.update_single_ticker, ticker): ticker
                    for ticker in self.watchlist
                }
                
                for future in as_completed(futures):
                    ticker = futures[future]
                    try:
                        result = future.result(timeout=120)  # 2 minute timeout per ticker
                        self.results.append(result)
                        self._log_result(result)
                    except Exception as e:
                        logger.error(f"Failed to update {ticker}: {e}")
                        self.results.append({
                            'ticker': ticker,
                            'status': 'error',
                            'action': None,
                            'duration': 0,
                            'details': {'error': str(e)}
                        })
        else:
            # Sequential execution
            for ticker in self.watchlist:
                result = self.update_single_ticker(ticker)
                self.results.append(result)
                self._log_result(result)
        
        total_time = time.time() - start_time
        
        # Summary
        self._print_summary(total_time)
        
        return self.results
    
    def _log_result(self, result: Dict) -> None:
        """Log individual result"""
        ticker = result['ticker']
        status = result['status']
        action = result.get('action', 'unknown')
        duration = result['duration']
        retries = result.get('retries', 0)
        
        retry_info = f" (retries: {retries})" if retries > 0 else ""
        
        if status == 'success':
            if action == 'skip':
                logger.info(f"[SKIP] {ticker}: Already up to date ({duration:.1f}s)")
            elif action == 'incremental':
                days = result['details'].get('days_added', 0)
                logger.info(f"[INCR] {ticker}: Added {days} days ({duration:.1f}s){retry_info}")
            elif action == 'rebuild':
                reason = result['details'].get('reason', 'Unknown')[:30]
                logger.info(f"[RBLD] {ticker}: Rebuilt - {reason}... ({duration:.1f}s){retry_info}")
            elif action == 'new':
                logger.info(f"[NEW]  {ticker}: Created new library ({duration:.1f}s){retry_info}")
        else:
            error = result['details'].get('error', 'Unknown error')[:50]
            logger.error(f"[FAIL] {ticker}: {error}... ({duration:.1f}s)")
    
    def _print_summary(self, total_time: float) -> None:
        """Print batch update summary"""
        print("\n" + "="*60)
        print("ENHANCED BATCH UPDATE SUMMARY")
        print("="*60)
        
        # Count by status
        success_count = sum(1 for r in self.results if r['status'] == 'success')
        error_count = sum(1 for r in self.results if r['status'] == 'error')
        
        # Count by action
        skip_count = sum(1 for r in self.results if r.get('action') == 'skip')
        incr_count = sum(1 for r in self.results if r.get('action') == 'incremental')
        rebuild_count = sum(1 for r in self.results if r.get('action') == 'rebuild')
        new_count = sum(1 for r in self.results if r.get('action') == 'new')
        
        # Count retries
        total_retries = sum(r.get('retries', 0) for r in self.results)
        
        print(f"Total tickers: {len(self.results)}")
        print(f"Successful:    {success_count}")
        print(f"Errors:        {error_count}")
        print()
        print(f"Actions:")
        print(f"  Skipped:     {skip_count} (already up to date)")
        print(f"  Incremental: {incr_count} (fast updates)")
        print(f"  Rebuilt:     {rebuild_count} (full recomputation)")
        print(f"  New:         {new_count} (first time)")
        print()
        print(f"Total retries: {total_retries}")
        print(f"Total time:    {total_time:.1f} seconds")
        print(f"Avg per ticker: {total_time/len(self.results):.1f} seconds")
        
        # Show any errors
        if error_count > 0:
            print("\nErrors:")
            for r in self.results:
                if r['status'] == 'error':
                    print(f"  {r['ticker']}: {r['details'].get('error', 'Unknown')}")
        
        # Show success metrics
        if success_count > 0:
            print("\nSuccess Metrics:")
            for r in self.results:
                if r['status'] == 'success' and r.get('action') in ['rebuild', 'new']:
                    ticker = r['ticker']
                    sharpe = r['details'].get('sharpe', 0)
                    trigger_days = r['details'].get('trigger_days', 0)
                    print(f"  {ticker}: Sharpe={sharpe:.2f}, Trigger Days={trigger_days}")
        
        print("="*60)

def main():
    """Main entry point for enhanced batch updater"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Signal Library Batch Updater')
    parser.add_argument('--tickers', nargs='+', help='Tickers to update')
    parser.add_argument('--watchlist', help='Watchlist file', default=WATCHLIST_FILE)
    parser.add_argument('--workers', type=int, default=4, help='Max parallel workers')
    parser.add_argument('--sequential', action='store_true', help='Run sequentially')
    parser.add_argument('--v3', action='store_true', help='Use V3 Parquet+NPZ format')
    parser.add_argument('--retries', type=int, default=3, help='Max retries per ticker')
    
    args = parser.parse_args()
    
    # Determine ticker list
    if args.tickers:
        watchlist = args.tickers
    else:
        watchlist = None  # Will load from file
    
    # Create and run updater
    updater = EnhancedBatchUpdater(
        watchlist=watchlist,
        max_workers=args.workers,
        use_v3_format=args.v3,
        max_retries=args.retries
    )
    
    # Save watchlist if custom tickers provided
    if args.tickers:
        updater.save_watchlist()
    
    # Run update
    results = updater.run(parallel=not args.sequential)
    
    # Exit with appropriate code
    error_count = sum(1 for r in results if r['status'] == 'error')
    sys.exit(1 if error_count > 0 else 0)

if __name__ == "__main__":
    main()