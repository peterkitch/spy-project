# SPYMASTER CRASH PREVENTION IMPLEMENTATION PLAN

## Priority 1: Critical Stability Fixes (Implement First)

### 1.1 Global Operation Mutex System
**Problem:** Multiple operations can conflict when triggered simultaneously
**Solution:** Implement a global operation manager

```python
# Add to global variables section (~line 2950)
class OperationManager:
    def __init__(self):
        self.active_operations = set()
        self.operation_lock = threading.Lock()
        self.operation_queue = queue.Queue()
        self.MAX_CONCURRENT_OPS = 3
    
    def can_start_operation(self, operation_id):
        with self.operation_lock:
            if len(self.active_operations) >= self.MAX_CONCURRENT_OPS:
                self.operation_queue.put(operation_id)
                return False
            self.active_operations.add(operation_id)
            return True
    
    def finish_operation(self, operation_id):
        with self.operation_lock:
            self.active_operations.discard(operation_id)
            # Process queued operations
            if not self.operation_queue.empty():
                next_op = self.operation_queue.get()
                self.active_operations.add(next_op)
                return next_op
        return None

operation_manager = OperationManager()
```

### 1.2 Universal Callback Guard Decorator
**Problem:** Callbacks can be triggered rapidly causing race conditions
**Solution:** Add rate limiting and state checking decorator

```python
def callback_guard(max_rate=1.0, check_state=True):
    """Decorator to guard callbacks from rapid triggers and invalid states"""
    def decorator(func):
        last_call = {}
        
        def wrapper(*args, **kwargs):
            # Rate limiting
            call_key = f"{func.__name__}"
            now = time.time()
            if call_key in last_call:
                if now - last_call[call_key] < max_rate:
                    raise PreventUpdate
            last_call[call_key] = now
            
            # State validation
            if check_state:
                ctx = dash.callback_context
                if not ctx.triggered:
                    raise PreventUpdate
                
                # Check for valid trigger
                trigger = ctx.triggered[0]
                if trigger['value'] is None and 'interval' not in trigger['prop_id']:
                    raise PreventUpdate
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Callback {func.__name__} failed: {str(e)}")
                # Return safe defaults instead of crashing
                return_annotation = func.__annotations__.get('return')
                if return_annotation:
                    # Return empty/default values based on expected outputs
                    return get_safe_defaults(return_annotation)
                raise PreventUpdate
        
        return wrapper
    return decorator
```

### 1.3 Input Validation Layer
**Problem:** Invalid inputs can cause unexpected behavior
**Solution:** Add comprehensive input sanitization

```python
def sanitize_ticker_input(ticker_input, max_tickers=20):
    """Sanitize and validate ticker input"""
    if not ticker_input:
        return None
    
    # Remove dangerous characters
    ticker_input = re.sub(r'[^\w\s,.-]', '', ticker_input)
    
    # Parse and validate
    tickers = [t.strip().upper() for t in ticker_input.split(',') if t.strip()]
    
    # Limit number of tickers
    tickers = tickers[:max_tickers]
    
    # Validate ticker format (basic check)
    valid_tickers = []
    for ticker in tickers:
        if 1 <= len(ticker) <= 10:  # Reasonable ticker length
            valid_tickers.append(ticker)
    
    return valid_tickers if valid_tickers else None

def sanitize_numeric_input(value, min_val=1, max_val=1000, default=50):
    """Sanitize numeric input with bounds checking"""
    try:
        num = float(value)
        if min_val <= num <= max_val:
            return num
        return default
    except (TypeError, ValueError):
        return default
```

## Priority 2: State Management Improvements

### 2.1 Callback State Tracker
**Problem:** Lost track of what callbacks are active
**Solution:** Implement callback activity monitoring

