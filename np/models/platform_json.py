import datetime
import json
import os
import sys
import pathlib
import time
from typing import Dict

try:
    from np.services import config as nptk
except ImportError:
    pass
try:
    sys.path.append("c:/program files/aibs_mpe/workflow_launcher")
    from np.services import config as nptk
except ImportError:
    pass

WSE_DATETIME_FORMAT = '%Y%m%d%H%M%S' # should match the pattern used throughout the WSE

MVR_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/mvr/data")
CAMVIEWER_RELATIVE_PATH = pathlib.Path("C/Users/svc_neuropix/cv3dImages") # NP.0 only
CAMSTIM_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/camstim/output")
SYNC_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/sync/output")

NEUROPIXELS_DATA_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/neuropixels_data")
NPEXP_PATH = pathlib.Path("//allen/programs/mindscope/workgroups/np-exp")

class PlatformJson:
    
    files_template: dict
    
    def __init__(self,path: os.PathLike = None):
        if path:
            if isinstance(path,str) and path.endswith('.json'):
                self.path = pathlib.Path(path)
            elif isinstance(path, pathlib.Path) and path.suffix == '.json':
                self.path = path
            else:
                raise TypeError(f"{self.__class__} path must be an os.PathLike ending in .json")
        else:
            raise ValueError(f"{self.__class__} requires a path to a json file")
        
    @property
    def contents(self) -> Dict:
        with self.path.open('r') as f:
            return json.load(f)
    
    @property
    def files(self) -> Dict[str, Dict[str,str]]:
        return self.contents['files']
    
    @property
    def exp_start(self) -> datetime.datetime:
        """Start time of experiment - not relevant for D2 files"""
        fields_to_try = ['ExperimentStartTime','ProbeInsertionStartTime','workflow_start_time']
        start_time = ''
        while fields_to_try:
            start_time = self.contents.get(fields_to_try.pop(0), '')
            if start_time != '':
                break
        else:
            # platform json file's creation time
            return datetime.datetime.fromtimestamp(self.path.stat().st_ctime)
        # workflow start time from platform json
        return datetime.datetime.strptime(start_time, WSE_DATETIME_FORMAT)
    
    @property
    def exp_end(self) -> datetime.datetime:
        """End time of experiment - not relevant for D2 files"""
        fields_to_try = ['ExperimentCompleteTime','workflow_complete_time','json_save_time']
        end_time = ''
        while fields_to_try and end_time == '':
            end_time = self.contents.get(fields_to_try.pop(0), '')
        # workflow end time from platform json
        return datetime.datetime.strptime(end_time, WSE_DATETIME_FORMAT)
    
    @property
    def rig(self):
        return self.contents.get('rig_id',None)
    
    @property
    def mon(self):
        return nptk.ConfigHTTP.hostname(f'{self.rig}-Mon')
    @property
    def sync(self):
        return nptk.ConfigHTTP.hostname(f'{self.rig}-Sync')
    @property
    def stim(self):
        return nptk.ConfigHTTP.hostname(f'{self.rig}-Stim')
    @property
    def acq(self):
        return nptk.ConfigHTTP.hostname(f'{self.rig}-Acq')
    
    @property
    def src_video(self) -> pathlib.Path:
        return pathlib.Path(f"//{self.mon}") / MVR_RELATIVE_PATH
    
    @property
    def src_image(self) -> pathlib.Path:
        if self.rig == 'NP.0':
            return pathlib.Path(f"//{self.mon}") / CAMVIEWER_RELATIVE_PATH
        return pathlib.Path(f"//{self.mon}") / MVR_RELATIVE_PATH
    
    @property
    def src_pkl(self) -> pathlib.Path:
        return pathlib.Path(f"//{self.stim}") / CAMSTIM_RELATIVE_PATH
    
    @property
    def src_sync(self) -> pathlib.Path:
        return pathlib.Path(f"//{self.sync}") / SYNC_RELATIVE_PATH
    
    
    # class FilesEntry:
        
        
def get_created_timestamp_from_file(file, date_format=WSE_DATETIME_FORMAT):

    t = os.path.getctime(str(file))
    # t = os.path.getmtime(file)
    t = time.localtime(t)
    t = time.strftime(date_format, t)

    return t

def get_files_created_between(dir, strsearch, start, end):
    """"Returns a generator of Path objects"""
    path_select_list = []
    paths_all = pathlib.Path(dir).glob(strsearch)
    for path in paths_all:
        time_created = int(get_created_timestamp_from_file(path))
        if time_created in range(start, end):
            path_select_list.append(path)

    return path_select_list

if __name__ == "__main__":
    j = PlatformJson(R"C:\ProgramData\AIBS_MPE\neuropixels_data\1204734093_601734_20220901\1204734093_601734_20220901_platformD1.json")
    from model import VariabilitySpontaneous
    experiment = VariabilitySpontaneous() 
    
    print(experiment.files(session_folder='1204734093_601734_20220901'))