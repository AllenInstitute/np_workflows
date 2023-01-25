from __future__ import annotations

import contextlib
import datetime
import pickle
import functools
import pathlib
import typing
from typing import Any, Optional, Protocol, Union, Sequence, ClassVar, Type


from np_workflows.services import config
from np_workflows.services.protocols import Service
from np_workflows.services.proxies import ImageMVR, VideoMVR, Sync, NoCamstim, ScriptCamstim, SessionCamstim, JsonRecorder, NewScaleCoordinateRecorder
from np_workflows.services import open_ephys as OpenEphys
from np_workflows.experiments.baseclasses import WithLims

import np_config
# import np_session
import np_logging

logger = np_logging.getLogger(__name__)

class Pretest(WithLims):
    
    rig_idx: ClassVar[int] = config.Rig.idx
    
    services: tuple[Service, ...] = (
        Sync, OpenEphys, ImageMVR, VideoMVR, NewScaleCoordinateRecorder, ScriptCamstim,
    )
    "All services used in the experiment that support Service protocols."
    
    photodoc_services: tuple[Service, ...] = (
        ImageMVR, NewScaleCoordinateRecorder,
    )
    "Services called during photodoc capture."
    
    photodoc_labels: tuple[str, ...] = (
        'pretest',
    )
    
    recorder: tuple[Service, ...] = (
        # JsonRecorder, #! update with platform json recorder when available
    )
    
    def __init__(self,
        labtracks_mouse_id: int | str,
        lims_user_id: str,
        lims_session_id: Optional[int] = None,
        *args,
        **kwargs,
    ) -> None:
        
        logger.debug(f"Initializing {__class__.__name__}({labtracks_mouse_id}, {lims_user_id})")
        
        self.config : dict[str, dict[str, Any]]  = (
            np_config.from_zk('/projects/np_workflows/defaults/configuration') 
            | np_config.from_zk(f'/rigs/NP.{self.rig_idx}')
        )
        labtracks_mouse_id=self.config['pretest_mouse']
        logger.debug(f"{__class__.__name__} modifying mouse ID to use {labtracks_mouse_id} for pretest")
        super().__init__(labtracks_mouse_id, lims_user_id, lims_session_id, *args, **kwargs)
   
    @property
    def _project(self) -> str:
        "Accessed by WithLims.project getter."
        return f'Pretest_NP{self.rig_idx}'
    
    def apply_config_to_services(self):
        super().apply_config_to_services()
        OpenEphys.folder = self.folder