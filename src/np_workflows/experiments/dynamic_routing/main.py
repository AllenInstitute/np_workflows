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


def new_experiment(
    mouse: int | str | np_session.Mouse,
    user: str | np_session.User,
    workflow: base_experiments.DynamicRoutingExperiment.Workflow,
) -> DRTask:
    """Create a new experiment for the given mouse and user."""
    experiment: DRTask
    if any(tag in workflow.name for tag in ("EPHYS", "PRETEST")):
        experiment = Ephys(mouse, user)
    elif "HAB" in workflow.name:
        experiment = Hab(mouse, user)
    elif "OPTO" in workflow.name:
        experiment = Opto(mouse, user)
    elif "TRAINING" in workflow.name:
        experiment = Training(mouse, user)
    else:
        raise ValueError(
            f"Unknown {workflow = }. Create an experiment with e.g.\n\n\texperiment = Ephys(mouse, user)\nexperiment.session.npexp_path.mkdir()"
        )
    experiment.workflow = workflow
    experiment.log(f"{experiment} created")
    experiment.session.npexp_path.mkdir(parents=True, exist_ok=True)
    return experiment


class DRTask(base_experiments.DynamicRoutingExperiment):
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""

    default_session_subclass = np_session.DRPilotSession

    workflow: base_experiments.DynamicRoutingExperiment.Workflow
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""


class Hab(DRTask):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            ScriptCamstim,
        )
        super().__init__(*args, **kwargs)


class Opto(DRTask):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            ScriptCamstim,
        )
        super().__init__(*args, **kwargs)


class Ephys(DRTask):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            NewScaleCoordinateRecorder,
            ScriptCamstim,
            OpenEphys,
        )
        super().__init__(*args, **kwargs)


class Training(DRTask):
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            ScriptCamstim,
        )
        super().__init__(*args, **kwargs)
