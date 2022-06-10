START /wait taskkill /f /im "C:\progra~1\AIBS_MPE\workflow_launcher\workflow_launcher.exe"
taskkill /f /im "workflow_launcher.exe"
@REM taskkill /f /im "WSE 2.1"

CALL install.bat
start "" /d "C:\progra~1\AIBS_MPE\workflow_launcher" "C:\progra~1\AIBS_MPE\workflow_launcher\workflow_launcher.exe"
@REM call "cmd /c start C:\progra~1\AIBS_MPE\workflow_launcher\workflow_launcher.exe" # needs to run from aibs_mpe/workflow_auncher
EXIT /B