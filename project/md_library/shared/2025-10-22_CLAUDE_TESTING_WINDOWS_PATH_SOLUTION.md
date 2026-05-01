# Claude Testing Solution: Windows Python Path Format

> **Historical note (2026-04-30 Phase -1):** this document records an
> early workaround for invoking Python from the Claude Code Bash tool
> on Windows by passing the full path to a Conda env's `python.exe`.
> It is **not** the current recommended setup. The current contributor
> shell is PowerShell 7+ (`pwsh`) and the Python environment should be
> created from `project/environment.yml` and activated with
> `conda activate spyproject2`, after which plain `python` resolves to
> the right interpreter. The literal Conda install paths below have
> been replaced with `<python-executable-from-your-env>` for reference.

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
<python-executable-from-your-env>
```

### Why This Works

1. **Forward slashes** are interpreted correctly by bash (which underlies the Bash tool)
2. **Full path** bypasses need for conda activation
3. **Direct executable call** avoids shell environment issues

### Example Usage

```bash
cd signal_library && <python-executable-from-your-env> ../quick_parity_test.py --ticker SPY --interval 1d
```

## Standard Test Command Pattern

For any Python test script in the spyproject2 environment:

```bash
cd [working_directory] && <python-executable-from-your-env> [script_path] [args]
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
