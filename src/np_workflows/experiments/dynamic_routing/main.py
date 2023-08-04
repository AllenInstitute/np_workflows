from __future__ import annotations

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
    PRETEST = "test"
    HAB_ori_AMN = "stage 5 ori AMN moving" 
    EPHYS_ori_AMN = "stage 5 ori AMN moving"
    OPTO_ori_AMN = "opto stim ori AMN moving"
    HAB_AMN_ori = "stage 5 AMN ori moving" 
    EPHYS_AMN_ori = "stage 5 AMN ori moving"
    OPTO_AMN_ori = "opto stim AMN ori moving"
    
def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: Workflow,
) -> DRTask:
    """Create a new experiment for the given mouse and user."""
    experiment: DRTask
    if workflow.name.startswith('EPHYS') or  workflow.name == 'PRETEST':
        experiment = Ephys(mouse, user)
    elif workflow.name.startswith('HAB'):
        experiment = Hab(mouse, user)
    elif workflow.name.startswith('OPTO'):
        experiment = Opto(mouse, user)
    else:
        raise ValueError(f"Unknown {workflow = }. Create an experiment with e.g.\n\n\texperiment = Ephys(mouse, user)\nexperiment.session.npexp_path.mkdir()")
    experiment.workflow = workflow
    experiment.log(f"{experiment} created")
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True) 
    return experiment

class DRTask(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass = np_session.DRPilotSession
    
    workflow: Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

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
        
class Opto(DRTask):
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