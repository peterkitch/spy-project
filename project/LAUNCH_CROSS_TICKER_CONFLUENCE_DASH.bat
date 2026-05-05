@echo off
set "CROSS_TICKER_CONFLUENCE_PORT=8057"
set "CROSS_TICKER_CONFLUENCE_RUN_ROOT=%~dp0output\cross_ticker_confluence"

set "OPENBLAS_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "OMP_NUM_THREADS=1"
set "NUMEXPR_NUM_THREADS=1"
set "PYTHONHASHSEED=0"

call C:\Users\sport\AppData\Local\NVIDIA\MiniConda\Scripts\activate.bat spyproject2
cd /d "%~dp0"
python cross_ticker_confluence_dash.py --port %CROSS_TICKER_CONFLUENCE_PORT% --run-root "%CROSS_TICKER_CONFLUENCE_RUN_ROOT%"
pause
