# Batch Files Analysis - local_optimization/batch_files/

## Summary: 25 batch files total

### 🟢 KEEP - Essential Files (7)

1. **LAUNCH_OPTIMIZED.bat** ✅
   - Main launcher with environment & threading control
   - **PRIMARY LAUNCHER - KEEP**

2. **run_spymaster_desktop.bat** ✅
   - Direct spymaster launcher with P-8 config
   - Useful for quick launches without menu

3. **run_impactsearch_desktop.bat** ✅
   - Direct impactsearch launcher with P-8 config
   - Useful for quick launches

4. **run_impactsearch_desktop_interactive.bat** ✅
   - Interactive mode for impactsearch
   - Referenced by LAUNCH_OPTIMIZED.bat

5. **run_onepass_desktop.bat** ✅
   - Direct onepass launcher with P-8 config
   - Useful for quick launches

6. **run_gtl_desktop.bat** ✅
   - Global Ticker Library validation launcher
   - Useful for quick launches

7. **test_ticker.bat** ✅
   - Quick ticker testing utility
   - Useful for debugging

### 🟡 KEEP - Utility Files (4)

8. **verify_mkl.bat** 🔧
   - Verifies MKL installation
   - Useful for troubleshooting

9. **run_benchmark_mkl.bat** 🔧
   - Performance benchmarking
   - Referenced by LAUNCH_OPTIMIZED.bat

10. **test_fastpath_mode.bat** 🔧
    - Tests fast-path implementation
    - Useful for impactsearch debugging

11. **test_hyd_raw_mode.bat** 🔧
    - Tests HYD/RAW mode configurations
    - Useful for debugging price basis issues

### 🔴 DELETE - Redundant/Obsolete Files (14)

12. **LAUNCH_OPTIMIZED_BACKUP.bat** ❌
    - Backup of old version
    - No longer needed

13. **LAUNCH_OPTIMIZED_MKL_CONTROL.bat** ❌
    - Old version before V2
    - Superseded by current LAUNCH_OPTIMIZED.bat

14. **LAUNCH_OPTIMIZED_V2.bat** ❌
    - Development version
    - Already copied to LAUNCH_OPTIMIZED.bat

15. **run_spymaster_desktop_STEP_A_AUTO.bat** ❌
    - Test configuration from optimization testing
    - No longer needed

16. **run_spymaster_desktop_STEP_C_P8.bat** ❌
    - Test configuration from optimization testing
    - P-8 config already in main file

17. **run_impactsearch_desktop_P8.bat** ❌
    - Duplicate P-8 version
    - Main file already has P-8

18. **run_onepass_desktop_P8.bat** ❌
    - Duplicate P-8 version
    - Main file already has P-8

19. **run_gtl_desktop_P8.bat** ❌
    - Duplicate P-8 version
    - Main file already has P-8

20. **create_mkl_env.bat** ❌
    - Environment creation script
    - Environments already exist

21. **fix_mkl_now.bat** ❌
    - Old MKL fix script
    - Issues already resolved

22. **install_yfinance_mkl.bat** ❌
    - Package installation script
    - Packages already installed

23. **quick_fix_plotly.bat** ❌
    - Old plotly fix
    - Issue already resolved

24. **quick_update_critical.bat** ❌
    - Old update script
    - Updates already applied

25. **update_all_packages.bat** ❌
    - Package updater
    - Can be dangerous, manual updates preferred

## Recommendation

### Keep 11 files:
- 1 primary launcher (LAUNCH_OPTIMIZED.bat)
- 5 direct launchers (spymaster, impactsearch, impactsearch_interactive, onepass, gtl)
- 5 utility/test files

### Delete 14 files:
- All backup/old versions
- All duplicate P-8 versions
- All one-time setup/fix scripts

This would reduce from 25 files to 11 files, making the folder much cleaner and easier to navigate.

## Proposed Clean Structure:
```
batch_files/
├── LAUNCH_OPTIMIZED.bat          # Main launcher
├── run_spymaster_desktop.bat     # Direct launchers
├── run_impactsearch_desktop.bat
├── run_impactsearch_desktop_interactive.bat
├── run_onepass_desktop.bat
├── run_gtl_desktop.bat
├── test_ticker.bat                # Testing utilities
├── test_fastpath_mode.bat
├── test_hyd_raw_mode.bat
├── verify_mkl.bat                 # Verification
└── run_benchmark_mkl.bat          # Benchmarking
```