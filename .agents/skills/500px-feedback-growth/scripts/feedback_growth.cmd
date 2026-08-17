@echo off
setlocal
set "PYTHONUTF8=1"

set "ENTRYPOINT=%~dp0feedback_growth.py"
for %%I in ("%~dp0..\..\..\..\.venv\Scripts\python.exe") do set "PROJECT_PYTHON=%%~fI"
set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if exist "%PROJECT_PYTHON%" goto project_python
if exist "%CODEX_PYTHON%" goto codex_python

where py >nul 2>nul
if not errorlevel 1 goto py_launcher
where python3 >nul 2>nul
if not errorlevel 1 goto python3_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_launcher

>&2 echo PressZan requires Python 3.9 or newer. Create .venv or install Python, then retry.
exit /b 9009

:project_python
"%PROJECT_PYTHON%" "%ENTRYPOINT%" %*
exit /b %errorlevel%

:codex_python
"%CODEX_PYTHON%" "%ENTRYPOINT%" %*
exit /b %errorlevel%

:py_launcher
py -3 "%ENTRYPOINT%" %*
exit /b %errorlevel%

:python3_launcher
python3 "%ENTRYPOINT%" %*
exit /b %errorlevel%

:python_launcher
python "%ENTRYPOINT%" %*
exit /b %errorlevel%
