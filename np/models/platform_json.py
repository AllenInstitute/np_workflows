import datetime
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import warnings
from typing import Dict, List, Tuple, Union

import requests

sys.path.append("c:/program files/aibs_mpe/workflow_launcher")

try:
    from np.services import config as nptk
except ImportError:
    pass
try:
    from np.services import config as nptk
except ImportError:
    pass

# -------------------------------------------------------------------------------------- #
SIMULATE = True 
# don't actually copy or modify anything - instead, create a 'virtual' session folder
# using symlinks to the actual data - ONLY APPLIES TO 'Files' CLASS
SYMLINK_REPO = pathlib.Path("//allen/programs/mindscope/workgroups/dynamicrouting/ben/staging")
warnings.warn(f"Simulation mode is ON. No files will be copied or modified.\nA virtual session folder will be created at {SYMLINK_REPO}") if SIMULATE else None
# -------------------------------------------------------------------------------------- #


TEMPLATES_ROOT = pathlib.Path("//allen/programs/mindscope/workgroups/dynamicrouting/ben/npexp_data_manifests")

WSE_DATETIME_FORMAT = '%Y%m%d%H%M%S' # should match the pattern used throughout the WSE

MVR_RELATIVE_PATH = pathlib.Path("c$/ProgramData/AIBS_MPE/mvr/data")
NEWSCALE_RELATIVE_PATH = pathlib.Path("c$/MPM_data")
CAMVIEWER_RELATIVE_PATH = pathlib.Path("c$/Users/svc_neuropix/cv3dImages") # NP.0 only
CAMSTIM_RELATIVE_PATH = pathlib.Path("c$/ProgramData/AIBS_MPE/camstim/data")
SYNC_RELATIVE_PATH = pathlib.Path("c$/ProgramData/AIBS_MPE/sync$/data")

NEUROPIXELS_DATA_RELATIVE_PATH = pathlib.Path("c$/ProgramData/AIBS_MPE/neuropixels_data")
NPEXP_PATH = pathlib.Path("//allen/programs/mindscope/workgroups/np-exp")

INCOMING_ROOT = pathlib.Path("//allen/programs/braintv/production/incoming/neuralcoding")

class Session:
    """Get session information from any string: filename, path, or foldername"""

    # use staticmethods with any path/string, without instantiating the class:
    #
    #  Session.mouse(
    #  "c:/1234566789_611166_20220708_surface-image1-left.png"
    #   )
    #  >>> "611166"
    #
    # or instantiate the class and reuse the same session:
    #   session = Session(
    #  "c:/1234566789_611166_20220708_surface-image1-left.png"
    #   )
    #   session.id
    #   >>> "1234566789"
    id = None
    mouse = None
    date = None
    
    NPEXP_ROOT = pathlib.Path(R"//allen/programs/mindscope/workgroups/np-exp")

    def __init__(self, path: str):
        if not isinstance(path, (str,pathlib.Path)):
            raise TypeError(f"{self.__class__.__name__} path must be a string or pathlib.Path object")

        self.folder = self.__class__.folder(path)
        # TODO maybe not do this - could be set to class without realizing - just assign for instances

        if self.folder:
            # extract the constituent parts of the session folder
            self.id = self.folder.split('_')[0]
            self.mouse = self.folder.split('_')[1]
            self.date = self.folder.split('_')[2]
        # elif 'production' and 'prod0' in str(path):
        #     self.id = re.search(R'(?<=_session_)\d+', str(path)).group(0)
        #     lims_dg = dg.lims_data_getter(self.id)
        #     self.mouse = lims_dg.data_dict['external_specimen_name']
        #     self.date = lims_dg.data_dict['datestring']
        #     self.folder = ('_').join([self.id, self.mouse, self.date])
        else:
            raise ValueError(f"{self.__class__.__name__} path must contain a valid session folder {path}")

    @classmethod
    def folder(cls, path: Union[str, pathlib.Path]) -> Union[str, None]:
        """Extract [10-digit session ID]_[6-digit mouse ID]_[6-digit date
        str] from a file or folder path"""

        # identify a session based on
        # [10-digit session ID]_[6-digit mouseID]_[6-digit date str]
        session_reg_exp = R"[0-9]{,}_[0-9]{6}_[0-9]{8}"

        session_folders = re.findall(session_reg_exp, str(path))
        if session_folders:
            if not all(s == session_folders[0] for s in session_folders):
                logging.debug(f"{cls.__class__.__name__} Mismatch between session folder strings - file may be in the wrong folder: {path}")
            return session_folders[0]
        else:
            return None
    
    @property
    def npexp_path(self) -> Union[pathlib.Path, None]:
        '''get session folder from path/str and combine with npexp root to get folder path on npexp'''        
        folder = self.folder
        if not folder:
            return None
        return self.NPEXP_ROOT / folder
    
    @property
    def lims(self) -> dict:
        """Content from lims on ecephys_session
        
        This property getter just prevents repeat calls to lims
        """
        if not hasattr(self, '_lims') or self._lims is None:
            self._lims = self.get_lims_content()
        return self._lims
    
    def get_lims_content(self) -> dict:
        response = requests.get(f"http://lims2/behavior_sessions/{self.id}.json?")
        if response.status_code == 404:
            response = requests.get(f"http://lims2/ecephys_sessions/{self.id}.json?")
        elif response.status_code != 200:
            raise requests.RequestException(f"Could not find content for session {self.id} in LIMS")
        
        return response.json()
        
    @property
    def project(self) -> str:
        return self.lims['project']['code']
        
    # @property
    # def type(self) -> str:
    # TODO if there's a way to get exp vs hab from lims, provide that info here
                

