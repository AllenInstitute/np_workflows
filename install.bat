cd /d "%~dp0"

:: we might want to use a different branch (for testing features etc) depending on which rig
:: we're on. if 'update.bat' is called before this script then we'll be on up-to-date master branch, and this
:: script will install the branch the branch set below.
SET rig=%AIBS_RIG_ID%

IF %rig%==NP.0 SET branch=main
IF %rig%==NP.1 SET branch=main
IF %rig%==NP.2 SET branch=main

CALL git stash
CALL git checkout %branch%
CALL git pull %branch%


@REM install python code: services, config, workflow py and wfl files ---------------------

@REM install workflow code to program files: completely replaces existing directory structure
robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E


@REM install wfl files alone in case we can't direct the WSE to program files -------------

@REM we can point the WSE to workflows for specific project folders only:
set sourcefolder=.\np\workflows

@REM np0 ----------------------------------------------------------------------------------
@REM on NP.0 we want both passive and vis behav workflows
IF %rig%==NP.0 (
    @REM install wfl files to non-default location, to leave current WSE operable for now
    set destfolder=C:\ProgramData\AIBS_MPE\wfltk\new_workflows
    del %destfolder%\*.wfl
    @REM include *remaster.wfl, exclude dynamic_routing.wfl:
    for /f %%d in ('dir %sourcefolder% /b /ad ') do (
        robocopy %sourcefolder%\%%d C:\ProgramData\AIBS_MPE\wfltk\new_workflows *remaster.wfl /s /xf dynamic_routing.wfl 
    )
    SET AIBS_WSE_WFLS=C:\ProgramData\AIBS_MPE\wfltk\new_workflows
    ECHO Setting WSE .wfl target directory: C:\ProgramData\AIBS_MPE\wfltk\new_workflows
)

@REM np1 ----------------------------------------------------------------------------------
@REM setting env var doesn't work on 1 - likely permissions need changing
@REM IF %rig%==NP.1 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\behavior\

IF %rig%==NP.1 (
    @REM install wfl files to default location
    set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows
    del %destfolder%\*.wfl
    @REM include *remaster.wfl, exclude dynamic_routing.wfl:
    for /f %%d in ('dir %sourcefolder% /b /ad ') do (
        robocopy %sourcefolder%\%%d c:\ProgramData\AIBS_MPE\wfltk\workflows *remaster.wfl /s /xf dynamic_routing.wfl *passive*
    )
    @REM setting env var doesn't work on np1 - likely permissions need changing
    SET AIBS_WSE_WFLSc:\ProgramData\AIBS_MPE\wfltk\workflows
)

@REM np2 ----------------------------------------------------------------------------------
IF %rig%==NP.2 (
    @REM no need to copy wfls to programdata, since we already copied them to programfiles
    @REM and we only need passive workflows at the moment
    @REM SET destfolder=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\passive
    @REM SET AIBS_WSE_WFLS=%destfolder%

    @REM install wfl files to default location
    set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows
    del %destfolder%\*.wfl
    @REM include *remaster.wfl, exclude dynamic_routing.wfl:
    for /f %%d in ('dir %sourcefolder% /b /ad ') do (
        robocopy %sourcefolder%\%%d c:\ProgramData\AIBS_MPE\wfltk\workflows *remaster.wfl /s /xf dynamic_routing.wfl 
    )
    SET AIBS_WSE_WFLS=c:\ProgramData\AIBS_MPE\wfltk\workflows
)