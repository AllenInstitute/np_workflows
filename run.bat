@REM taskkill /f /im "C:\Program Files\AIBS_MPE\workflow_launcher\workflow_launcher.exe"
@REM @REM taskkill /f /im "WSE 2.1"

CALL install.bat
@REM start "" /d "C:\Program Files\AIBS_MPE\workflow_launcher" "C:\Program Files\AIBS_MPE\workflow_launcher\workflow_launcher.exe"
call "cmd /c start C:\progra~1\AIBS_MPE\workflow_launcher\workflow_launcher.exe"
EXIT /B