class SessionFile:
    """ Represents a single file belonging to a neuropixels ecephys session """

    session = None

    def __init__(self, path: Union[str, pathlib.Path]):
        """ from the complete file path we can extract some information upon
        initialization """

        if not isinstance(path, (str, pathlib.Path)):
            raise TypeError(f"{self.__class__.__name__}: path must be a str or pathlib.Path pointing to a file: {type(path)}")
        
        path = pathlib.Path(path)

        # ensure the path is a file, not directory
        # ideally we would check the path on disk with pathlib.Path.is_file(), but that only works if the file exists
        # we also can't assume that a file that exists one moment will still exist the next
        # (threaded operations, deleting files etc) - so no 'if exists, .is_file()?'
        # we'll try using the suffix/extension first, but be aware that sorted probe folders named 'Neuropix-PXI-100.1' 
        # will give a non-empty suffix here - probably safe to assume that a numeric suffix is never an actual file
        is_file = (path.suffix != '')
        is_file = False if path.suffix.isdecimal() else is_file
        try:
            is_file = True if path.is_file() else is_file
            # is_file() returns false if file doesn't exist so only change it if it exists
        except:
            pass
    
        if not is_file:
            raise ValueError(f"{self.__class__.__name__}: path must point to a file {path}")
        else:
            try:
                self.path = path # might be read-only, in the case of DVFiles
            except:
                pass
            
        self.name = self.path.name

        # get the name of the folder the file lives in (which may be the same as self.root_path below)
        self.parent = self.path.parent

        # extract the session ID from anywhere in the path
        self.session = Session(self.path)
        if not self.session:
            raise ValueError(f"{self.__class__.__name__}: path does not contain a session ID {self.path.as_posix}")
    
    @property
    def root_path(self) -> str:
        """root path of the file (may be the same as session_folder_path)"""
        # we expect the session_folder string to first appear in the path as
        # a child of some 'repository' of session folders (like npexp), 
        # - split the path at the first session_folder match and call that folder the root
        parts = pathlib.Path(self.path).parts
        while parts:
            if self.session.folder in parts[0]:
                break
            parts = parts[1:]
        else:
            raise ValueError(f"{self.__class__.__name__}: session_folder not found in path {self.path.as_posix()}")

        return pathlib.Path(str(self.path).split(str(parts[0]))[0])


    @property
    def session_folder_path(self) -> Union[str, None]:
        """path to the session folder, if it exists"""
        
        # if a repository (eg npexp) contains session folders, the following location should exist:
        session_folder_path = self.root_path / self.session.folder
        if os.path.exists(session_folder_path):
            return session_folder_path
        # but it might not exist: we could have a file sitting in a folder with a flat structure:
        # assorted files from multiple sessions in a single folder (e.g. LIMS incoming),
        # or a folder which has the session_folder pattern plus extra info
        # appended, eg. _probeABC
        # in that case return the root path
        return self.root_path
    
    
    @property
    def session_relative_path(self) -> pathlib.Path:
        '''filepath relative to a session folder's parent'''
        # wherever the file is, get its path relative to the parent of a
        # hypothetical session folder ie. session_id/.../filename.ext :
        session_relative_path = self.path.relative_to(self.root_path)
        if session_relative_path.parts[0] != self.session.folder:
            return pathlib.Path(self.session.folder, session_relative_path.as_posix())
        else:
            return session_relative_path
    
    @property
    def relative_path(self) -> pathlib.Path:
        '''filepath relative to a session folder'''
        return pathlib.Path(self.session_relative_path.relative_to(self.session.folder))
    
    @property
    def npexp_path(self) -> pathlib.Path:
        '''filepath on npexp (might not exist)'''
        if self.session:
            return self.session.NPEXP_ROOT / self.session_relative_path
        else:
            return None
 
    # TODO add lims_path property

    @property
    def z_drive_path(self) -> pathlib.Path:
        """Path to possible backup on 'z' drive (might not exist)
        
        This property getter just prevents repeat calls to find the path
        """
        if not hasattr(self, '_z_drive_path'):
            self._z_drive_path = self.get_z_drive_path()
        return self._z_drive_path
            
    def get_z_drive_path(self) -> pathlib.Path:
        """Path to possible backup on 'z' drive (might not exist)"""
        running_on_rig = nptk.COMP_ID if "NP." in nptk.COMP_ID else None
        local_path = str(self.path)[0] not in ["/", "\\"]
        rig_from_path = nptk.Rig.rig_from_path(self.path.as_posix()) 
        
        # get the sync computer's path 
        if (running_on_rig and local_path):
            sync_path = nptk.Rig.Sync.path
        elif rig_from_path:
            rig_idx = nptk.Rig.rig_str_to_int(rig_from_path)
            sync_path = R'\\' + nptk.ConfigHTTP.get_np_computers(rig_idx,'sync')
        else:
            sync_path = None
            
        if sync_path and sync_path not in self.path.as_posix():
            # add the z drive/neuropix data folder for this rig
            return pathlib.Path(
                    sync_path, 
                    "neuropixels_data", 
                    self.session.folder
                    ) / self.session_relative_path
    
    
    def __lt__(self, other):
        if self.session.id == other.session.id:
            return self.session_relative_path < other.session_relative_path
        return self.session.id < other.session.id


