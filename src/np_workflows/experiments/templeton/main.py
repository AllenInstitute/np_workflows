from __future__ import annotations

import np_logging
import np_session
from np_services import (
    MouseDirector,
    NewScaleCoordinateRecorder,
    OpenEphys,
    ScriptCamstim,
    Sync,
    VideoMVR,
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
        return f"templeton {task_name}" if "templeton" not in task_name else task_name

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
    if any(tag in workflow.name for tag in ("EPHYS", "PRETEST")):
        experiment = Ephys(mouse, user)
    elif "HAB" in workflow.name:
        experiment = Hab(mouse, user)
    else:
        raise ValueError(
            f"Unknown {workflow = }. Create an experiment with e.g.\n\n\texperiment = Ephys(mouse, user)\nexperiment.session.npexp_path.mkdir()"
        )
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
