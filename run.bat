@ECHO OFF
cd /d "%~dp0"
@REM CALL update.bat
@REM CALL install.bat
SET AIBS_RIG_ID=NP.2
CALL python install_workflows.py
CALL run_router.bat
CALL run_workflow.bat