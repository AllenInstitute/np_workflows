from __future__ import annotations

import abc
import functools
from typing import Any, Optional, Protocol, Union, Sequence, ClassVar, Type

from np_workflows.services.protocols import (
    Service,
    TestFailure,
    Configurable,
    Initializable,
    Testable,
    Pretestable,
    Startable,
    Verifiable,
    Stoppable,
    Finalizable,
    Validatable,
    Shutdownable,
)
from np_workflows.services import config

import np_session
import np_logging

logger = np_logging.getLogger(__name__)

class Experiment(abc.ABC):
    
    rig_idx: ClassVar[int] = config.Rig.idx
    # session: str
    # "Unique id for the session, e.g. lims ecephys session id, datetime. Used for folder name."
    services: tuple[Service, ...]
    "Devices, databases etc. Will always be called in order."
    
    config: dict[str, dict[str, Any]]
    "Top-level keys are names of Services. Each Service then has config specific to rig, session, experiment, etc."
    
    state_globals: dict[str, dict[str, Any]]
    "Storage for WSE state dictionary. Main key of interest is `external`"
    
    def apply_config_to_services(self) -> None:
        """For each service, apply every key in self.config['service'] as an attribute."""        
        def apply_config(service):
            if config := self.config['services'].get(service.__name__):
                for key, value in config.items():
                    setattr(service, key, value)
                    logger.debug(f"{self.__class__.__name__} | Set {service.__name__}.{key} = {getattr(service, key)}")
        
        for service in self.services:
            for base in service.__class__.__bases__:
                apply_config(base)
            apply_config(service)
            
    # def configure_services(self) -> None:
    #     for service in (_ for _ in self.services if isinstance(_, Configurable)):
    #         service.configure()
            
    def initialize_services(self) -> None:
        
        self.apply_config_to_services()
        self.state_globals['external']['component_status'] = {service.__name__: False for service in self.services}
        for service in self.services:
                        
            if isinstance(service, Initializable):
                while True:
                    try:
                        service.initialize()
                        
                    except Exception as exc:
                        logger.error("%s | %r", service.__name__, exc)
                        import pdb; pdb.set_trace()
                        continue
                    
                    else:
                        break
            
            if isinstance(service, Testable):
                while True:
                    try:
                        service.test()
                        
                    except TestFailure as exc:
                        try:
                            logger.error("%s | %r", service.__name__, service.exc)
                        except AttributeError:
                            logger.error("%s | %r", service.__name__, exc)
                        import pdb; pdb.set_trace()
                        continue
                    
                    except Exception as exc:
                        logger.error("%s | %r", service.__name__, exc)
                        import pdb; pdb.set_trace()
                        continue
                    
                    else:
                        break
            
            self.state_globals['external']['component_status'][service.__name__] = True
            
                
    def pretest_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Pretestable)):
            service.pretest()
            
    def start_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Startable)):
            service.start()
            if isinstance(service, Verifiable):
                service.verify()
                
    def stop_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Stoppable)):
            service.stop()
            if isinstance(service, Finalizable):
                service.finalize()
                
    def validate_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Validatable)):
            service.validate()
            
    def shutdown_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Shutdownable)):
            service.shutdown()
    
    
class WithLims(Experiment):
    "Provides lims info properties"
    
    def __init__(self,
        labtracks_mouse_id: int | str,
        lims_user_id: str,
        lims_session_id: Optional[int] = None,
    ):
        logger.debug(f"Initializing {__class__.__name__}({labtracks_mouse_id}, {lims_user_id})")
        self.mouse = labtracks_mouse_id
        self.operator = lims_user_id
        if lims_session_id is None:
            try:
                lims_session_id = self.generate_ecephys_session()
            except Exception as exc:
                logger.exception(exc)
                import pdb; pdb.set_trace()
        self.session = lims_session_id
        logger.debug(f"{__class__.__name__}.mouse = {self.mouse!r}")
        logger.debug(f"{__class__.__name__}.operator = {self.operator!r}")
        logger.debug(f"{__class__.__name__}.session = {self.session!r}")
        logger.debug(f"{__class__.__name__}.folder = {self.folder}")
        

    def generate_ecephys_session(
        self,
        labtracks_mouse_id: Optional[int | str] = None,
        lims_user_id: Optional[str] = None,
    ) -> np_session.lims.SessionInfo:
        if labtracks_mouse_id is None:
            labtracks_mouse_id = self.mouse
        if lims_user_id is None:
            lims_user_id = self.operator
        return np_session.lims.generate_ecephys_session(mouse=labtracks_mouse_id, user=lims_user_id)

    @property
    def session(self) -> np_session.lims.SessionInfo:
        if not hasattr(self, '_session'):
            self._session = self.generate_ecephys_session()
        return self._session

    @session.setter
    def session(self, value: str | np_session.lims.SessionInfo):
        if not isinstance(value, np_session.lims.SessionInfo):
            value = np_session.lims.SessionInfo(value)
        self._session = value

    @property
    def mouse(self) -> np_session.lims.MouseInfo:
        return self._mouse

    @mouse.setter
    def mouse(self, value: str | int):
        self._mouse = np_session.lims.MouseInfo(value)

    @property
    def operator(self) -> np_session.lims.UserInfo:
        return self._operator

    @operator.setter
    def operator(self, value: str):
        self._operator = np_session.lims.UserInfo(value)

    @property
    def project(self) -> str:
        "Primarily used for file manifest"
        if not hasattr(self, '_project'):
            self._project = self.mouse.project
        return self._project

    @project.setter
    def project(self, value: str):
        self._project = value

    @functools.cached_property
    def folder(self) -> str:
        return np_session.folder_from_lims_id(str(self.session))

    @functools.cached_property
    def files(self) -> dict[str, dict[str, str]]:
        "Expected manifest of files from experiment"
        return np_session.files_manifest(str(self.project), self.folder, 'D1')

