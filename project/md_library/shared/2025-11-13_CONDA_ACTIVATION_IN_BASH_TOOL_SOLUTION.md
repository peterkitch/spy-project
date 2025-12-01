# Conda Environment Activation in Bash Tool - Complete Solution

**Date**: 2025-11-13
**Issue**: Repeated failures to activate conda environments when running Python scripts
**Scope**: Cross-cutting issue affecting all scripts (spymaster, impactsearch, onepass, confluence, GTL)

## The Problem

When attempting to run Python scripts that require conda environments, the following errors occur:

### Error Pattern 1: ModuleNotFoundError
```
ModuleNotFoundError: No module named 'dash'
```
This happens when running Python without activating the conda environment first.

### Error Pattern 2: Command Not Found
```
/usr/bin/bash: line 1: call: command not found
```
This happens when trying to use Windows CMD syntax in the Bash tool.

## Root Cause Analysis

### Critical Discovery: The Bash Tool Uses Git Bash, NOT Windows CMD

**Shell Type**: `/usr/bin/bash` (Unix-like shell)
**NOT**: Windows Command Prompt (cmd.exe)
**NOT**: PowerShell

This is the fundamental issue that causes all conda activation problems!

### Why Common Approaches Fail

| Approach | Why It Fails |
|----------|--------------|
| `call C:\path\to\activate.bat` | `call` is a CMD command, not available in bash |
| `conda activate env_name` | `conda` is not in the bash PATH by default |
| `C:\path\to\activate.bat` | `.bat` files require CMD to execute |
| Using backslashes in paths | Bash uses forward slashes; backslashes need escaping |

### Environment Specifics

- **Conda Location**: `/c/Users/sport/AppData/Local/NVIDIA/MiniConda/`
- **Activate Script**: `/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate`
- **Primary Environment**: `spyproject2` (has Intel MKL, NumPy 1.26.4, Dash, Plotly, all dependencies)
- **Alternative Environment**: `spyproject2_basic` (generic BLAS, NumPy 2.2.6)

## The Correct Solution

### For Single Commands

Use `source` to activate conda, then run your command:

```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python script.py
```

### For Background/Long-Running Processes

Same pattern with background flag:

```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python confluence.py
# Run in background with run_in_background=true parameter
```

### Key Elements

1. **Use `source`** (or `.` in bash) - NOT `call`
2. **Unix-style paths** with forward slashes: `/c/Users/...`
3. **Quote the path** to handle spaces: `"/c/Users/sport/AppData/..."`
4. **Chain with `&&`** to ensure activation succeeds before running Python
5. **Specify environment name** after the activate script: `spyproject2`

## Verification Steps

### Step 1: Verify Conda Activate Exists
```bash
ls -la "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate"
# Should show: -rwxr-xr-x ... activate
```

### Step 2: Test Activation
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && which python
# Should show: /c/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python
```

### Step 3: Verify Modules
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python -c "import dash; import plotly; print('Modules available')"
# Should print: Modules available
```

### Step 4: Run Script
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python confluence.py
# Should start successfully
```

## Common Mistakes to Avoid

### ❌ WRONG: Using Windows CMD Syntax
```bash
call C:\Users\sport\AppData\Local\NVIDIA\MiniConda\Scripts\activate.bat spyproject2
# Error: call: command not found
```

### ❌ WRONG: Using conda command directly
```bash
conda activate spyproject2 && python script.py
# Error: conda: command not found
```

### ❌ WRONG: Windows-style paths with backslashes
```bash
source "C:\Users\sport\AppData\Local\NVIDIA\MiniConda\Scripts\activate" spyproject2
# May cause path interpretation issues
```

### ❌ WRONG: Forgetting to chain with &&
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2
python script.py
# Second command runs in a separate shell without activation
```

### ✅ CORRECT: Unix-style with source and chaining
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python script.py
```

## Script-Specific Examples

### Spymaster
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python spymaster.py
# Runs on port 8050 (default)
```

### ImpactSearch
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python impactsearch.py
# Runs on port 8051
```

### OnePass
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python onepass.py
```

### Confluence
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python confluence.py
# Runs on port 8056 (or fallback 8057 if occupied)
```

### Global Ticker Library
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && cd global_ticker_library && python run.py --validate-manual
```

## Testing and Validation

When testing scripts, always:

1. **Kill existing processes** first (if testing web apps):
   ```bash
   taskkill /F /IM python.exe
   ```

2. **Clear cache** if needed (especially for Selenium tests):
   ```bash
   rm -rf cache
   rm -f *.pkl *.json
   ```

3. **Activate environment and run**:
   ```bash
   source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python script.py
   ```

4. **Check output** for successful startup:
   - Web apps: Look for "Dash is running on http://..."
   - Scripts: Look for expected output without import errors

## Troubleshooting

### Issue: "No such file or directory"
**Solution**: Check path format - use `/c/Users/...` not `C:\Users\...`

### Issue: "conda: command not found"
**Solution**: Use full path to activate script with `source`, don't rely on conda being in PATH

### Issue: "Modules still not found after activation"
**Solution**: Verify `which python` shows conda env path, not system Python

### Issue: "Script starts but imports fail"
**Solution**: Ensure `&&` is used to chain activation with script execution

## Quick Reference Card

**ALWAYS USE THIS PATTERN:**
```bash
source "/c/Users/sport/AppData/Local/NVIDIA/MiniConda/Scripts/activate" spyproject2 && python <SCRIPT_NAME>.py
```

**NEVER USE:**
- `call` command
- `conda activate` directly
- Windows-style paths with backslashes
- Separate commands without `&&`

## Related Documentation

- Unicode/Console Issues: `md_library/shared/2025-08-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md`
- Selenium Testing: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- Performance Optimization: `md_library/shared/2025-01-15_MKL_THREAD_OPTIMIZATION_BASELINE_TESTS.md`

## Status

✅ **VERIFIED WORKING** as of 2025-11-13
- Confluence app successfully started on port 8057
- All modules (dash, plotly, pandas, numpy) available
- Pattern confirmed working across all project scripts
