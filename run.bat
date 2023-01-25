@ECHO OFF
cd /d "%~dp0"
@REM CALL update.bat
@REM CALL install.bat
CALL python install_workflows.py
CALL run_router.bat
CALL run_workflow.bat