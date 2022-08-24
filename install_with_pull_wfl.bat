:: we're using slightly different workflow code depending on which rig we're on
:: code for each rig is on a branch in a github repo

SET rig=%AIBS_RIG_ID%
SET branch=np

IF %rig%==NP.2 SET branch=main
IF %rig%==NP.1 SET branch=main
IF %rig%==NP.0 SET branch=main

git checkout %branch%
git pull origin %branch%

@REM install workflow code to program files: completely replaces existing directory structure
robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E

@REM install wfl files to default location 
set sourcefolder=.\np\workflows
set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows

del %destfolder%\*.wfl
@REM include *remaster.wfl, exclude dynamic_routing.wfl:
for /f %%d in ('dir %sourcefolder% /b /ad ') do (
    robocopy %sourcefolder%\%%d %destfolder% *remaster.wfl /s /xf dynamic_routing.wfl 
)

@REM alternatively we can point to specific project folders in program files:

@REM on NP.1 we want both passive and vis behav workflows
IF %rig%==NP.0 SET AIBS_WSE_WFLS=%destfolder%

@REM doesn't work on 1 - likely permissions need changing
@REM IF %rig%==NP.1 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\behavior\

IF %rig%==NP.2 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\passive\

