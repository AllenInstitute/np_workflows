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


class VippoSession(enum.Enum):
    """Enum for the different sessions available, each with different param sets."""

    PRETEST = "pretest"
    HAB = "hab"
    EPHYS = "ephys"


class VippoMixin:
    """Provides project-specific methods and attributes, mainly related to camstim scripts."""
    
    workflow: VippoSession
    """Enum for particular workflow/session, e.g. PRETEST, HAB_60, HAB_90,
    EPHYS."""
    
    session: np_session.PipelineSession
    mouse: np_session.Mouse
    user: np_session.User
    platform_json: np_session.PlatformJson
    
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        match self.workflow:
            case VippoSession.PRETEST | VippoSession.EPHYS:
                return (Sync, VideoMVR, OpenEphys)
            case VippoSession.HAB:
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
        self.mouse.state['last_vippo_session'] = str(self.workflow)
        if self.mouse == 366122:
            return
        match self.workflow:
            case VippoSession.PRETEST:
                return
            case VippoSession.HAB:
                self.session.project.state['latest_hab'] = self.session.id
            case VippoSession.EPHYS:
                self.session.project.state['latest_ephys'] = self.session.id
                self.session.project.state['sessions'] = self.session.project.state.get('sessions', []) + [self.session.id]
                
    def run_stim(self) -> None:

        self.update_state()
        
        if not SessionCamstim.is_ready_to_start():
            raise RuntimeError("SessionCamstim is not ready to start.")
        
        np_logging.web(f'vippo_{self.workflow.name.lower()}').info(f"Started session {self.mouse.mtrain.stage['name']}")
        SessionCamstim.start()
        
        with contextlib.suppress(Exception):
            while not SessionCamstim.is_ready_to_start():
                time.sleep(2.5)
            
        if isinstance(SessionCamstim, Finalizable):
            SessionCamstim.finalize()

        with contextlib.suppress(Exception):
            np_logging.web(f'vippo_{self.workflow.name.lower()}').info(f"Finished session {self.mouse.mtrain.stage['name']}")
    
    
    def copy_data_files(self) -> None: 
        super().copy_data_files()
        
        # When all processing completes, camstim Agent class passes data and uuid to
        # /camstim/lims BehaviorSession class, and write_behavior_data() writes a
        # final .pkl with default name YYYYMMDDSSSS_mouseID_foragingID.pkl
        # - if we have a foraging ID, we can search for that
        if None == (stim_pkl := next(self.session.npexp_path.glob(f'{self.session.date:%y%m%d}*_{self.session.mouse}_*.pkl'), None)):
            logger.warning('Did not find stim file on npexp matching the format `YYYYMMDDSSSS_mouseID_foragingID.pkl`')
            return
        assert stim_pkl
        if not self.session.platform_json.foraging_id:
            self.session.platform_json.foraging_id = stim_pkl.stem.split('_')[-1]
        new_stem = f'{self.session.folder}.stim'
        logger.debug(f'Renaming stim file copied to npexp: {stim_pkl} -> {new_stem}')
        stim_pkl = stim_pkl.rename(stim_pkl.with_stem(new_stem))
        
        # remove other stim pkl, which is nearly identical, if it was also copied
        for pkl in self.session.npexp_path.glob('*.pkl'):
            if (
                self.session.folder not in pkl.stem
                and 
                abs(pkl.stat().st_size - stim_pkl.stat().st_size) < 1e6
            ):
                logger.debug(f'Deleting extra stim pkl copied to npexp: {pkl.stem}')
                pkl.unlink()
        
        
def validate_selected_workflow(session: VippoSession, mouse: np_session.Mouse) -> None:
    for workflow in ('hab', 'ephys'):
        if (
            workflow in session.value.lower()
            and workflow not in mouse.mtrain.stage['name'].lower()
        ) or (
            session.value.lower() == 'ephys' and 'hab' in mouse.mtrain.stage['name'].lower()
        ):
            raise ValueError(f"Workflow selected ({session.value}) does not match MTrain stage ({mouse.mtrain.stage['name']}): please check cells above.")

    
class Hab(VippoMixin, np_workflows.PipelineHab):
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


class Ephys(VippoMixin, np_workflows.PipelineEphys):
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
    workflow: VippoSession,
) -> Ephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match workflow:
        case VippoSession.PRETEST | VippoSession.EPHYS:
            experiment = Ephys(mouse, user)
        case VippoSession.HAB:
            experiment = Hab(mouse, user)
        case _:
            raise ValueError(f"Invalid workflow type: {workflow}")
    experiment.workflow = workflow
    
    with contextlib.suppress(Exception):
        np_logging.web(f'vippo_{experiment.workflow.name.lower()}').info(f"{experiment} created")
            
    return experiment

