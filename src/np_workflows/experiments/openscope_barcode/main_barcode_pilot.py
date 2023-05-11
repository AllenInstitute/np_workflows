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
    ScriptCamstim, SessionCamstim, 
    SessionCamstim,
    OpenEphys,
    Sync,
    VideoMVR,
    NewScaleCoordinateRecorder,
    MouseDirector,
)

from .barcode_stim_config import (
    BarcodeSession,
    camstim_defaults,
    per_session_main_stim_params,
    per_session_mapping_params,
    per_session_opto_params,
    default_barcode_params as DEFAULT_STIM_PARAMS,
)

logger = np_logging.getLogger(__name__)


class BarcodeMixin:
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    session_type: BarcodeSession
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
            label: f"barcode_{label}_script.py" for label in ("main", "mapping", "opto")
        }
        
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.session_type:
            case BarcodeSession.PRETEST | BarcodeSession.EPHYS:
                return (Sync, VideoMVR, OpenEphys)
            case BarcodeSession.HAB_60 | BarcodeSession.HAB_90 | BarcodeSession.HAB_120:
                return (Sync, VideoMVR)

    @property
    def stims(self) -> tuple[Service, ...]:
        return (ScriptCamstim, SessionCamstim)
    
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
        self.mouse.state['last_barcode_session'] = str(self.session_type)
        if self.mouse == 366122:
            return
        match self.session_type:
            case BarcodeSession.PRETEST:
                return
            case BarcodeSession.HAB_60 | BarcodeSession.HAB_90 | BarcodeSession.HAB_120:
                self.session.project.state['latest_hab'] = self.session.id
            case BarcodeSession.EPHYS:
                self.session.project.state['latest_ephys'] = self.session.id
                self.session.project.state['sessions'] = self.session.project.state.get('sessions', []) + [self.session.id]
                
    def run_stim_scripts(self) -> None:

        self.update_state()
        
        for stim in ('mapping', 'main', 'opto'):
            
            if not (params := self.params[stim]):
                logger.info("%s script skipped this session: %r", stim, self.session_type)
                continue
            
            ScriptCamstim.params = params
            ScriptCamstim.script = self.scripts[stim]
            
            logger.debug("Starting %s script", stim)
            
            ScriptCamstim.start()
            
            with contextlib.suppress(Exception):
                np_logging.web(f'barcode_{self.session_type.name.lower()}').info(f"{stim.capitalize()} stim started")
            
            with contextlib.suppress(Exception):
                while not ScriptCamstim.is_ready_to_start():
                    time.sleep(2.5)
                
            if isinstance(ScriptCamstim, Finalizable):
                ScriptCamstim.finalize()

            with contextlib.suppress(Exception):
                np_logging.web(f'barcode_{self.session_type.name.lower()}').info(f"{stim.capitalize()} stim finished")
            
            
    @property
    def params(self) -> dict[Literal["main", "mapping", "opto", "system"], dict[str, Any]]:
        params = copy.deepcopy(DEFAULT_STIM_PARAMS)
        if system := self.system_camstim_params:
            params["system"] = system
        params["main"] = per_session_main_stim_params(self.session_type)
        params["mapping"] = per_session_mapping_params(self.session_type)
        params["opto"] =  per_session_opto_params(self.session_type, self.mouse)
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
    
    
class Hab(BarcodeMixin, np_workflows.PipelineHab):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            NewScaleCoordinateRecorder,
            ScriptCamstim,
            SessionCamstim,
        )
        super().__init__(*args, **kwargs)


class Ephys(BarcodeMixin, np_workflows.PipelineEphys):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            NewScaleCoordinateRecorder,
            ScriptCamstim,
            SessionCamstim,
            OpenEphys,
        )
        super().__init__(*args, **kwargs)


# --------------------------------------------------------------------------------------


def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    session: BarcodeSession,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match session:
        case BarcodeSession.PRETEST | BarcodeSession.EPHYS:
            experiment = Ephys(mouse, user)
        case BarcodeSession.HAB_60 | BarcodeSession.HAB_90 | BarcodeSession.HAB_120:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {session}")
    experiment.session_type = session
    
    with contextlib.suppress(Exception):
        np_logging.web(f'barcode_{experiment.session_type.name.lower()}').info(f"{experiment} created")
            
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