```python
class CallbackStateTracker:
    def __init__(self):
        self.active_callbacks = {}
        self.callback_history = deque(maxlen=100)
        self.lock = threading.Lock()
    
    def start_callback(self, callback_id, context):
        with self.lock:
            self.active_callbacks[callback_id] = {
                'start_time': time.time(),
                'context': context
            }
    
    def end_callback(self, callback_id, success=True):
        with self.lock:
            if callback_id in self.active_callbacks:
                duration = time.time() - self.active_callbacks[callback_id]['start_time']
                self.callback_history.append({
                    'id': callback_id,
                    'duration': duration,
                    'success': success,
                    'timestamp': time.time()
                })
                del self.active_callbacks[callback_id]
    
    def get_active_count(self):
        with self.lock:
            return len(self.active_callbacks)
    
    def is_overloaded(self, threshold=10):
        return self.get_active_count() > threshold

callback_tracker = CallbackStateTracker()
```

### 2.2 Smart Debouncing System
**Problem:** Rapid input changes cause callback spam
**Solution:** Implement intelligent debouncing

```python
class SmartDebouncer:
    def __init__(self):
        self.pending = {}
        self.timers = {}
    
    def debounce(self, key, value, callback, delay=0.5):
        """Debounce a callback with a specific key"""
        # Cancel existing timer
        if key in self.timers:
            self.timers[key].cancel()
        
        # Create new timer
        timer = threading.Timer(delay, lambda: self._execute(key, value, callback))
        self.timers[key] = timer
        self.pending[key] = value
        timer.start()
    
    def _execute(self, key, value, callback):
        if key in self.pending:
            del self.pending[key]
            del self.timers[key]
            callback(value)
    
    def cancel_all(self):
        for timer in self.timers.values():
            timer.cancel()
        self.pending.clear()
        self.timers.clear()

debouncer = SmartDebouncer()
```

## Priority 3: UI Protection Mechanisms

### 3.1 Element Interaction Safety Wrapper
**Problem:** Elements become non-interactable
**Solution:** Add interaction safety checks

```python
@app.callback(
    Output('interaction-safety-store', 'data'),
    Input('interval-component', 'n_intervals'),
    prevent_initial_call=True
)
def check_ui_safety(n):
    """Monitor UI state for safety"""
    return {
        'modals_open': check_modals_open(),
        'overlays_active': check_overlays_active(),
        'safe_to_interact': True,
        'timestamp': time.time()
    }

def check_modals_open():
    """Check if any modals are blocking interaction"""
    # This would need clientside callback to check DOM
    return False

def check_overlays_active():
    """Check if any overlays are active"""
    # This would need clientside callback to check DOM
    return False
```

### 3.2 Clientside Callback Protection
**Problem:** JavaScript errors can break the UI
**Solution:** Add clientside error handling

```javascript
// Add to assets/custom.js
window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clientside: {
        safe_interaction: function(n_clicks, element_id) {
            try {
                // Check if element exists and is interactable
                const element = document.getElementById(element_id);
                if (!element) return {error: 'Element not found'};
                
                const rect = element.getBoundingClientRect();
                const isVisible = rect.top >= 0 && rect.bottom <= window.innerHeight;
                const isEnabled = !element.disabled;
                const isNotCovered = document.elementFromPoint(
                    rect.left + rect.width/2, 
                    rect.top + rect.height/2
                ) === element;
                
                if (!isVisible || !isEnabled || !isNotCovered) {
                    return {error: 'Element not interactable', safe: false};
                }
                
                return {safe: true};
            } catch(e) {
                console.error('Interaction safety check failed:', e);
                return {error: e.message, safe: false};
            }
        }
    }
});
```

## Priority 4: Resource Management

### 4.1 Memory Leak Prevention
**Problem:** Uncleaned intervals and callbacks
**Solution:** Implement cleanup manager

```python
class ResourceCleanupManager:
    def __init__(self):
        self.intervals = set()
        self.threads = []
        self.callbacks = {}
    
    def register_interval(self, interval_id):
        self.intervals.add(interval_id)
    
    def register_thread(self, thread):
        self.threads.append(thread)
        # Clean up dead threads
        self.threads = [t for t in self.threads if t.is_alive()]
    
    def cleanup_all(self):
        """Clean up all resources"""
        # Cancel all threads
        for thread in self.threads:
            if thread.is_alive():
                # Set cancel flag if available
                pass
        
        # Clear intervals (would need clientside callback)
        self.intervals.clear()
        
        # Clear callbacks
        self.callbacks.clear()

cleanup_manager = ResourceCleanupManager()
```

