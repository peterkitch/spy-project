# Claude Testing Solution: Windows Python Path Format

**Date**: 2025-10-22
**Context**: Running automated tests from Claude Code on Windows with conda environments

## Problem

Claude's Bash tool was unable to run Python tests in the conda environment despite multiple attempts using:
- `cmd /c "activate.bat && python script.py"` - exits immediately, no output
- `C:\Users\...\python.exe` - path not found (backslashes interpreted by bash)
- Background shells - not maintaining environment

## Solution

**Use forward slashes in the full Python executable path:**

```bash
C:/Users/<USERNAME>/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe
```

### Why This Works

1. **Forward slashes** are interpreted correctly by bash (which underlies the Bash tool)
2. **Full path** bypasses need for conda activation
3. **Direct executable call** avoids shell environment issues

### Example Usage

```bash
cd signal_library && C:/Users/<USERNAME>/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe ../quick_parity_test.py --ticker SPY --interval 1d
```

## Standard Test Command Pattern

For any Python test script in the spyproject2 environment:

```bash
cd [working_directory] && C:/Users/<USERNAME>/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe [script_path] [args]
```

### Key Points

- **Always use forward slashes** in Windows paths when calling from bash
- **Change to appropriate working directory first** if script expects it
- **Use relative paths** for the script argument
- **Full conda environment path** ensures correct Python version and packages

## Tested Successfully

- ✅ SPY 1d parity test (8,239 bars)
- ✅ QQQ 1d parity test (6,697 bars)
- ✅ AAPL 1d parity test (11,306 bars)

All tests showed perfect parity (0.00e+00 difference) between original and vectorized implementations.

## Related Files

- Test script: `quick_parity_test.py`
- Full test suite: `test_scripts/shared/test_vectorized_parity.py`
- Implementation: `signal_library/multi_timeframe_builder.py`
