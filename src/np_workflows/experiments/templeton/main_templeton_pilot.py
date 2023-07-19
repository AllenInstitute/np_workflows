import configparser
import contextlib
import copy
import dataclasses
import datetime
import enum
import functools
import pathlib
import platform
import shutil
import threading
import time
import zlib
from typing import ClassVar, Literal, NamedTuple, NoReturn, Optional, Type, TypedDict

import IPython
import IPython.display
import ipywidgets as ipw
import np_config
import np_logging
import np_services
import np_session
import np_workflows
import fabric
import PIL.Image
import pydantic
from pyparsing import Any
from np_services import (
    Service,
    Finalizable,
    ScriptCamstim,
    OpenEphys,
    Sync,
    ImageMVR,
    VideoMVR,
    NewScaleCoordinateRecorder,
    MouseDirector,
)

from np_workflows.shared.base_experiments import WithSessionInfo
from np_workflows.experiments.templeton.templeton_stim_config import (
    TempletonWorkflow,
    camstim_defaults,
)

logger = np_logging.getLogger(__name__)

class TempletonMixin:
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass: Type[np_session.Session] = np_session.TempletonPilotSession
    
    workflow: TempletonWorkflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""
    
    services = (Sync, VideoMVR, ImageMVR, OpenEphys, NewScaleCoordinateRecorder, ScriptCamstim, MouseDirector)
    stims = (ScriptCamstim,)
    
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.workflow:
            case TempletonWorkflow.PRETEST | TempletonWorkflow.EPHYS_AUD | TempletonWorkflow.EPHYS_VIS:
                return (Sync, VideoMVR, OpenEphys)
            case TempletonWorkflow.HAB_AUD | TempletonWorkflow.HAB_VIS:
                return (Sync, VideoMVR)

    @property
    def task_name(self) -> str:
        if hasattr(self, '_task_name'): 
            return self._task_name 
        match self.workflow:
            case TempletonWorkflow.PRETEST:
                return 'templeton test'
            case TempletonWorkflow.HAB_AUD | TempletonWorkflow.EPHYS_AUD:
                return 'templeton stage 2 aud'
            case TempletonWorkflow.HAB_VIS | TempletonWorkflow.EPHYS_VIS:
                return 'templeton stage 2 vis'

    @task_name.setter
    def task_name(self, value:str) -> None:
        try:
            TempletonWorkflow(value)
        except ValueError:
            print(f"Not a recognized task, but the attribute is updated!")
        self._task_name = value

    @property
    def task_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/DynamicRouting1.py',
                taskVersion = self.task_name,
        )
        
    @property
    def spontaneous_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse) if self.workflow != TempletonWorkflow.PRETEST else 'test',
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/TaskControl.py',
                taskVersion = 'spontaneous',
        )
        
    @property
    def spontaneous_rewards_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse) if self.workflow != TempletonWorkflow.PRETEST else 'test',
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/TaskControl.py',
                taskVersion = 'spontaneous rewards',
                # rewardSound = "device",
        )
        
    @property
    def optotagging_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        locs_root = pathlib.Path("//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/OptoGui/optolocs")
        locs = sorted(tuple(locs_root.glob(f"optolocs_{self.mouse.id}_{str(self.rig).replace('.', '')}_*")), reverse=True)[0]
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse) if self.workflow != TempletonWorkflow.PRETEST else 'test',
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/TaskControl.py',
                taskVersion = 'optotagging',
                optoTaggingLocs = locs.as_posix(),
        )

    @property
    def mapping_params(self: WithSessionInfo) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/RFMapping.py'
            )

    @property
    def sound_test_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = 'sound',
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/TaskControl.py',
                taskVersion = 'sound test',
        )
    
    def run_script(self, stim: Literal['sound_test', 'mapping', 'task', 'optotagging', 'spontaneous', 'spontaneous_rewards']) -> None:
        ScriptCamstim.script = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/runTask.py'
        ScriptCamstim.params = getattr(self, f'{stim.replace(" ", "_")}_params')

        with contextlib.suppress(Exception):
            np_logging.web(f'templeton_{self.workflow.name.lower()}').info(f"{stim} started")

        ScriptCamstim.start()

        while not ScriptCamstim.is_ready_to_start():
            time.sleep(1)

        with contextlib.suppress(Exception):
            np_logging.web(f'templeton_{self.workflow.name.lower()}').info(f"{stim} complete")
    
    
    run_mapping = functools.partialmethod(run_script, 'mapping')
    run_sound_test = functools.partialmethod(run_script, 'sound_test')
    run_optotagging = functools.partialmethod(run_script, 'optotagging')
    run_spontaneous = functools.partialmethod(run_script, 'spontaneous')
    run_spontaneous_rewards = functools.partialmethod(run_script, 'spontaneous_rewards')
    
    def run_task(self) -> None:
        self.update_state()
        self.run_script('task')    
           
    def run_stim_desktop_theme_script(self, selection: str) -> None:     
        ScriptCamstim.script = '//allen/programs/mindscope/workgroups/dynamicrouting/ben/change_desktop.py'
        ScriptCamstim.params = {'selection': selection}
        ScriptCamstim.start()
        while not ScriptCamstim.is_ready_to_start():
            time.sleep(0.1)

    set_grey_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'grey')
    set_dark_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'dark')
    reset_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'reset')
        

    def initialize_and_test_services(self) -> None:
        """Configure, initialize (ie. reset), then test all services."""
        
        ScriptCamstim.script = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/runTask.py'
        ScriptCamstim.data_root = pathlib.Path('//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/Data') / str(self.mouse)

        MouseDirector.user = self.user.id
        MouseDirector.mouse = self.mouse.id

        OpenEphys.folder = self.session.folder

        NewScaleCoordinateRecorder.log_root = self.session.npexp_path

        self.configure_services()

        super().initialize_and_test_services()

    def update_state(self) -> None:
        "Store useful but non-essential info."
        self.mouse.state['last_session'] = self.session.id
        self.mouse.state['last_workflow'] = str(self.workflow.name)
        self.mouse.state['last_task'] = str(self.task_name)

    @functools.cached_property
    def system_camstim_params(self) -> dict[str, Any]:
        "System config on Stim computer, if accessible."
        return camstim_defaults()
    
    def copy_data_files(self) -> None:
        """Copy files from raw data storage to session folder for all services
        except Open Ephys."""
        password = input(f'Enter password for svc_neuropix:')
        for service in self.services:
            match service.__class__.__name__:
                case "OpenEphys" | "open_ephys":
                    continue # copy ephys separately
                case _:
                    with contextlib.suppress(AttributeError):
                        files = service.data_files or service.get_latest_data('*')
                        if not files:
                            continue
                        files = set(files)
                        print(files)
                        for file in files:
                            shutil.copy2(file, self.npexp_path)
        
    def copy_ephys(self) -> None:
        password = np_config.fetch('/logins')['svc_neuropix']['password']
        ssh = fabric.Connection(host=np_services.OpenEphys.host, user='svc_neuropix', connect_kwargs=dict(password=password))
        for ephys_folder in np_services.OpenEphys.data_files:

            with contextlib.suppress(Exception):
                with ssh:
                    ssh.run(
                        f'robocopy "{ephys_folder}" "{self.npexp_path}" /j /s /xo' 
                        # /j unbuffered, /s incl non-empty subdirs, /xo exclude src files older than dest
                    )
        
    
