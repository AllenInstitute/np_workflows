"""
For each *.wfl in np/workflows/, start the WSE with only the one workflow available
(so it loads automatically in the WSE GUI) and detect fundamental syntax/startup
problems in the .wfl or companion .py file. 

Such problems may be caught by the WSE machinery and allowed to continue regardless 
(e.g. import errors), so it may be difficult to reason about the actual cause from the
exception that's ultimately raised by the WSE.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
import shutil
import subprocess
import sys
import time

import pytest

if 'np-' not in os.environ.get('AIBS_RIG_ID','').lower():
    os.environ['AIBS_RIG_ID'] = 'BTVTest.1'
    
NP_DEV_ROOT = pathlib.Path(__file__).parent.parent
"`np` folder in Git repo - mirrored to `Program Files` before running WSE"
NP_WSE_ROOT = pathlib.Path(R"c:\progra~1\AIBS_MPE\workflow_launcher\np").resolve()
"`np` folder in `Program Files/AIBS_MPE/workflow_launcher/` - used by WSE"
BAT_ROOT = NP_DEV_ROOT.parent

NP_WFLS = tuple((NP_DEV_ROOT / 'workflows').rglob('*.wfl'))

PROGRAM_DATA_WFLS_ROOT = pathlib.Path("c:/ProgramData/AIBS_MPE/wfltk/workflows").resolve()


# @pytest.skip   
def start_router():
    "Stays running once started"
    cmd = [str(BAT_ROOT / 'run_router.bat')]
    return subprocess.Popen(cmd)

# @pytest.skip   
def copy_dev_to_program_files() -> int:
    cmd = ["robocopy"]
    cmd += [f"{NP_DEV_ROOT}" + "\\"]
    cmd += [f"{NP_WSE_ROOT}" + "\\"]
    cmd += ["/MIR"] 
    cmd += ["/E"]
    return subprocess.run(cmd).returncode


# @pytest.skip   
def setup():
    copy_dev_to_program_files()
    start_router()

def test_copy():
    assert copy_dev_to_program_files() < 2
    
def launch_wse():
    setup()
    time.sleep(1)
    return subprocess.Popen(
        args=["C:/progra~1/AIBS_MPE/workflow_launcher/workflow_launcher.exe"],
        cwd="C:/progra~1/AIBS_MPE/workflow_launcher",
        )
    
def prepare_test_wfl_dir() -> pathlib.Path:
    path = PROGRAM_DATA_WFLS_ROOT.parent / 'test_workflows'
    path.mkdir(parents=True, exist_ok=True)
    for wfl in path.rglob('*.wfl'):
        wfl.unlink()
    return path

def set_wse_to_use_test_wfl_dir(wfl_dir: str|pathlib.Path) -> None:
    os.environ['AIBS_WSE_WFLS'] = str(wfl_dir)
    assert os.path.isdir(path := os.environ.get('AIBS_WSE_WFLS')), f'AIBS_WSE_WFLS not a dir: {path}'

def kill_running_wse():
    subprocess.run(['taskkill /f /im "C:\progra~1\AIBS_MPE\workflow_launcher\workflow_launcher.exe"'])
    
@pytest.mark.parametrize("wfl_path", NP_WFLS)
def test_single_workflow(wfl_path):
    tmp_dir = prepare_test_wfl_dir()
    set_wse_to_use_test_wfl_dir(tmp_dir)
    shutil.copy2(wfl_path, tmp_dir)
    wse = launch_wse()
    time.sleep(10)
    wse.terminate()
    

# kill_running_wse()
test_single_workflow(NP_WFLS[0])

# test_each_workflow()
#     cmd = f"SET AIBS_WSE_WFLS={}"
    
#     @REM include *remaster.wfl, exclude dynamic_routing.wfl:
#     for /f %%d in ('dir %sourcefolder% /b /ad ') do (
#         robocopy %sourcefolder%\%%d C:\ProgramData\AIBS_MPE\wfltk\new_workflows *remaster.wfl /s /xf dynamic_routing.wfl 
#     )
#     SET AIBS_WSE_WFLS=C:\ProgramData\AIBS_MPE\wfltk\new_workflows
#     ECHO Setting WSE .wfl target directory: C:\ProgramData\AIBS_MPE\wfltk\new_workflows
# )

# @REM np1 ----------------------------------------------------------------------------------
# @REM setting env var doesn't work on 1 - likely permissions need changing
# @REM IF %rig%==NP.1 SET AIBS_WSE_WFLS=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\behavior\

# IF %rig%==NP.1 (
#     @REM install wfl files to default location
#     set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows
#     del %destfolder%\*.wfl
#     @REM include *remaster.wfl, exclude dynamic_routing.wfl:
#     for /f %%d in ('dir %sourcefolder% /b /ad ') do (
#         robocopy %sourcefolder%\%%d c:\ProgramData\AIBS_MPE\wfltk\workflows *remaster.wfl /s /xf dynamic_routing.wfl *passive*
#     )
#     @REM setting env var doesn't work on np1 - likely permissions need changing
#     SET AIBS_WSE_WFLSc:\ProgramData\AIBS_MPE\wfltk\workflows
# )

# @REM np2 ----------------------------------------------------------------------------------
# IF %rig%==NP.2 (
#     @REM no need to copy wfls to programdata, since we already copied them to programfiles
#     @REM and we only need passive workflows at the moment
#     @REM SET destfolder=c:\progra~1\AIBS_MPE\workflow_launcher\np\workflows\passive
#     @REM SET AIBS_WSE_WFLS=%destfolder%

#     @REM install wfl files to default location
#     set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows
#     del %destfolder%\*.wfl
#     @REM include *remaster.wfl, exclude dynamic_routing.wfl:
#     for /f %%d in ('dir %sourcefolder% /b /ad ') do (
#         robocopy %sourcefolder%\%%d c:\ProgramData\AIBS_MPE\wfltk\workflows *remaster.wfl /s /xf dynamic_routing.wfl 
#     )
#     SET AIBS_WSE_WFLS=c:\ProgramData\AIBS_MPE\wfltk\workflows
# )