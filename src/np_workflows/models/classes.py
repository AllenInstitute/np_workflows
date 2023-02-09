import contextlib
import datetime
import functools
import pathlib
import pickle
import typing
from typing import Any, ClassVar, Optional, Protocol, Sequence, Type, Union

import np_config
import np_logging
from np_services import (Finalizable, ImageMVR, JsonRecorder,
                         NewScaleCoordinateRecorder, NoCamstim, OpenEphys,
                         ScriptCamstim, Service, SessionCamstim, Startable,
                         Stoppable, Sync, Verifiable, VideoMVR)

from np_workflows.models.baseclasses import Experiment, WithLims

logger = np_logging.getLogger(__name__)


class NpUltra(Experiment):
        
    services: tuple[Service, ...] = (
        Sync, OpenEphys, ImageMVR, NewScaleCoordinateRecorder, NoCamstim,
    )
    "All services used in the experiment that support Service protocols."
    
    photodoc_services: tuple[Service, ...] = (
        ImageMVR, NewScaleCoordinateRecorder,
    )
    "Services called during photodoc capture."
    stimulus_services: tuple[Service, ...] = (
        NoCamstim,
    )
    
    def __init__(self, labtracks_mouse_id) -> None:    
        #TODO deepmerge config dicts 
        self.config : dict[str, dict[str, Any]]  = (
            np_config.from_zk('/projects/np_workflows/defaults/configuration')
            | np_config.from_zk(f'/projects/np_workflows/{self.__class__.__name__}/configuration')
            | np_config.from_zk(f'/rigs/NP.{self.rig_idx}')
        )
        self.session = f"{labtracks_mouse_id}_{datetime.datetime.now().strftime('%Y%m%d')}"
        OpenEphys.folder = self.session
        NoCamstim.remote_file = pathlib.Path('C:/Users/svc_neuropix/Desktop/run_blue_opto.bat')
    
    def advance_trial_idx(self, state_globals):
        if not hasattr(self, 'trial_idx'):
            self.trial_idx = 0
        else: 
            self.trial_idx += 1
        state_globals['external']['trial_idx'] = self.trial_idx
        OpenEphys.set_folder(f'{self.session}_{self.trial_idx}')
    
    @property
    def initial_services(self) -> tuple[Service, ...]:
        return tuple(set(self.services) - set(self.stimulus_services) - set(self.photodoc_services) ) 
    
    def start_recording(self):
        for service in self.initial_services:
            if isinstance(service, Startable):
                service.start()
        for service in self.initial_services:
            if isinstance(service, Verifiable):
                service.verify()
                
    def start_stimulus(self):
        for service in self.stimulus_services:
            if isinstance(service, Startable):
                service.start()
        for service in self.stimulus_services:
            if isinstance(service, Verifiable):
                service.verify()
                
    def stop_recording(self):
        for service in self.stimulus_services:
            if isinstance(service, Stoppable):
                service.stop()
        for service in self.services:
            if isinstance(service, Stoppable):
                service.stop()
                
        for service in self.stimulus_services:
            if isinstance(service, Finalizable):
                service.finalize()
        for service in self.services:
            if isinstance(service, Finalizable):
                service.finalize()
        
class Pretest(WithLims):
        
    services: tuple[Service, ...] = (
        Sync, OpenEphys, ImageMVR, VideoMVR, NewScaleCoordinateRecorder, ScriptCamstim,
    )
    "All services used in the experiment that support Service protocols."
    
    stim_services: tuple[Service, ...] = (
        NoCamstim,
    )
    
    photodoc_services: tuple[Service, ...] = (
        ImageMVR, NewScaleCoordinateRecorder,
    )
    "Services called during photodoc capture."
    
    recorder_services: tuple[Service, ...] = (
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
        
        #TODO deepmerge config dicts 
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