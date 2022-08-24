:: we're using slightly different workflow code depending on which rig we're on
:: code for each rig is on a branch in a github repo

SET rig=%AIBS_RIG_ID%

IF %rig%==NP.0 SET branch=dev
IF %rig%==NP.1 SET branch=main
IF %rig%==NP.2 SET branch=main

CALL git stash
CALL git checkout %branch%

@REM install workflow code to program files: completely replaces existing directory structure
robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E

@REM install wfls -------------------------------------------------------------------------
@REM we can point the WSE to workflows for specific project folders only:

set sourcefolder=.\np\workflows

@REM np0 ----------------------------------------------------------------------------------
@REM on NP.1 we want both passive and vis behav workflows
IF %rig%==NP.0 (
    @REM install wfl files to non-default location, to leave current WSE operable for now
    set destfolder=c:\ProgramData\AIBS_MPE\wfltk\new_workflows
    del %destfolder%\*.wfl
    @REM include *remaster.wfl, exclude dynamic_routing.wfl:
    for /f %%d in ('dir %sourcefolder% /b /ad ') do (
        robocopy %sourcefolder%\%%d %destfolder% *remaster.wfl /s /xf dynamic_routing.wfl 
    )
    SET AIBS_WSE_WFLS=%destfolder%
)

@REM np1 ----------------------------------------------------------------------------------
@REM setting env var doesn't work on 1 - likely permissions need changing
@REM IF %rig%==NP.1 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\behavior\

IF %rig%==NP.1 (
    @REM install wfl files to default location, to leave current WSE operable for now
    set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows
    del %destfolder%\*.wfl
    @REM include *remaster.wfl, exclude dynamic_routing.wfl:
    for /f %%d in ('dir %sourcefolder% /b /ad ') do (
        robocopy %sourcefolder%\%%d %destfolder% *remaster.wfl /s /xf dynamic_routing.wfl 
    )
    @REM setting env var doesn't work on 1 - likely permissions need changing
    SET AIBS_WSE_WFLS=%destfolder%
)

@REM np2 ----------------------------------------------------------------------------------
IF %rig%==NP.2 (
    @REM no need to copy wfls to programdata, since we already copied them to programfiles
    @REM and we only need passive workflows at the moment
    AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\passive\
)