class PlatformJson(SessionFile):
    
    class IncompleteInfoFromPlatformJson(Exception):
        pass
    # files_template: dict
    
    def __init__(self,path: Union[str, pathlib.Path] = None):
        if path:
            if isinstance(path,str) and path.endswith('.json'):
                self.path = pathlib.Path(path)
            elif isinstance(path, pathlib.Path) and path.suffix == '.json':
                self.path = path
            else:
                raise TypeError(f"{self.__class__} path must be a path ending in .json")
        else:
            raise ValueError(f"{self.__class__} requires a path to a json file")
        super().__init__(self.path)

    @property
    def backup(self) -> pathlib.Path:
        # probably a good idea to create a backup before modifying anything
        # but don't overwrite an existing backup
        return self.path.with_suffix('.bak')
    
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
        # try to get workflow end time from platform json
        # fields in order of preference for estimating exp end time (for recovering
        # files created during exp based on timestamp)
        fields_to_try = ['ExperimentCompleteTime','workflow_complete_time','json_save_time','platform_json_save_time']
        end_time = ''
        while fields_to_try and end_time == '':
            end_time = self.contents.get(fields_to_try.pop(0), '')
        if end_time == '':
            raise self.__class__.IncompleteInfoFromPlatformJson(f"End time of experiment could not be determined from {self.path.as_uri()}")
        return datetime.datetime.strptime(end_time, WSE_DATETIME_FORMAT)
    
    @property
    def rig(self):
        return self.contents.get('rig_id',None)
    
    @property
    def experiment(self):
        return self.contents.get('experiment', self.session.project)
    
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
    # no src_acq because it depends on probe letter (A:/ B:/)
    
    @property
    def src_video(self) -> pathlib.Path:
        return pathlib.Path(fR"\\{self.mon}\{MVR_RELATIVE_PATH}")
    
    @property
    def src_motor_locs(self) -> pathlib.Path:
        return pathlib.Path(fR"\\{self.mon}\{NEWSCALE_RELATIVE_PATH}")
    
    @property
    def src_image(self) -> pathlib.Path:
        if self.rig == 'NP.0':
            return pathlib.Path(fR"\\{self.mon}\{CAMVIEWER_RELATIVE_PATH}")
        return pathlib.Path(fR"\\{self.mon}\{MVR_RELATIVE_PATH}")
    
    @property
    def src_pkl(self) -> pathlib.Path:
        return pathlib.Path(fR"\\{self.stim}\{CAMSTIM_RELATIVE_PATH}")
    
    @property
    def src_sync(self) -> pathlib.Path:
        return pathlib.Path(fR"\\{self.sync}\{SYNC_RELATIVE_PATH}") 
    
    def write_trigger(self):
        """Write a trigger file to lims incoming/trigger"""
        with open(INCOMING_ROOT / "trigger" / f"{self.session.id}.ecp", "w") as f:
            f.writelines(f"sessionid: {self.session.id}\n")
            f.writelines(f"location: '{INCOMING_ROOT.as_posix()}'")
    
# entries ------------------------------------------------------------------------------ #

# depending on the data type of each entry, the method to find the corresponding
# original data files will be quite different
# - from each entry in platform.json "files" field we create an Entry object of a
#   specific subtype, using the factory method below e.g. entry_from_factory(self, entry)

