import abc
import json
import time
from pathlib import Path


class MPEConfig:
    """Container for default parameters for mpeconfig/zookeeper.
    
    Fetching via mpeconfig package uses this function:
    
        def source_configuration(
            project_name: str,
            hosts: str = "aibspi.corp.alleninstitute.org:2181",
            use_local_config: bool = False,
            send_start_log: bool = True,
            fetch_logging_config: bool = True,
            fetch_project_config: bool = True,
            version: str = None,
            rig_id: str = None,
            comp_id: str = None,
            serialization: str = "yaml"
        ): -> dict
    
    """
    lims_project_name:str = None # should be the class name, but just in case
    version:str = 'unknown'

class Model(abc.ABC):
    mpe_config = MPEConfig()
    local_config = None
    
class Behavior(Model):
    pass

class Passive(Model):
    mpe_config = MPEConfig()
    mpe_config.lims_project_name = 'neuropixels_passive_experiment_workflow'
    mpe_config.version = '1.4.0+g6c8db37.b73352'

class OpenScopeIllusion(Passive):
    lims_project_name = "OpenScopeIllusion"
    
class OpenScopeGlobalLocalOddball(Passive):
    lims_project_name = "OpenScopeGlobalLocalOddball"

class VariabilitySpontaneous(Passive):
    lims_project_name = "VariabilitySpontaneous"

class DynamicRouting(Model):
    mpe_config = MPEConfig()
    mpe_config.lims_project_name = 'dynamic_routing'
    
    
    def __init__(self):
        """
        It may make more sense to store information in a class model instead of the global state variable.
        """
        self._user_name: str = ""
        self._mouse_id: str = ""
        self._experiment_id: str = ""

    @property
    def user_name(self) -> str:
        return self._user_name

    @user_name.setter
    def user_name(self, value: str):
        self._user_name = value

    @property
    def mouse_id(self) -> str:
        return self._mouse_id

    @mouse_id.setter
    def mouse_id(self, value:str):
        self._mouse_id = value

    @property
    def experiment_id(self) -> str:
        return self._experiment_id

    @experiment_id.setter
    def experiment_id(self, value: str):
        self._experiment_id = value

    def write_platform_json(self, path: str):
        platform_data= dict(operator=self.user_name,
                             mouse_id=self.mouse_id,
                             experiment_id=self.experiment_id)

        filename = f"{self.experiment_id}_{self.mouse_id}_{time.strftime('%Y%m%d', time.localtime())}"

        with (Path(path)/filename).open('w') as platform_json:
            platform_json.write(json.dumps(platform_data))
