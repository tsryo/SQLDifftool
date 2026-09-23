@echo off
rem Starts SQL Difftool from the project's virtual environment. Extra arguments are passed on (e.g. --demo).
pushd "%~dp0"
".venv\Scripts\python.exe" -m sqldifftool %*
popd
