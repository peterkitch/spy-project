# MKL Performance Comparison Results

## Test Date: 2025-01-15

## Executive Summary
MKL provides modest performance improvements (~1.3x average speedup) for the trading analysis scripts. While the benefit is not dramatic, MKL optimization is still recommended for heavy computational workloads.

## Test Results

### Performance Comparison Table

| Test Type | MKL Enabled (8 threads) | MKL Disabled (1 thread) | Speedup |
|-----------|-------------------------|-------------------------|---------|
| **Matrix Multiplication** | 0.25s | 0.46s | **1.9x** |
| **SMA Processing** | 0.59s | 0.58s | 1.0x |
| **Data Operations** | 0.46s | 0.46s | 1.0x |
| **Average Speedup** | - | - | **1.3x** |

### Detailed Breakdown

#### Matrix Multiplication (Pure Compute)
- **500x500**: 42.7 GFLOP/s (MKL) vs 46.4 GFLOP/s (No MKL)
- **1000x1000**: 455.0 GFLOP/s (MKL) vs 65.1 GFLOP/s (No MKL) - **7x faster with MKL**
- **2000x2000**: 504.0 GFLOP/s (MKL) vs 71.4 GFLOP/s (No MKL) - **7x faster with MKL**

#### Key Findings
1. **Large matrix operations**: MKL provides significant speedup (up to 7x)
2. **Small operations**: Minimal difference or sometimes slower with MKL due to threading overhead
3. **I/O-bound operations**: No significant difference
4. **Mixed workloads**: Modest improvements (~1.3x average)

## Updated LAUNCH_OPTIMIZED.bat Features

### New User Controls
The launcher now provides three performance modes:

1. **HIGH PERFORMANCE (MKL Enabled - P-8 Config)**
   - Uses Intel MKL optimizations
   - 8 P-cores configuration
   - Best for heavy computations
   - ~2x faster for compute-intensive tasks

2. **STANDARD PERFORMANCE (MKL Disabled)**
   - Single-threaded operation
   - Lower CPU usage
   - Simpler configuration
   - Suitable for I/O-bound tasks

3. **BENCHMARK Mode**
   - Runs performance comparison
   - Shows actual speedup metrics
   - Helps users decide which mode to use

### How to Use
1. Run `LAUNCH_OPTIMIZED.bat` from `local_optimization\batch_files\`
2. Select performance mode (1, 2, or 3 for benchmark)
3. Choose which script to run (Spymaster, ImpactSearch, OnePass, GTL)
4. Can change MKL mode anytime with option [M]

### MKL Configuration Details

#### When MKL is ENABLED:
```batch
set "MKL_THREADING_LAYER=INTEL"
set "MKL_NUM_THREADS=8"
set "OMP_NUM_THREADS=8"
set "OPENBLAS_NUM_THREADS=8"
set "NUMEXPR_NUM_THREADS=8"
set "MKL_DYNAMIC=FALSE"
set "KMP_HW_SUBSET=8C,1T"
set "KMP_AFFINITY=granularity=fine,compact,1,0"
set "KMP_BLOCKTIME=200"
```

#### When MKL is DISABLED:
```batch
set "MKL_NUM_THREADS=1"
set "OMP_NUM_THREADS=1"
set "OPENBLAS_NUM_THREADS=1"
set "NUMEXPR_NUM_THREADS=1"
set "MKL_DISABLE_FAST_MM=1"
```

## Recommendations

### When to Use MKL (High Performance Mode)
- Running full market scans with spymaster.py
- Processing large batches of tickers
- Heavy SMA pair optimization calculations
- Matrix-heavy computations
- When speed is critical

### When to Use Standard Mode (MKL Disabled)
- Quick single-ticker analysis
- Simple data lookups
- When running on battery (laptop)
- If experiencing stability issues
- For debugging/testing

### General Recommendation
**Keep MKL ENABLED for most use cases**. The performance benefit, while modest on average (1.3x), can be significant for compute-intensive operations (up to 7x for large matrix operations).

## Environment Information
- **Active Environment**: spyproject2 (has actual MKL, despite misleading naming)
- **MKL Version**: Intel MKL 2023.1
- **NumPy**: 1.26.4 (compiled with MKL support)
- **CPU**: Intel i7-13700F (8P+8E cores)

## Files Created
- `test_mkl_comparison.py` - MKL performance comparison test (moved to `utilities/performance_testing/`)
- `LAUNCH_OPTIMIZED.bat` - Updated launcher with MKL control
- `LAUNCH_OPTIMIZED_BACKUP.bat` - Backup of original launcher

## Conclusion
MKL optimization provides measurable benefits, particularly for compute-intensive operations. The new LAUNCH_OPTIMIZED.bat gives users full control over whether to use MKL, allowing them to choose based on their specific workload requirements.