class Hab(TempletonMixin, np_workflows.WithSession):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            NewScaleCoordinateRecorder,
            ScriptCamstim,
        )
        super().__init__(*args, **kwargs)


class Ephys(TempletonMixin, np_workflows.WithSession):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            NewScaleCoordinateRecorder,
            ScriptCamstim,
            OpenEphys,
        )
        super().__init__(*args, **kwargs)


# --------------------------------------------------------------------------------------


def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: TempletonWorkflow,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match workflow:
        case TempletonWorkflow.PRETEST | TempletonWorkflow.EPHYS_AUD | TempletonWorkflow.EPHYS_VIS:
            experiment = Ephys(mouse, user)
        case TempletonWorkflow.HAB_AUD | TempletonWorkflow.HAB_VIS:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {workflow}")
    experiment.workflow = workflow
    
    with contextlib.suppress(Exception):
        np_logging.web(f'templeton_{experiment.workflow.name.lower()}').info(f"{experiment} created")
    
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True)
            
    return experiment


# --------------------------------------------------------------------------------------

def validate_or_overwrite(validate: str | pathlib.Path, src: str | pathlib.Path):
    "Checksum validate against `src`, (over)write `validate` as `src` if different."
    validate, src = pathlib.Path(validate), pathlib.Path(src)
    def copy():
        logger.debug("Copying %s to %s", src, validate)
        shutil.copy2(src, validate)
    while (
        validate.exists() == False
        or (v := zlib.crc32(validate.read_bytes())) != (c := zlib.crc32(pathlib.Path(src).read_bytes()))
        ):
        copy()
    logger.debug("Validated %s CRC32: %08X", validate, (v & 0xFFFFFFFF) )
