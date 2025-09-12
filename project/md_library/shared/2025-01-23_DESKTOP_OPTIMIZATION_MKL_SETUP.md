# Desktop Optimization Guide - MKL Setup for 4.5x Performance

## Overview
This guide documents how to optimize PRJCT9 scripts for maximum performance on desktop systems using Intel Math Kernel Library (MKL) instead of OpenBLAS. The optimization achieved **4.5x faster performance** on NumPy/SciPy operations.

## Problem Identified
- Default conda environments often use OpenBLAS with limited thread count (MAX_THREADS=2)
- This severely limits performance on multi-core desktop systems
- Switching to MKL allows full CPU utilization (24+ threads)

## Performance Results

### Benchmark Comparison (24-core Desktop)
| Operation | OpenBLAS (2 threads) | MKL (24 threads) | Improvement |
|-----------|---------------------|------------------|-------------|
| Matrix Multiplication (6000x6000) | 1.54s | 0.63s | **2.4x faster** |
| SVD (3000x3000) | 15.83s | 3.48s | **4.5x faster** |
| Eigenvalues (4000x4000) | 9.35s | 1.86s | **5.0x faster** |
| **Total** | **26.71s** | **5.97s** | **4.5x faster** |

### Real-World Impact
- ^GSPC (S&P 500) analysis: Completed in **26 seconds** with MKL optimization
- Processing 24,540 trading days with 12,882 SMA pairs

## Setup Instructions

### 1. Create MKL-Optimized Conda Environment

```bash
# Create new environment with MKL from defaults channel
conda create -n spyproject2_mkl -c defaults python=3.12 -y

# Activate the environment
conda activate spyproject2_mkl

# Install MKL-backed NumPy, SciPy, and Pandas
conda install -c defaults "numpy=1.26.4" "scipy=1.13.1" "pandas>=2.2" mkl mkl-service -y

# Install remaining requirements (preserving MKL)
pip install -r requirements.txt --no-deps
```

### 2. Install Missing Dependencies

Some packages may need their dependencies installed separately:

```bash
pip install curl_cffi pyquery w3lib fake-useragent appdirs typing_extensions
```

### 3. Configure Windows for Maximum Performance

#### Set Ultimate Performance Power Plan:
```powershell
# Create and activate Ultimate Performance plan
powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61
powercfg -setactive [GUID from above command]

# Set minimum processor state to 100%
powercfg -setacvalueindex [GUID] 54533251-82be-4824-96c1-47b60b740d00 893dee8e-2bef-41e0-89c6-b55d0929964c 100
```

### 4. Create Optimized Launcher Scripts

Create batch files to launch scripts with optimal settings:

```batch
@echo off
REM Example launcher for spymaster.py

REM Activate MKL environment
call conda activate spyproject2_mkl

REM Set threading for maximum performance
set "MKL_NUM_THREADS=24"
set "OMP_NUM_THREADS=24"
set "NUMEXPR_NUM_THREADS=24"
set "MKL_DYNAMIC=FALSE"

REM Set app-specific flags
set "PRICE_BASIS=raw"
set "USE_RAW_CLOSE=1"

REM Launch application
python spymaster.py
```

## Verification

### Check NumPy Configuration
```python
import numpy as np
np.__config__.show()
```

Look for:
- **Good**: `"blas": {"name": "mkl-sdl"}`
- **Bad**: `"blas": {"name": "openblas64", ... "MAX_THREADS=2"}`

### Run Performance Benchmark
```python
import numpy as np
import time

n = 6000
a = np.random.rand(n, n)
b = np.random.rand(n, n)

start = time.perf_counter()
c = a @ b
duration = time.perf_counter() - start

print(f"6000x6000 matrix multiplication: {duration:.2f}s")
# Should be <1 second with MKL, >1.5 seconds with OpenBLAS-2-threads
```

## Troubleshooting

### Issue: Plotly Version Incompatibility
If you see errors about `titlefont` vs `title_font`:
```bash
pip install plotly==5.20.0
```

### Issue: Missing Dependencies
If imports fail after `--no-deps` installation:
```bash
pip install [missing_package]
```

### Issue: Still Using OpenBLAS
Ensure you're using the conda `defaults` channel, not `conda-forge`:
```bash
conda install -c defaults numpy scipy pandas mkl mkl-service --force-reinstall
```

## Local Files Organization

Machine-specific optimization files should be kept in `local_optimization/` (gitignored):

```
local_optimization/
├── batch_files/         # Windows batch launchers
├── setup_scripts/       # MKL setup Python scripts
└── benchmarks/          # Performance testing scripts
```

## Environment Variables

### Force RAW Close Prices (not Adjusted)
```batch
set PRICE_BASIS=raw
set USE_RAW_CLOSE=1
```

### Threading Configuration
```batch
set MKL_NUM_THREADS=24      # Adjust to your CPU core count
set OMP_NUM_THREADS=24
set NUMEXPR_NUM_THREADS=24
set MKL_DYNAMIC=FALSE
```

## Key Takeaways

1. **OpenBLAS with MAX_THREADS=2 is a major bottleneck** on multi-core systems
2. **MKL provides 4.5x performance improvement** for numerical operations
3. **No code changes required** - only environment configuration
4. **Machine-specific files should be gitignored** to keep repo clean
5. **Document the process** so others can replicate the optimization

## Additional Resources

- [Intel MKL Documentation](https://software.intel.com/content/www/us/en/develop/documentation/mkl-linux-developer-guide/top.html)
- [NumPy Performance Tips](https://numpy.org/doc/stable/user/c-info.python-as-glue.html)
- [Conda Performance Optimization](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html)