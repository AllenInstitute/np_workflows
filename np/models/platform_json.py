import datetime
import enum
import json
import logging
import os
import pathlib
import re
import shutil
import sys
from typing import Dict, List, Union

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

WSE_DATETIME_FORMAT = '%Y%m%d%H%M%S' # should match the pattern used throughout the WSE

MVR_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/mvr/data")
NEWSCALE_RELATIVE_PATH = pathlib.Path("C/MPM_data")
CAMVIEWER_RELATIVE_PATH = pathlib.Path("C/Users/svc_neuropix/cv3dImages") # NP.0 only
CAMSTIM_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/camstim/data")
SYNC_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/sync/data")

NEUROPIXELS_DATA_RELATIVE_PATH = pathlib.Path("C/ProgramData/AIBS_MPE/neuropixels_data")
NPEXP_PATH = pathlib.Path("//allen/programs/mindscope/workgroups/np-exp")


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
        
    @property
    def type(self) -> str:
        result = re.findall(fR"(behavior|ecephys)(?=_session_{self.id})",self.lims['storage_directory'])
        return result[0] if result else None
    
    # @property
    # def lims_path(self) -> Union[pathlib.Path, None]:
    #     '''get lims id from path/str and lookup the corresponding directory in lims'''
    #     if not (self.folder or self.id):
    #         return None
        
    #     try:
    #         lims_dg = dg.lims_data_getter(self.id)
    #         WKF_QRY =   '''
    #                     SELECT es.storage_directory
    #                     FROM ecephys_sessions es
    #                     WHERE es.id = {}
    #                     '''
    #         lims_dg.cursor.execute(WKF_QRY.format(lims_dg.lims_id))
    #         exp_data = lims_dg.cursor.fetchall()
    #         if exp_data and exp_data[0]['storage_directory']:
    #             return pathlib.Path('/'+exp_data[0]['storage_directory'])
    #         else:
    #             return None
            
    #     except:
    #         return None


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
    
    # files_template: dict
    
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
        super().__init__(self.path)
        
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
     
# entries ------------------------------------------------------------------------------ #

# depending on the data type of each entry, the method to find the corresponding
# original data files will be quite different
# - from each entry in platform.json "files" field we create an Entry object of a
#   specific subtype, using the factory method below e.g. entry_from_factory(self, entry)

class Entry:
    def __init__(self, entry:Dict=None, platform_json:PlatformJson=None):
        self.descriptive_name = entry[0]
        self.dir_or_file_type = list(entry[1].keys())[0]
        self.dir_or_file_name = list(entry[1].values())[0]
        self.platform_json = platform_json
        self.path = self.platform_json.path / self.dir_or_file_name
        
    @property
    def original(self) -> os.PathLike:
        """Path to original file for this entry"""
        raise NotImplementedError
    
    def copy(self,dest: os.PathLike=None):
        """Copy original file to a specified destination folder"""
        if dest is None:
            dest = self.path
        # TODO add checksum of file/dir to db
        if self.dir_or_file_type == 'directory_name':
            return shutil.copytree(self.original,dest)
        if self.dir_or_file_type == 'filename':
            return shutil.copy2(self.original,dest)
            
    def __dict__(self):
        return {self.descriptive_name: {self.dir_or_file_type:self.dir_or_file_name}}
    
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
    
    @property
    def original(self) -> os.PathLike:
        
        drive = pathlib.Path(f"//{self.platform_json.acq}/{self.probe_drive_map[self.probe_letter]}")
        
        hits = get_dirs_created_between(drive,self.platform_json.session.folder,self.platform_json.exp_start,self.platform_json.exp_end)
        # TODO if multiple folders found, find the largest
        # TODO locate even if no folders with matching session folder or creation time
        if len(hits) == 1:
            return hits[0]
        print(f"No folders matching {self.platform_json.session.folder} not found in {drive}")
        
# -------------------------------------------------------------------------------------- #
class Sync(Entry):
    
    descriptors = ['synchronization_data']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    @property
    def original(self) -> os.PathLike:
        src = self.platform_json.src_sync
        hits = get_files_created_between(src,self.path.suffix,self.platform_json.exp_start,self.platform_json.exp_end)
        return hits[0] if len(hits) == 1 else None
        print(f"No matching sync file found")
        
# -------------------------------------------------------------------------------------- #
class Camstim(Entry):
    pkls = ['behavior','optogenetic','visual','replay']
    descriptors = [f"{pkl}_stimulus" for pkl in pkls]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pkl = self.dir_or_file_name.split('.')[-2] 
        
    @property
    def original(self) -> os.PathLike:
        src = self.platform_json.src_pkl
        hits = []
        glob = f"*{self.pkl}*.pkl"
        hits.append(get_files_created_between(src,glob,self.platform_json.exp_start,self.platform_json.exp_end))
        
        glob = f"*{self.platform_json.contents['stimulus_name']}*.pkl"
        hits.append(get_files_created_between(src,glob,self.platform_json.exp_start,self.platform_json.exp_end))
        
        if len(hits) == 0:
            print(f"No matching {self.pkl}.pkl found")
            return None
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return hits[max([h.stat().st_size for h in hits if h])]
        
# -------------------------------------------------------------------------------------- #
class VideoTracking(Entry):
    cams = ['behavior','eye', 'face']
    descriptors =[f"{cam}_tracking" for cam in cams]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cam = self.dir_or_file_name.split('.')[-2] 
        
    @property
    def original(self) -> os.PathLike:
        hits = []
        src = self.platform_json.src_video
        glob = f"*{self.cam}*{self.path.suffix}"
        hits.append(get_files_created_between(src,glob,self.platform_json.exp_start,self.platform_json.exp_end))

