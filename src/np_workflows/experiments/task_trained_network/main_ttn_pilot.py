import configparser
import contextlib
import copy
import dataclasses
import datetime
import enum
import functools
import pathlib
import platform
import threading
import time
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
    """Enum for session type, e.g. PRETEST, HAB_60, HAB_90, ECEPHYS."""
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.ttn_session:
            case TTNSession.PRETEST | TTNSession.ECEPHYS:
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

        self.configure_services()

        super().initialize_and_test_services()

    def run_stim_scripts(self) -> None:
        for stim in ('mapping', 'main', 'opto'):
            
            if not (params := self.params[stim]):
                logger.info("%s script skipped this session: %r", stim, self.ttn_session)
                continue
            
            ScriptCamstim.params = params
            ScriptCamstim.script = self.scripts[stim]
            
            logger.info("Starting %s script", stim)
            ScriptCamstim.start()
            
            with contextlib.suppress(Exception):
                while not ScriptCamstim.is_ready_to_start():
                    time.sleep(2.5)
                
            logger.info("%s script complete", stim)
            
            if isinstance(ScriptCamstim, Finalizable):
                ScriptCamstim.finalize()

    @property
    def params(self) -> dict[Literal["main", "mapping", "opto", "system"], dict[str, Any]]:
        params = copy.deepcopy(DEFAULT_STIM_PARAMS)
        if system := self.system_camstim_params:
            params["system"] = system
        params["main"] = per_session_main_stim_params(self.ttn_session)
        params["mapping"] = per_session_mapping_params(self.ttn_session)
        params["opto"] =  per_session_opto_params(self.ttn_session, self.mouse)
        return params

    @functools.cached_property
    def scripts(self) -> dict[Literal["main", "mapping", "opto"], str]:
        "Relative path to script on Stim computer from current location."
        return {label: np_config.local_to_unc(
            platform.node(),
            pathlib.Path(__file__).parent / 'camstim_scripts' / f"ttn_{label}_script.py"
        ).as_posix() for label in ("main", "mapping", "opto")}

    @functools.cached_property
    def system_camstim_params(self) -> dict[str, Any]:
        "System config on Stim computer, if accessible."
        return camstim_defaults()
    
class Hab(TTNMixin, np_workflows.Hab):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            ScriptCamstim,
        )
        super().__init__(*args, **kwargs)


class Ecephys(TTNMixin, np_workflows.Ecephys):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            ScriptCamstim,
            OpenEphys,
            NewScaleCoordinateRecorder,
        )
        super().__init__(*args, **kwargs)


# --------------------------------------------------------------------------------------


def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    session: TTNSession,
) -> Ecephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match session:
        case TTNSession.PRETEST | TTNSession.ECEPHYS:
            experiment = Ecephys(mouse, user)
        case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {session}")
    experiment.ttn_session = session
    logger.info("Created new experiment session: %s", experiment)
    return experiment


# --------------------------------------------------------------------------------------
