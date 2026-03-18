@echo off
REM Create and use a venv in the repo root. Run from repo root: scripts\setup_venv.bat

cd /d "%~dp0\.."
if not exist "venv" (
  python -m venv venv
  echo Created venv at %CD%\venv
)
call venv\Scripts\activate.bat
pip install -e .
pip install -r requirements.txt
echo Done. Activate with: venv\Scripts\activate.bat
