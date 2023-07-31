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

from np_workflows.shared import base_experiments

logger = np_logging.getLogger(__name__)

class Workflow(enum.Enum):
    """Enum for the different TTN sessions available, each with a different task."""
    PRETEST = "test"
    HAB_AUD = "hab: stage 2 aud"
    EPHYS_AUD = "ephys: stage 2 aud"
    HAB_VIS = "hab: stage 2 vis"
    EPHYS_VIS = "ephys: stage 2 vis"

class TempletonPilot(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass: Type[np_session.Session] = np_session.TempletonPilotSession
    
    workflow: Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

    @property
    def is_pretest(self) -> bool:
        return self.workflow == Workflow.PRETEST
    
    @property
    def is_hab(self) -> bool:
        return self.workflow == Workflow.HAB_AUD or self.workflow == Workflow.HAB_VIS

    @property
    def task_name(self) -> str:
        if hasattr(self, '_task_name'): 
            return self._task_name 
        match self.workflow:
            case Workflow.PRETEST:
                return 'templeton test'
            case Workflow.HAB_AUD | Workflow.EPHYS_AUD:
                return 'templeton stage 2 aud'
            case Workflow.HAB_VIS | Workflow.EPHYS_VIS:
                return 'templeton stage 2 vis'

    @task_name.setter
    def task_name(self, value:str) -> None:
        try:
            Workflow(value)
        except ValueError:
            print(f"Not a known task name, but the attribute is updated anyway!")
        self._task_name = value

    def log(self, message: str, weblog_name: Optional[str] = None):
        if weblog_name is None:
            weblog_name = f'templeton_{self.workflow.name.lower()}'
        super().log(message, weblog_name)

    
class Hab(TempletonPilot):
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


class Ephys(TempletonPilot):
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
    workflow: Workflow,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match workflow:
        case Workflow.PRETEST | Workflow.EPHYS_AUD | Workflow.EPHYS_VIS:
            experiment = Ephys(str(mouse), str(user))
        case Workflow.HAB_AUD | Workflow.HAB_VIS:
            experiment = Hab(str(mouse), str(user))
        case _:
            raise ValueError(f"Invalid session type: {workflow}")
    experiment.workflow = workflow
    
    experiment.log(f"{experiment} created")
    
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True)
            
    return experiment


# --------------------------------------------------------------------------------------

