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
import PIL.Image
import pydantic
from pyparsing import Any
from np_services import (
    Service,
    Finalizable,
    ScriptCamstim,
    OpenEphys,
    Sync,
    VideoMVR,
    NewScaleCoordinateRecorder,
    MouseDirector,
)

from .ttn_stim_config import (
    TTNSession,
    camstim_defaults,
    per_session_main_stim_params,
    per_session_mapping_params,
    per_session_opto_params,
    default_ttn_params as DEFAULT_STIM_PARAMS,
)

logger = np_logging.getLogger(__name__)


class TTNMixin:
    """Provides TTN-specific methods and attributes, mainly related to camstim scripts."""
    
    ttn_session: TTNSession
    """Enum for session type, e.g. PRETEST, HAB_60, HAB_90, EPHYS."""

    @property
    def script_root_on_stim(self) -> pathlib.Path:
        "Path to local copy on Stim, from Stim."
        return pathlib.Path('C:/ProgramData/StimulusFiles/dev')
    
    @property
    def script_root_on_local(self) -> pathlib.Path:
        "Path to version controlled scripts on local machine."
        return (pathlib.Path(__file__).parent / 'camstim_scripts')
    
    @property
    def script_names(self) -> dict[Literal["main", "mapping", "opto"], str]:
        return {
            label: f"ttn_{label}_script.py" for label in ("main", "mapping", "opto")
        }
        
    @property
    def stim_root_on_stim(self) -> pathlib.Path:
        "Path to dev folder on Stim computer, as seen from local machine."
        return np_config.local_to_unc(self.rig.stim, self.script_root_on_stim)
    
    @property
    def stim_root_on_local(self) -> pathlib.Path:
        "Path to version controlled stim files on local machine."
        return self.script_root_on_local / 'stims'
        
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.ttn_session:
            case TTNSession.PRETEST | TTNSession.EPHYS:
                return (Sync, VideoMVR, OpenEphys)
            case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
                return (Sync, VideoMVR)

    @property
    def stims(self) -> tuple[Service, ...]:
        return (ScriptCamstim,)
    
    def initialize_and_test_services(self) -> None:
        """Configure, initialize (ie. reset), then test all services."""
        
        MouseDirector.user = self.user.id
        MouseDirector.mouse = self.mouse.id

        OpenEphys.folder = self.session.folder

        NewScaleCoordinateRecorder.log_root = self.session.npexp_path
        NewScaleCoordinateRecorder.log_name = self.platform_json.path.name

        self.configure_services()

        super().initialize_and_test_services()


    def update_state(self) -> None:
        "Store useful but non-essential info."
        self.mouse.state['last_session'] = self.session.id
        self.mouse.state['last_ttn_session'] = str(self.ttn_session)
        if self.mouse == 366122:
            return
        match self.ttn_session:
            case TTNSession.PRETEST:
                return
            case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
                self.session.project.state['latest_hab'] = self.session.id
            case TTNSession.EPHYS:
                self.session.project.state['latest_ephys'] = self.session.id
                self.session.project.state['sessions'] = self.session.project.state.get('sessions', []) + [self.session.id]
                
    def run_stim_scripts(self) -> None:
        self.validate_or_copy_stim_files()
        self.update_state()
        
        for stim in ('mapping', 'main', 'opto'):
            
            if not (params := self.params[stim]):
                logger.info("%s script skipped this session: %r", stim, self.ttn_session)
                continue
            
            ScriptCamstim.params = params
            ScriptCamstim.script = self.scripts[stim]
            
            logger.debug("Starting %s script", stim)
            
            ScriptCamstim.start()
            
            with contextlib.suppress(Exception):
                np_logging.web(f'ttn_{self.ttn_session.name.lower()}').info(f"{stim.capitalize()} stim started")
            
            with contextlib.suppress(Exception):
                while not ScriptCamstim.is_ready_to_start():
                    time.sleep(2.5)
                
            if isinstance(ScriptCamstim, Finalizable):
                ScriptCamstim.finalize()

            with contextlib.suppress(Exception):
                np_logging.web(f'ttn_{self.ttn_session.name.lower()}').info(f"{stim.capitalize()} stim finished")
    
    def validate_or_copy_stim_files(self):
        for vc_copy in self.stim_root_on_local.iterdir():
            stim_copy = self.stim_root_on_stim / vc_copy.name
            validate_or_overwrite(validate=stim_copy, src=vc_copy)
            
    @property
    def params(self) -> dict[Literal["main", "mapping", "opto", "system"], dict[str, Any]]:
        params = copy.deepcopy(DEFAULT_STIM_PARAMS)
        params["mouse_id"] = str(self.mouse)
        params["user_id"] = str(self.user)
        if system := self.system_camstim_params:
            params["system"] = system
        params["main"] = per_session_main_stim_params(self.ttn_session)
        params["mapping"] = per_session_mapping_params(self.ttn_session)
        params["opto"] =  per_session_opto_params(self.ttn_session, self.mouse)
        return params

    @functools.cached_property
    def scripts(self) -> dict[Literal["main", "mapping", "opto"], str]:
        """Local path on Stim computer to each script.
        
        Verifies Stim copy matches v.c., or overwrites on Stim.
        """
        for label in ("main", "mapping", "opto"):
            script = self.script_names[label]
            vc_copy = self.script_root_on_local / script
            stim_copy = np_config.local_to_unc(
                self.rig.stim, self.script_root_on_stim / script,
            )
            
            validate_or_overwrite(validate=stim_copy, src=vc_copy)
            
        return {
            label: str(self.script_root_on_stim / script) 
            for label, script in self.script_names.items()
        }

    @functools.cached_property
    def system_camstim_params(self) -> dict[str, Any]:
        "System config on Stim computer, if accessible."
        return camstim_defaults()
    
    
class Hab(TTNMixin, np_workflows.PipelineHab):
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


class Ephys(TTNMixin, np_workflows.PipelineEphys):
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
    session: TTNSession,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match session:
        case TTNSession.PRETEST | TTNSession.EPHYS:
            experiment = Ephys(mouse, user)
        case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {session}")
    experiment.ttn_session = session
    
    with contextlib.suppress(Exception):
        np_logging.web(f'ttn_{experiment.ttn_session.name.lower()}').info(f"{experiment} created")
            
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
