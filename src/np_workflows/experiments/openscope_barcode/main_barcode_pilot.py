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

logger = np_logging.getLogger(__name__)


class BarcodeSession(enum.Enum):
    """Enum for the different sessions available, each with different param sets."""

    PRETEST = "pretest"
    HAB = "hab"
    EPHYS = "ephys"


class BarcodeMixin:
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    workflow: BarcodeSession
    """Enum for particular workflow/session, e.g. PRETEST, HAB_60, HAB_90, EPHYS."""

    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.workflow:
            case BarcodeSession.PRETEST | BarcodeSession.EPHYS:
                return (Sync, VideoMVR, OpenEphys)
            case BarcodeSession.HAB:
                return (Sync, VideoMVR)

    @property
    def stims(self) -> tuple[Service, ...]:
        return (SessionCamstim, )
    
    def initialize_and_test_services(self) -> None:
        """Configure, initialize (ie. reset), then test all services."""
        
        MouseDirector.user = self.user.id
        MouseDirector.mouse = self.mouse.id

        OpenEphys.folder = self.session.folder

        NewScaleCoordinateRecorder.log_root = self.session.npexp_path
        NewScaleCoordinateRecorder.log_name = self.platform_json.path.name

        SessionCamstim.labtracks_mouse_id = self.mouse.id
        SessionCamstim.lims_user_id = self.user.id

        self.configure_services()

        super().initialize_and_test_services()

    def update_state(self) -> None:
        "Store useful but non-essential info."
        self.mouse.state['last_session'] = self.session.id
        self.mouse.state['last_barcode_session'] = str(self.workflow)
        if self.mouse == 366122:
            return
        match self.workflow:
            case BarcodeSession.PRETEST:
                return
            case BarcodeSession.HAB:
                self.session.project.state['latest_hab'] = self.session.id
            case BarcodeSession.EPHYS:
                self.session.project.state['latest_ephys'] = self.session.id
                self.session.project.state['sessions'] = self.session.project.state.get('sessions', []) + [self.session.id]
                
    def run_stim(self) -> None:

        self.update_state()
        
        if not SessionCamstim.is_ready_to_start():
            raise RuntimeError("SessionCamstim is not ready to start.")
        
        np_logging.web(f'barcode_{self.workflow.name.lower()}').info(f"Started session {self.mouse.mtrain.stage['name']}")
        SessionCamstim.start()
        
        with contextlib.suppress(Exception):
            while not SessionCamstim.is_ready_to_start():
                time.sleep(2.5)
            
        if isinstance(SessionCamstim, Finalizable):
            SessionCamstim.finalize()

        with contextlib.suppress(Exception):
            np_logging.web(f'barcode_{self.workflow.name.lower()}').info(f"Finished session {self.mouse.mtrain.stage['name']}")
            

def validate_selected_workflow(session: BarcodeSession, mouse: np_session.Mouse) -> None:
    for workflow in ('hab', 'ephys'):
        if (
            workflow in session.value.lower()
            and workflow not in mouse.mtrain.stage['name'].lower()
        ) or (
            session.value.lower() == 'ephys' and 'hab' in mouse.mtrain.stage['name'].lower()
        ):
            raise ValueError(f"Workflow selected ({session.value}) does not match MTrain stage ({mouse.mtrain.stage['name']}): please check cells above.")

    
class Hab(BarcodeMixin, np_workflows.PipelineHab):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            NewScaleCoordinateRecorder,
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
            SessionCamstim,
            OpenEphys,
        )
        super().__init__(*args, **kwargs)


# --------------------------------------------------------------------------------------


def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: BarcodeSession,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match workflow:
        case BarcodeSession.PRETEST | BarcodeSession.EPHYS:
            experiment = Ephys(mouse, user)
        case BarcodeSession.HAB:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid workflow type: {workflow}")
    experiment.workflow = workflow
    
    with contextlib.suppress(Exception):
        np_logging.web(f'barcode_{experiment.workflow.name.lower()}').info(f"{experiment} created")
            
    return experiment

