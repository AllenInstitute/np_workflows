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
    HAB_AUD = "stage 2 aud"
    EPHYS_AUD = "stage 2 aud opto stim"
    HAB_VIS = "stage 2 vis"
    EPHYS_VIS = "stage 2 vis opto stim"

class TempletonPilot(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass = np_session.TempletonPilotSession
    
    workflow: Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

    def task_name(self) -> str:
        task_name = super().task_name
        return f'templeton {task_name}' if 'templeton' not in task_name else task_name


    
class Hab(TempletonPilot):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            ScriptCamstim,
            NewScaleCoordinateRecorder,
        )
        super().__init__(*args, **kwargs)


class Ephys(TempletonPilot):
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
    workflow: Workflow,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match workflow:
        case Workflow.PRETEST | Workflow.EPHYS_AUD | Workflow.EPHYS_VIS:
            experiment = Ephys(mouse, user)
        case Workflow.HAB_AUD | Workflow.HAB_VIS:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid session type: {workflow}")
    experiment.workflow = workflow
    
    experiment.log(f"{experiment} created")
    
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True)
            
    return experiment


# --------------------------------------------------------------------------------------