class VideoInfo(Entry):
    cams = ['beh','eye', 'face']
    descriptors =[f"{cam}_cam_json" for cam in cams]
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    # inherits behavior from VideoTracking while being subclass of Entry (for ease of
    # luookingp Entry's subclasses )
    @property
    def original(self) -> os.PathLike:
        return VideoTracking(self).original
    
# -------------------------------------------------------------------------------------- #
class SurfaceImage(Entry):
    imgs = ['pre_experiment','brain','pre_insertion','post_insertion','post_stimulus','post_experiment']
    descriptors =[f"{img}_surface_image_{side}" for img in imgs for side in ['left','right'] ]
    
    #TODO assign total surface images to each instance
    total_imgs_per_exp:int = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.side = self.descriptive_name.split('_')[-1]
        
    @property
    def original(self) -> os.PathLike:
        if self.total_imgs_per_exp is None:
            print(f"No total images needs to be assigned")
            return None
        src = self.platform_json.src_image
        glob = f"*{self.path.suffix}"
        hits = get_files_created_between(src,glob,self.platform_json.exp_start,self.platform_json.exp_end)
        if len(hits) == 0:
            print(f"No matching surface image found")
            return None
        # need to know how many surface images there should be in total for this experiment
        if len(hits) == self.total_imgs_per_exp:
            img_idx0 = self.descriptors.index(self.descriptive_name)
            #decsriptors are in order left, then right - return right or left of a pair
            img_idx1 = img_idx0 - 1 if img_idx0%2 else img_idx0 + 1
            return hits[img_idx0] if self.side in hits[img_idx0].name else hits[img_idx1]
# --------------------------------------------------------------------------------------
class NewscaleLog(Entry):
    descriptors = ['newstep_csv']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)   
    
    @property
    def original(self) -> os.PathLike:
        src = self.platform_json.src_motor_locs
        return src / 'log.csv'
    
    
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
    def original(self) -> os.PathLike:    
        pass
        #TODO notebook entries
        
# --------------------------------------------------------------------------------------
class Surgery(Entry):
    descriptors = ['surgery_notes','post_removal_surgery_image','final_surgery_image']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)   
    
    @property
    def original(self) -> os.PathLike:    
        pass
        #TODO surgery notes 
    
# -------------------------------------------------------------------------------------- #
    
 
class Files(PlatformJson):
    
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        
    @property
    def template(self) -> dict:
        template_root = pathlib.Path(R"\\allen\programs\mindscope\workgroups\dynamicrouting\ben\npexp_data_manifests")
        if self.session.type == 'behavior':
            session_type = 'habituation'
        elif 'D1' in self.path.stem:
            session_type = 'D1'
        elif 'D2' in self.path.stem:
            session_type = 'D2'
        template_path = template_root / session_type / f"{self.session.project}.json"
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
    
    def entry_from_factory(self, entry:Dict) -> Entry:
        descriptive_name = Entry(entry,self).descriptive_name
        for entry_class in Entry.__subclasses__():
            if descriptive_name in entry_class.descriptors:
                return entry_class(entry,self)
        raise ValueError(f"{descriptive_name} is not a recognized platform.json[files] entry-type")
        
    @property
    def entries(self) -> List[Entry]:
        return [self.entry_from_factory(entry) for entry in self.expected.items()]
    
    @property
    def paths(self) -> List[Entry]:
        return [self.path.parent / pathlib.Path(v.dir_or_file_name) for v in self.entries]
    
    @property
    def exist(self) -> List[Entry]:
        return [f.exists() for f in self.paths]
    
    def fetch_missing(self):
        for entry in self.entries:
            
            print(f"missing: {entry.dir_or_file_name}\nfound:{entry.original}")
          
def get_created_timestamp_from_file(file:os.PathLike):
    timestamp = pathlib.Path(file).stat().st_ctime
    return datetime.datetime.fromtimestamp(timestamp)

def get_dirs_created_between(dir: os.PathLike, strsearch, start:datetime.datetime, end:datetime.datetime) -> List[pathlib.Path]:
    """"Returns a list of Path objects"""
    hits = []
    glob_matches = pathlib.Path(dir).glob(strsearch)
    for match in glob_matches:
        if match.is_dir():
            t = get_created_timestamp_from_file(match)
            if start <= t <= end:
                hits.append(match)
    return hits
    
def get_files_created_between(dir: os.PathLike, strsearch, start:datetime.datetime, end:datetime.datetime) -> List[pathlib.Path]:
    """"Returns a list of Path objects"""
    hits = []
    glob_matches = pathlib.Path(dir).rglob(strsearch)
    for match in glob_matches:
        t = get_created_timestamp_from_file(match)
        if start <= t <= end:
            hits.append(match)

    return hits


if __name__ == "__main__":
    j = Files(R"\\w10DTSM112719\C\ProgramData\AIBS_MPE\neuropixels_data\1204677304_632487_20220901\1204677304_632487_20220901_platformD1.json")
    j = Files(R"\\w10dtsm18307\c$\ProgramData\AIBS_MPE\neuropixels_data\1204734093_601734_20220901\1204734093_601734_20220901_platformD1.json")
    j.entries
    j.fetch_missing()
    j.session.type
    from model import VariabilitySpontaneous
    experiment = VariabilitySpontaneous() 
    x = requests.get(f"http://lims2/ecephys_sessions/{j.session.id}.json?").json()

    print(experiment.files(session_folder='1204734093_601734_20220901'))