class Entry:
        
    def __init__(self, entry:Union[Dict,Tuple]=None, platform_json:PlatformJson=None):
        # entry in platform json 'files' has the format:
        #   'ephys_raw_data_probe_A': {
        #          'directory_name': '1208053773_623319_20...7_probeABC'}
        # we'll call the first key the 'descriptive_name'
        # second is 'dir_or_file_type' (values are 'directory_name', 'filename')
        # the value is the name of the directory or file: 'dir_or_file_name'
        
        self.descriptive_name = d = entry[0] if isinstance(entry, tuple) else list(entry.keys())[0]
        self.dir_or_file_type: str = list(entry[1].keys())[0] if isinstance(entry, tuple) else list(entry[d].keys())[0]
        self.dir_or_file_name: str = list(entry[1].values())[0] if isinstance(entry, tuple) else list(entry[d].values())[0]
        
        # we'll need some general info about the experiment:
        self.platform_json: PlatformJson = Files(platform_json.path) if not isinstance(platform_json, Files) else platform_json
        
        self.actual_data: pathlib.Path = self.platform_json.path.parent / self.dir_or_file_name
        # a presumed path to the data in the same folder as the platform json file
        self.expected_data: pathlib.Path = self.platform_json.path.parent / self.platform_json.expected[self.descriptive_name][self.dir_or_file_type]

    def __eq__(self, other):
        # when comparing entries we want to know whether they have the same
        # descriptive name key and the same file/folder name
        return self.descriptive_name == other.descriptive_name and self.dir_or_file_name == other.dir_or_file_name
            
    def __dict__(self):
        return {self.descriptive_name: {self.dir_or_file_type:self.dir_or_file_name}}
    
    @property
    def correct(self) -> bool:
        """Check entry dict matches template and specified file exists"""
        return self.correct_dict and self.correct_data
    
    @property
    def correct_dict(self) -> bool:
        return self.__dict__() == self.__dict__()
        # return self in [self.platform_json.entry_from_factory(entry) for entry in self.platform_json.expected.items()]
    
    @property
    def correct_data(self) -> bool:
        # exists mainly to be overloaded by ephys entry
        return self.expected_data.exists()
    
    @property
    def sources(self) -> List[pathlib.Path]:
        sources = []
        if self.origin:
            sources.append(self.origin)
        if "neuropixels_data" not in str(self.expected_data):
            sources.append(self.z)
        if NPEXP_PATH not in self.expected_data.parents:
            sources.append(self.npexp)
        return sources

    @property
    def origin(self) -> pathlib.Path:
        """Path to original file for this entry"""
        raise NotImplementedError # should be implemented by subclasses
    
    @property
    def npexp(self) -> pathlib.Path:
        """Path to possible copy on np-exp"""
        return NPEXP_PATH / self.platform_json.session.folder / self.dir_or_file_name 
    
    @property
    def z(self) -> pathlib.Path:
        """Path to possible copy on z-drive/neuropixels_data"""
        return pathlib.Path(f"//{self.platform_json.sync}/{NEUROPIXELS_DATA_RELATIVE_PATH}") / self.platform_json.session.folder / self.dir_or_file_name 
    
    
    def rename():
        """Rename the current data in the same folder as the platform json file"""
        pass
        # TODO 
    
    def return_single_hit(self, hits:List[pathlib.Path]) -> pathlib.Path:
        """Return a single hit if possible, or None if no hits.
        
        Processes the output from get_files[or dirs]_created_between() according to some
        common rule(s) 
        - (Current) take the largest filesize,
        - (add?) look for session folder string, 
        - (add?) exclude pretest/temp, 
        """
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        
        if len(hits) > 1 and all(h.is_file() for h in hits):
            sizes = [h.stat().st_size for h in hits if h]
            if all(s == sizes[0] for s in sizes):
                return hits[0] # all matches the same size
            else: 
                return hits[sizes.index(max(sizes))] # largest file size
        
        if len(hits) > 1 and all(h.is_dir() for h in hits):
            raise NotImplementedError 
            #TODO get logic from oe060 sorting
    
    def copy(self, dest: Union[str, pathlib.Path]=None):
        """Copy original file to a specified destination folder"""
        # TODO add checksum of file/dir to db
        if not self.sources:
            print("Copy aborted - no files found at origin or backup locations")
            return
        
        if dest is None:
            dest = self.expected_data

        dest = pathlib.Path(dest)
        
        for source in self.sources:
        
            if not source.exists():
                continue 
            
            if source.is_dir():
                pass
                # TODO add size comparison
                
            if source.is_file() and dest.is_file():
                
                if dest.stat().st_size == 0:
                    pass
                elif dest.stat().st_size < source.stat().st_size:
                    pass
                elif dest.stat().st_size == source.stat().st_size:
                    hashes = []
                    for idx, file in enumerate([dest, source]):
                        print(f"Generating checksum for {file} - may take a while.." )
                        with open(file,'rb') as f:
                            hashes.append(hashlib.md5(f.read()).hexdigest())
                     
                    if hashes[0] == hashes[1]:
                        print(f"Original data and copy in folder are identical")
                        return
                
                elif dest.stat().st_size > source.stat().st_size:
                    print(f"{source} is smaller than {dest} - copy manually if you really want to overwrite")
                    return
                        
            # do the actual copying
            if self.dir_or_file_type == 'directory_name':
                print(f"Copying {source} to {dest}")
                if not SIMULATE:
                    shutil.copytree(source,dest, dirs_exist_ok=True)
                else:
                    for path in source.rglob('*'):
                        if path.is_dir():
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.symlink_to(source)
                print('Copying complete')
            
            if self.dir_or_file_type == 'filename':
                print(f"Copying {source} to {dest}")
                shutil.copy2(source,dest) if not SIMULATE else dest.symlink_to(source)
                print('Copying complete')
        
            if self.correct_data:
                break
    
