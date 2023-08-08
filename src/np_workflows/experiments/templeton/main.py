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

class TempletonPilot(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    default_session_subclass = np_session.TempletonPilotSession
    
    workflow: base_experiments.DynamicRoutingExperiment.Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

    @property
    def task_name(self) -> str:
        task_name = super().task_name
        return f'templeton {task_name}' if 'templeton' not in task_name else task_name
    
    @task_name.setter
    def task_name(self, value: str):
        super().task_name = value
       
def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: base_experiments.DynamicRoutingExperiment.Workflow,
) -> TempletonPilot:
    """Create a new experiment for the given mouse and user."""
    experiment: Ephys | Hab
    if any(tag in workflow.name for tag in ('EPHYS', 'PRETEST')):
        experiment = Ephys(mouse, user)
    elif 'HAB' in workflow.name:
        experiment = Hab(mouse, user)
    else:
        raise ValueError(f"Unknown {workflow = }. Create an experiment with e.g.\n\n\texperiment = Ephys(mouse, user)\nexperiment.session.npexp_path.mkdir()")
    experiment.workflow = workflow
    experiment.log(f"{experiment} created")
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True) 
    return experiment

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