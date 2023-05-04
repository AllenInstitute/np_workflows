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
from typing import ClassVar, Literal, NamedTuple, NoReturn, Optional, TypedDict

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

from .templeton_stim_config import (
    TempletonSession,
    camstim_defaults,
)

logger = np_logging.getLogger(__name__)


class TempletonMixin:
    """Provides TTN-specific methods and attributes, mainly related to camstim scripts."""
    
    templeton_session: TempletonSession
    """Enum for session type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""
    
    services = (Sync, VideoMVR, ImageMVR, OpenEphys, NewScaleCoordinateRecorder, ScriptCamstim)
    stims = (ScriptCamstim,)
    
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.templeton_session:
            case TempletonSession.PRETEST | TempletonSession.EPHYS_AUD | TempletonSession.EPHYS_VIS:
                return (Sync, VideoMVR, OpenEphys)
            case TempletonSession.HAB_AUD | TempletonSession.HAB_VIS:
                return (Sync, VideoMVR)

    @property
    def task_name(self) -> str:
        match self.templeton_session:
            case TempletonSession.PRETEST | TempletonSession.EPHYS_AUD | TempletonSession.EPHYS_VIS:
                return NotImplemented # TODO
            case TempletonSession.HAB_AUD | TempletonSession.HAB_VIS:
                return NotImplemented # TODO

    @property
    def task_params(self) -> dict[str, str]:
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/DynamicRouting1.py',
                taskVersion = self.task_name,
        )

    @property
    def mapping_params(self) -> dict[str, str]:
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/RFMapping.py'
            )

    @property
    def sound_test_params(self) -> dict[str, str]:
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = 'sound',
                taskScript = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/TaskControl.py',
                taskVersion = 'sound test',
        )

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
        self.mouse.state['last_templeton_session'] = str(self.templeton_session)


    def run_sound_test(self) -> None:
        ScriptCamstim.params = self.sound_test_params
        ScriptCamstim.start()

        while not ScriptCamstim.is_ready_to_start():
            time.sleep(1)

        # re-initialize in case sound test produces pkl files that we don't want:
        ScriptCamstim.initialize()

    
    def run_script(self, stim: Literal['sound_test', 'mapping', 'task']) -> None:
        ScriptCamstim.params = getattr(self, f'{stim}')

        with contextlib.suppress(Exception):
            np_logging.web(f'templeton_{self.templeton_session.name.lower()}').info(f"{stim} started")

        ScriptCamstim.start()

        while not ScriptCamstim.is_ready_to_start():
            time.sleep(10)

        if isinstance(ScriptCamstim, Finalizable):
            ScriptCamstim.finalize()

        with contextlib.suppress(Exception):
            np_logging.web(f'templeton_{self.templeton_session.name.lower()}').info(f"{stim} complete")
    
    
    def run_mapping(self) -> None:
        self.run_script('mapping')    
            
    def run_sound_test(self) -> None:
        self.run_script('sound_test')    
            
    def run_task(self) -> None:
        self.update_state()
        self.run_script('task')    
                

    @functools.cached_property
    def system_camstim_params(self) -> dict[str, Any]:
        "System config on Stim computer, if accessible."
        return camstim_defaults()
    
    def copy_data_files(self) -> None:
        """Copy files from raw data storage to session folder for all services."""
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
    session: TempletonSession,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match session:
        case TempletonSession.PRETEST | TempletonSession.EPHYS_AUD | TempletonSession.EPHYS_VIS:
            experiment = Ephys(mouse, user)
        case TempletonSession.HAB_AUD | TempletonSession.HAB_VIS:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {session}")
    experiment.templeton_session = session
    
    with contextlib.suppress(Exception):
        np_logging.web(f'templeton_{experiment.templeton_session.name.lower()}').info(f"{experiment} created")
    
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