# -------------------------------------------------------------------------------------- #
class EphysRaw(Entry):
    
    probe_drive_map = {
        'A':'A',
        'B':'A',
        'C':'A',
        'D':'B',
        'E':'B',
        'F':'B'
    }
    probe_group_map = {
        'A':'_probeABC',
        'B':'_probeABC',
        'C':'_probeABC',
        'D':'_probeDEF',
        'E':'_probeDEF',
        'F':'_probeDEF'
    }
    
    descriptors = [f'ephys_raw_data_probe_{c}' for c in 'ABCDEF']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_letter = self.descriptive_name[-1].upper() # A-F           
        self.source = pathlib.Path(f"//{self.platform_json.acq}/{self.probe_drive_map[self.probe_letter]}")
    
    @property
    def origin(self) -> pathlib.Path:
        
        def filter_hits(hits:List[pathlib.Path]) -> List[pathlib.Path]:
            return [h for h in hits if not any(f in h.as_posix() for f in ['_temp', '_pretest'])]
        
        glob = f"*{self.platform_json.session.folder}*"
        hits = get_dirs_created_between(self.source,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        
        single_hit = self.return_single_hit(filter_hits(hits))
        if single_hit:
            return single_hit
            
        hits = get_dirs_created_between(self.source,'*',self.platform_json.exp_start,self.platform_json.exp_end)
        single_hit = self.return_single_hit(filter_hits(hits))      
        if single_hit:
            return single_hit   
                   
        # TODO if multiple folders found, find the largest
        # TODO locate even if no folders with matching session folder or creation time
        print(f"No matches for {self.platform_json.session.folder} in {self.source} or no folders created during the experiment")
    
    @property 
    def platform_json_on_z_drive(self) -> bool:
        # raw probe data isn't stored on the z drive like other data, so this flag will
        # be queried by other functions
        if any(s in str(self.platform_json.path.parent).lower() for s in ['neuropixels_data', 'z:/', 'z:\\']):
            return True
        return False
    
    def copy(self, *args, **kwargs):
        if self.platform_json_on_z_drive:
            print(f"Copying not implemented for {self.__class__.__name__} to {self.expected_data}: these data don't live on Z: drive")
        else:
            super().copy(*args, **kwargs)
            
    @property
    def correct_data(self) -> bool:
        # overloaded to return True if the platform json being examined is on the
        # z-drive and data exists at origin
        if self.platform_json_on_z_drive and self.origin:
            return True
        return super().correct_data
    
# -------------------------------------------------------------------------------------- #
class Sync(Entry):
    
    descriptors = ['synchronization_data']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = self.platform_json.src_sync
        
    @property
    def origin(self) -> pathlib.Path:
        glob = f"*{self.expected_data.suffix}"
        hits = get_files_created_between(self.source,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        if hits:
            return self.return_single_hit(hits)
       
        # try again with differnt search
        glob = f"*{self.platform_json.session.folder}*.sync"
        start = self.platform_json.exp_start
        end = self.platform_json.exp_end + datetime.timedelta(minutes=30)
        hits = get_files_created_between(self.source,glob,start,end)
        if hits:
            return self.return_single_hit(hits)
        # try again with differnt search
        glob = f"*{self.platform_json.session.folder}*.h5"
        start = self.platform_json.exp_start
        end = self.platform_json.exp_end + datetime.timedelta(minutes=30)
        hits = get_files_created_between(self.source,glob,start,end)
        if not hits:
            print(f"No matching sync file found at origin {self.source}")
        return self.return_single_hit(hits)
        
# -------------------------------------------------------------------------------------- #
class Camstim(Entry):
    pkls = ['behavior','optogenetic','visual','replay']
    descriptors = [f"{pkl}_stimulus" for pkl in pkls]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = self.platform_json.src_pkl
        self.pkl = self.dir_or_file_name.split('.')[-2] 
        
    @property
    def origin(self) -> pathlib.Path:
        hits = []
        glob = f"*{self.pkl}*.pkl"
        hits += get_files_created_between(self.source,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        
        glob = f"*{self.platform_json.contents['stimulus_name']}*.pkl"
        hits += get_files_created_between(self.source,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        
        if len(hits) == 0:
            print(f"No matching {self.pkl}.pkl found")
        
        return self.return_single_hit(hits)

# -------------------------------------------------------------------------------------- #
class VideoTracking(Entry):
    cams = ['behavior','eye', 'face']
    descriptors =[f"{cam}_tracking" for cam in cams]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = self.platform_json.src_video
        self.cam = self.dir_or_file_name.split('.')[-2] 
        
    @property
    def origin(self) -> pathlib.Path:
        glob = f"*{self.cam}*{self.expected_data.suffix}"
        start = self.platform_json.exp_start
        end = self.platform_json.exp_end + datetime.timedelta(seconds=10)
        hits = get_files_created_between(self.source,glob,start,end)
        if not hits:
            print(f"No matching video info json at origin {self.source}")
        return self.return_single_hit(hits)
    
class VideoInfo(Entry):
    # preference would be to inherit from VideoTracking
    # but then this class wouldn't be a direct subclass of Entry
    # and Entry.__subclasses__() no longer returns this class
    
    cams = ['beh','eye', 'face']
    descriptors =[f"{cam}_cam_json" for cam in cams]
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = self.platform_json.src_video
        self.cam = self.dir_or_file_name.split('.')[-2] 
    
    @property
    def origin(self) -> pathlib.Path:
        hits = []
        glob = f"*{self.cam}*{self.expected_data.suffix}"
        start = self.platform_json.exp_start
        end = self.platform_json.exp_end + datetime.timedelta(seconds=10)
        hits = get_files_created_between(self.source,glob,start,end)
        if not hits:
            print(f"No matching video info json at origin {self.source}")
        return self.return_single_hit(hits)
    
# -------------------------------------------------------------------------------------- #
class SurfaceImage(Entry):

    imgs = ['pre_experiment','brain','pre_insertion','post_insertion','post_stimulus','post_experiment']
    descriptors =[f"{img}_surface_image_{side}" for img in imgs for side in ['left','right'] ] # dorder of left/right is important for self.original
    
    #TODO assign total surface images to each instance
    total_imgs_per_exp:int = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert hasattr(self,'descriptive_name')
        self.source = self.platform_json.src_image
        self.side = self.descriptive_name.split('_')[-1]
    
    @property
    def total_imgs_per_exp(self):
        return sum('_surface_image_' in descriptive_name for descriptive_name in self.platform_json.template.keys())
    
    @property
    def origin(self) -> pathlib.Path:
        if not self.total_imgs_per_exp:
            print(f"Num. total images needs to be assigned")
            return None
        glob = f"*{self.expected_data.suffix}"
        hits = get_files_created_between(self.source,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        
        if len(hits) == 0:
            print(f"No matching surface image found at origin {self.source}")
            return None
        
        right_labels_only = True if all('right' in hit.name for hit in hits) else False
        lefts_labels_only = True if all('left' in hit.name for hit in hits) else False
        equal_right_left_labels = True if sum('left' in hit.name for hit in hits) == sum('right' in hit.name for hit in hits) else False

        # need to know how many surface images there should be in total for this experiment
        if len(hits) == self.total_imgs_per_exp and equal_right_left_labels:
            # we have all expected left/right pairs of images
            # hits is sorted by creation time, so we just have to work out which pair
            # matches this entry (self), then grab the left or right image from the pair
            img_idx0 = self.descriptors.index(self.descriptive_name)
            #decsriptors are in order left, then right - return right or left of a pair
            img_idx1 = img_idx0 - 1 if img_idx0%2 else img_idx0 + 1
            return hits[img_idx0] if self.side in hits[img_idx0].name else hits[img_idx1]
        
        if len(hits) == 0.5*self.total_imgs_per_exp and right_labels_only or lefts_labels_only:
            # we have only the left or the right image for each pair
            img_idx = self.descriptors.index(self.descriptive_name)//2
            # regardless of which self.side this entry is, we have no choice but to
            # return the image that we have (relabeled inaccurately as the other side in half the cases)
            return hits[img_idx]
        print(f"{self.total_imgs_per_exp} images found - can't determine which is {self.dir_or_file_name}")
        
# --------------------------------------------------------------------------------------
class NewscaleLog(Entry):
    descriptors = ['newstep_csv']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)   
        self.source = self.platform_json.src_motor_locs
        
    @property
    def origin(self) -> pathlib.Path:
        log = self.source / 'log.csv'
        if log.exists():
            return log
        else:
            print(f"No matching newscale log found at origin {self.source}")
    
    
    # TODO trim the MPM logs or copy only what's needed
    ##* the lines below will extract relevant motor locs without using pandas,
    ##* but it's unreasonably slow with 500k lines in the csv file
    # with log.open('r') as o:
    #     locs = csv.reader(o)
    #     with file.open('w') as n:
    #         locs_from_exp_date = csv.writer(n)
            
    #     for row in locs:
    #         sys.stdout.write(f"{locs.line_num}\r")
    #         sys.stdout.flush()
            
    #         if self.exp_start.strftime(R"%Y/%m/%d") in row[0]:
    #             # find csv entries recorded on the same day as the
    #             # experiment
    #             locs_from_exp_date.writerow(row)
    
# --------------------------------------------------------------------------------------
class Notebook(Entry):
    descriptors = [
                'area_classifications',
                'fiducial_image',
                'overlay_image',
                'insertion_location_image',
                'isi_registration_coordinates',
                'isi _registration_coordinates'
                ]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)   
    
    @property
    def origin(self) -> pathlib.Path:    
        pass
        #TODO notebook entries
        
# --------------------------------------------------------------------------------------
class Surgery(Entry):
    descriptors = ['surgery_notes','post_removal_surgery_image','final_surgery_image']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)   
    
    @property
    def origin(self) -> pathlib.Path:    
        pass
        #TODO surgery notes 
    
    def copy(self):
        super().copy()
         # create an empty txt file for surgery notes if it doesn't exist
        if self.descriptive_name == 'surgery_notes':
            self.expected_data.touch()
# -------------------------------------------------------------------------------------- #
    
 
class Files(PlatformJson):
    """
        A subclass with more-specific methods for fixing the files manifest part of the
        platform.json, that run on initialization.
        
        Correcting the platform json and its corresponding session folder of data is a
        multi-step process:
        
            1. Deal with the data:
                for each entry in a reference/template platform json, if the expected
                file/dir doesn't exist in the session folder, copy it from the original
                source
                - seek user input to find correct file and copy it with correct name
                
            * all template entries should now have correct corresponding files 
                all(entry.correct_data for entry in Files(*.json).new_entries)
                
            
            2. Deal with the platform json 'files' dict:
                we could replace the 'files' dict with the template dict, but there may
                be entries in the files dict that we don't want to lose
                    - find entries not in template 
                    - decide what to do with their data
                    - decide whether or not to delete from the files dict
                    
                there may also be incorrect entries in the files dict that 
                correspond to incorrect data
                    - find entries that don't match template 
                    - decide whether to delete their data
                                 
            3. Update files dict with template, replacing incorrect existing entries with correct
               versions and leaving additional entries intact
            * all template entries should now be in the files dict
                Files(*.json).missing == {}
    """

    
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if SIMULATE:
            # in simulation mode, we do all file operations in a 'virtual' session
            # folder: we'll try to create a complete session folder of correctly named
            # experiment files, but instead of modifying the session folder the platform
            # json lives in, we'll copy it to a new folder, and instead of copying data
            # to the folder (which can take time for ephys, videos..), we'll just create
            # symlinks to what we identify are the candidate correct files. from there,
            # further validation can run on the symlinks to check their contents
            
            # - create a blank slate to work from:
            self.simulated_folder.mkdir(parents=True,exist_ok=True)
            [p.unlink() for p in self.simulated_folder.rglob('*')]
            
            # - copy the original platform json to the simulated folder 
            if self.backup.exists():
                shutil.copy(self.backup,self.simulated_folder)
            else:
                shutil.copy2(self.path, self.simulated_folder)
                
            # - now replace the linked file with the new one so that all calls to the
            #   platform json's path/parent folder will resolve to the new virtual one.
            #   this will save us from making a lot of 'if SIMULATED:' checks
            self.path = self.simulated_folder / self.path.name
            
        # this will become the list of entries in the updated 'files' dict
        # (list is populated by the functions that follow)
        self.new_entries:List[Entry] = [] 
        
        # 1. Deal with the missing data/files
        self.fix_data()
        # 2. Deal with the platform json 'files' dict
        self.fix_dict()
        if self.correct_data and self.correct_dict:
            #3. Update the contents of the files dict in the platform json
            self.write()
            # (this also appends the project codename ie. OpenScopeIllusion)
        
        # optional steps (not triggered automatically):
        # - checksum data
        # - copy data to lims incoming
    
    @property
    def simulated_folder(self) -> pathlib.Path:
        """Where symlinks to data are created, instead of modifying original data.
        Created anew each time we run in Simulation mode."""
        return SYMLINK_REPO / self.session.folder
        
    @property
    def template(self) -> dict: 
        if (
            any(h in self.contents.get('stimulus_name','') for h in ['hab','habituation'])
        or any(h in self.contents.get('workflow','') for h in ['hab','habituation'])
        ):
            session_type = 'habituation'
        elif 'D1' in self.path.stem:
            session_type = 'D1'
        elif 'D2' in self.path.stem:
            session_type = 'D2'
        template_path = TEMPLATES_ROOT / session_type / f"{self.experiment}.json"
        with template_path.open('r') as f:
            return json.load(f)['files']
        
    @property
    def expected(self) -> dict:
        # convert template dict to str
        # replace % with session string
        # switch ' and " so we can convert str back to dict with json.loads()
        return json.loads(str(self.template).replace('%',str(self.session.folder)).replace("'",'"'))
        
    @property
    def current(self) -> dict:
        return self.contents['files']
    
    @property
    def missing(self) -> dict:
        return {k:v for k,v in self.expected.items() if k not in self.current}
    
    @property
    def extra(self) -> dict:
        return {k:v for k,v in self.current.items() if k not in self.expected}
    
    @property
    def incorrect(self) -> dict:
        return {k:v for k,v in self.current.items() if k in self.expected.keys() and v != self.expected[k]}
    
    def entry_from_factory(self, entry:Dict) -> Entry:
        descriptive_name = Entry(entry,self).descriptive_name
        for entry_class in Entry.__subclasses__():
            if descriptive_name in entry_class.descriptors:
                return entry_class(entry,self)
        raise ValueError(f"{descriptive_name} is not a recognized platform.json[files] entry-type")
    
    @property
    def new_dict(self) -> dict:
        return {k:v for e in self.new_entries for k,v in e.__dict__().items()} if self.new_entries else {}
    
    @property
    def correct_data(self) -> bool:
        return all([e.correct_data for e in self.new_entries]) if self.new_entries else False

    @property
    def correct_dict(self) -> bool:
        return all(e in self.new_dict.keys() for e in self.expected.keys())
    
    
    def fix_data(self):
        """
            1. Deal with the data:
                for each entry in a reference/template platform json, if the expected
                file/dir doesn't exist in the session folder, copy it from the original
                source
                - seek user input to find correct file and copy it with correct name
                
            * all template entries should now have correct corresponding files 
                Files.correct_data = all(entry.correct_data for entry in Files(*.json).new_entries)
        """
        expected = [self.entry_from_factory({k:v}) for k,v in self.expected.items()]
        for entry in expected:
            if entry.correct_data:
                self.new_entries.append(entry)
                continue

            entry.copy()
            
            if entry.correct_data:
                print(f"fixed {entry.dir_or_file_name}")
                self.new_entries.append(entry)
                continue
            print(f"need help finding {entry.dir_or_file_name}")
        
    def fix_dict(self):
        """            
        2. Deal with the platform json 'files' dict:
            we could replace the 'files' dict with the template dict, but there may
            be entries in the files dict that we don't want to lose
                - find entries not in template 
                - decide what to do with their data
                - decide whether or not to delete from the files dict
                
            there may also be incorrect entries in the files dict that 
            correspond to incorrect data
                - find entries that don't match template 
                - decide whether to delete their data"""
                
        extra = [self.entry_from_factory({k:v}) for k,v in self.extra.items()]
        for entry in extra:
            if entry.actual_data.exists():
                self.new_entries.append(entry)
                continue
            print(f"{entry.descriptive_name} removed from platform.json: specified data does not exist {entry.dir_or_file_name} ")

    def write(self):
        """Overwrite existing platform json, with a backup of the original preserved"""
        # ensure a backup of the original first
        shutil.copy2(self.path, self.backup) if not self.backup.exists() else None
        
        print(f"updating {self.path} with {len(self.missing)} new entries and {len(self.incorrect)} corrected entries")
        contents = self.contents # must copy contents to avoid breaking class property (Which pulls from .json)
        contents['files'] = {**self.new_dict}
        contents['project'] = self.session.project
            
        with self.path.open('w') as f:
            json.dump(dict(contents), f, indent=4)
        print(f"updated {self.path.name}")
                    
def get_created_timestamp_from_file(file:Union[str, pathlib.Path]):
    timestamp = pathlib.Path(file).stat().st_ctime
    return datetime.datetime.fromtimestamp(timestamp)

def get_dirs_created_between(dir: Union[str, pathlib.Path], strsearch, start:datetime.datetime, end:datetime.datetime) -> List[pathlib.Path]:
    """"Returns a list of Path objects, sorted by creation time"""
    hits = []
    glob_matches = pathlib.Path(dir).glob(strsearch)
    for match in glob_matches:
        if match.is_dir():
            t = get_created_timestamp_from_file(match)
            if start <= t <= end:
                hits.append(match)
    return sorted(hits, key=get_created_timestamp_from_file)
    
def get_files_created_between(dir: Union[str, pathlib.Path], strsearch, start:datetime.datetime, end:datetime.datetime) -> List[pathlib.Path]:
    """"Returns a list of Path objects, sorted by creation time"""
    hits = []
    glob_matches = pathlib.Path(dir).rglob(strsearch)
    for match in glob_matches:
        t = get_created_timestamp_from_file(match)
        if start <= t <= end:
            hits.append(match)
    return sorted(hits, key=get_created_timestamp_from_file)

if __name__ == "__main__":
    # j = Files(R"\\w10dtsm18307\c$\ProgramData\AIBS_MPE\neuropixels_data\1204734093_601734_20220901\1204734093_601734_20220901_platformD1.json")
    # j = Files(R"\\w10DTSM112719\C\ProgramData\AIBS_MPE\neuropixels_data\1204677304_632487_20220901\1204677304_632487_20220901_platformD1.json")
    # j = Files(R"\\w10dtsm18306\neuropixels_data\1208053773_623319_20220907\1208053773_623319_20220907_platformD1.json")
    # j = Files(R"\\allen\programs\mindscope\workgroups\np-exp\1208664393_623319_20220908\1208664393_623319_20220908_platformD1.json")
    # j = Files(R"\\allen\programs\mindscope\workgroups\np-exp\1208035625_636890_20220907\1208035625_636890_20220907_platformD1.json")
    # j = Files(R"\\allen\programs\mindscope\workgroups\np-exp\1210343162_623786_20220912\1210343162_623786_20220912_platformD1.json")
    
    # j = PlatformJson(R"\\allen\programs\mindscope\workgroups\np-exp\1210343162_623786_20220912\1210343162_623786_20220912_platformD1.json")
    j = PlatformJson(R"C:\Users\ben.hardcastle\Desktop\1194643724_615563_20220727\1194643724_615563_20220727_platformD1.json")
    j = Files(R"C:\Users\ben.hardcastle\Desktop\1194643724_615563_20220727\1194643724_615563_20220727_platformD1.json")
    # time.sleep(3600*6)
    # j.write_trigger()
    # j.fix_data()
    
    for disk in re.findall("([A-Z](?=:):)",str(os.popen("fsutil fsinfo drives").readlines())):
        try:
            print(shutil.disk_usage(disk))
        except PermissionError:
            pass
    
    # j.fetch_data_missing_from_folder()
    # j.fix_current_entries()
    # j.add_missing_entries()
    # j.update() # create valid files dict and write to json
    # # TODO update entries in platform json to reflect new additions to the folder
    # # TODO fix entries in platform json that don't match the template (eg not fields
    # # aren't missing from 'files', but they have the wrong filename etc)
    
    # from model import VariabilitySpontaneous
    # experiment = VariabilitySpontaneous() 
    # x = requests.get(f"http://lims2/ecephys_sessions/{j.session.id}.json?").json()

    # print(experiment.files(session_folder='1204734093_601734_20220901'))
