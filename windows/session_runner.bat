@echo off
rem Session Runner: walks the RA through one session, step by step.
rem   session_runner.bat                 pick the protocol from the list on the first screen
rem   session_runner.bat full-battery    open with this protocol picked (protocols\full-battery.json)
rem   session_runner.bat --demo       every Start opens Notepad (click-through on any PC)
rem Uses the same Python as the Physio Recorder (.venv, pythonw: no console window).
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment not found: %CD%\.venv\Scripts\pythonw.exe
  echo See README, "Setting up a new PC". Tell the researcher.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" session_runner.py %*