### 4.2 Cache Size Management
**Problem:** Unlimited cache growth
**Solution:** Implement cache limits

```python
def enforce_cache_limits():
    """Enforce size limits on all caches"""
    global optimization_results_cache, _precomputed_results_cache
    
    # Limit optimization cache
    MAX_OPT_CACHE = 50
    if len(optimization_results_cache) > MAX_OPT_CACHE:
        # Remove oldest entries
        items = list(optimization_results_cache.items())
        optimization_results_cache = dict(items[-MAX_OPT_CACHE:])
    
    # Limit precomputed results cache
    MAX_RESULTS_CACHE = 100
    if len(_precomputed_results_cache) > MAX_RESULTS_CACHE:
        items = list(_precomputed_results_cache.items())
        _precomputed_results_cache = dict(items[-MAX_RESULTS_CACHE:])
```

## Priority 5: Error Recovery Mechanisms

### 5.1 Automatic Recovery System
**Problem:** Errors leave app in broken state
**Solution:** Add self-healing mechanisms

```python
class ErrorRecoverySystem:
    def __init__(self):
        self.error_counts = {}
        self.recovery_actions = {
            'optimization_stuck': self.reset_optimization,
            'batch_process_hung': self.reset_batch_process,
            'callback_timeout': self.reset_callback
        }
    
    def record_error(self, error_type, details):
        if error_type not in self.error_counts:
            self.error_counts[error_type] = []
        
        self.error_counts[error_type].append({
            'time': time.time(),
            'details': details
        })
        
        # Check if recovery needed
        recent_errors = [e for e in self.error_counts[error_type] 
                        if time.time() - e['time'] < 60]
        
        if len(recent_errors) >= 3:
            self.trigger_recovery(error_type)
    
    def trigger_recovery(self, error_type):
        if error_type in self.recovery_actions:
            logger.warning(f"Triggering recovery for {error_type}")
            self.recovery_actions[error_type]()
    
    def reset_optimization(self):
        global optimization_in_progress, pending_optimization
        optimization_in_progress = False
        pending_optimization = None
        optimization_results_cache.clear()
    
    def reset_batch_process(self):
        # Clear batch process state
        pass
    
    def reset_callback(self):
        # Reset callback state
        pass

recovery_system = ErrorRecoverySystem()
```

## Implementation Priority Order

1. **Week 1: Critical Stability**
   - Implement OperationManager
   - Add callback_guard decorator to all callbacks
   - Add input sanitization

2. **Week 2: State Management**
   - Implement CallbackStateTracker
   - Add SmartDebouncer
   - Add state validation

3. **Week 3: UI Protection**
   - Add clientside safety checks
   - Implement interaction safety wrapper
   - Add modal/overlay detection

4. **Week 4: Resource Management**
   - Implement ResourceCleanupManager
   - Add cache size limits
   - Add memory monitoring

5. **Week 5: Error Recovery**
   - Implement ErrorRecoverySystem
   - Add automatic recovery triggers
   - Add health monitoring dashboard

## Testing Plan

1. **Unit Tests**: Test each component in isolation
2. **Integration Tests**: Test component interactions
3. **Chaos Tests**: Run modified chaos test after each implementation
4. **Load Tests**: Test with high concurrent users
5. **Endurance Tests**: Run for extended periods

## Success Metrics

- Zero crashes during 1-hour chaos test
- <100ms callback response time under load
- <500MB memory usage after 24 hours
- Automatic recovery from 95% of errors
- Zero data corruption incidents

## Maintenance Plan

1. **Monitoring**: Add comprehensive logging and metrics
2. **Alerts**: Set up alerts for error thresholds
3. **Documentation**: Document all safety mechanisms
4. **Regular Testing**: Weekly chaos tests
5. **Performance Reviews**: Monthly performance audits