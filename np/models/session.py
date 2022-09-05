
import logging
import os
import pathlib
import re
from typing import Union

from np.services import config as nptk

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
    def lims_path(self) -> Union[pathlib.Path, None]:
        '''get lims id from path/str and lookup the corresponding directory in lims'''
        if not (self.folder or self.id):
            return None
        
        try:
            lims_dg = dg.lims_data_getter(self.id)
            WKF_QRY =   '''
                        SELECT es.storage_directory
                        FROM ecephys_sessions es
                        WHERE es.id = {}
                        '''
            lims_dg.cursor.execute(WKF_QRY.format(lims_dg.lims_id))
            exp_data = lims_dg.cursor.fetchall()
            if exp_data and exp_data[0]['storage_directory']:
                return pathlib.Path('/'+exp_data[0]['storage_directory'])
            else:
                return None
            
        except:
            return None


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
        if hasattr(self, '_z_drive_path'):
            return self._z_drive_path
        else:
            self._z_drive_path = self.get_z_drive_path()
            return self.z_drive_path
            
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

