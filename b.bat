robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E

set sourcefolder=.\np\workflows\
set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows\

del %destfolder%\*.wfl

@REM for /f %%d in ('dir %sourcefolder% /b /ad ') do (
@REM     robocopy %sourcefolder%\%%d %destfolder% *.wfl /s /xf dynamic_routing.wfl
@REM )
robocopy .\np\workflows\illusion %destfolder% demo.wfl 
CALL run_router.bat
CALL run_workflow.bat

