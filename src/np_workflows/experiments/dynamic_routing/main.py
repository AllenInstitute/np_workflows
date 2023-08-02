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
    """Enum for the different sessions available, each with a different
    task."""
    #! update in new_experiment and in main class
    PRETEST = "test"
    HAB_ori_AMN = "hab | stage 5 ori AMN moving" 
    EPHYS_ori_AMN = "ephys | stage 5 ori AMN moving"
    HAB_AMN_ori = "hab | stage 5 AMN ori moving" 
    EPHYS_AMN_ori = "ephys | stage 5 AMN ori moving"

def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: Workflow,
) -> 'Ephys' | 'Hab':
    """Create a new experiment for the given mouse and user."""
    if workflow.value.startswith('ephys') or  workflow.value.startswith('test'):
        experiment = Ephys(mouse, user)
    elif workflow.value.startswith('hab'):
        experiment = Hab(mouse, user)
    else:
        raise ValueError(f"Unknown {workflow = }. Create an experiment with e.g.\n\n\texperiment = Ephys(mouse, user)\nexperiment.session.npexp_path.mkdir()")
    experiment.workflow = workflow
    experiment.log(f"{experiment} created")
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True) 
    return experiment

class DRTask(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass: Type[np_session.Session] = np_session.DRPilotSession
    
    workflow: Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

    @property
    def is_pretest(self) -> bool:
        return self.workflow == Workflow.PRETEST
    
    @property
    def is_hab(self) -> bool:
        return self.workflow in (Workflow.HAB_ori_AMN, Workflow.HAB_AMN_ori)

    @property
    def task_name(self) -> str:
        if hasattr(self, '_task_name'): 
            return self._task_name 
        match self.workflow:
            case Workflow.PRETEST:
                return 'test'
            case _:
                return self.workflow.value.split('|')[-1].strip()


    @task_name.setter
    def task_name(self, value:str) -> None:
        self._task_name = value

    def log(self, message: str, weblog_name: Optional[str] = None):
        if weblog_name is None:
            weblog_name = f'{self.__class__.__name__}_{self.workflow.name.lower()}'
        super().log(message, weblog_name)

    
class Hab(DRTask):
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


class Ephys(DRTask):
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



# --------------------------------------------------------------------------------------

