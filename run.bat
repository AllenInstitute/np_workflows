@ECHO OFF
cd /d "%~dp0"
CALL update.bat
CALL install.bat
CALL run_router.bat
CALL run_workflow.bat