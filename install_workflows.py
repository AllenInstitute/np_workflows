import pathlib
import shutil 

# TODO 
# - move Rig config to np_config
# - get rig-specific list of wfls from np_config

CWD = pathlib.Path('./src').resolve()
RELATIVE = 'np_workflows/workflows'
LOCAL = CWD / RELATIVE
PROGRAM_FILES = pathlib.Path('C:/Program Files/AIBS_MPE/workflow_launcher') / RELATIVE
PROGRAM_DATA = pathlib.Path('C:/ProgramData/AIBS_MPE/wfltk/workflows')

# wfls to program data (WSE looks here by default)
for wfl in PROGRAM_DATA.glob('*.wfl'):
    wfl.unlink()
for wfl in (LOCAL).rglob('pretest.wfl'):
    src = wfl
    dest = PROGRAM_DATA / wfl.name
    print(f'{src} to {dest}')
    shutil.copy2(src, dest)

# everything else to program files (WSE looks here for all other files)
shutil.copytree(LOCAL.parent, PROGRAM_FILES.parent, dirs_exist_ok=True)