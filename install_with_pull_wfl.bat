:: we're using slightly different workflow code depending on which rig we're on
:: code for each rig is on a branch in a github repo

SET rig=%AIBS_RIG_ID%
SET branch=np

IF %rig%==NP.2 SET branch=np2
IF %rig%==NP.1 SET branch=np

git checkout %branch%
git pull origin %branch%

robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E

set sourcefolder=.\np\workflows\
set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows\

del %destfolder%\*.wfl
IF %rig%==NP.1 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\behavior\
IF %rig%==NP.2 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\passive\

@REM for /f %%d in ('dir %sourcefolder% /b /ad ') do (
@REM     robocopy %sourcefolder%\%%d %destfolder% *.wfl /s /xf dynamic_routing.wfl 
@REM )
