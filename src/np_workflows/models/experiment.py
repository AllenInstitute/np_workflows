import abc
import contextlib
import functools
import pathlib
import shutil
import time
from typing import Any, ClassVar, Literal, Optional, Protocol, Sequence, Type, Union

import fabric 

import np_config
import np_logging
import np_session
import np_services
import np_workflows.npxc as npxc
from np_services import (
    Service,
    TestError,
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


logger = np_logging.getLogger(__name__)


class WithLims(abc.ABC):
    
    services: tuple[Service, ...]
    "Devices, databases etc. Order will be preserved when called, but not necessarily called altogether."

    def __init__(self, 
        mouse: Optional[str | int |  np_session.LIMS2MouseInfo] = None,
        operator: Optional[str | np_session.LIMS2UserInfo] = None, 
        session: Optional[str | pathlib.Path | int | np_session.LIMS2SessionInfo] = None,
        session_type: Literal['ecephys', 'hab'] = 'ecephys',
        **kwargs
        ):
        
        # np_config.merge(self.__dict__, kwargs)
        
        if session:
            self.session = session
        elif operator and mouse:
            self.session = np_session.generate_session(mouse, operator, session_type)
        else:
            raise ValueError('Must specify either a mouse + operator, or an existing session')
    
        self.session_type = session_type
            
        self.configure_services()
        self.session.npexp_path.mkdir(parents=True, exist_ok=True)

    @property
    def platform_json(self) -> np_services.PlatformJsonWriter:
        with contextlib.suppress(AttributeError):
            return self._platform_json
        self._platform_json = np_services.PlatformJsonWriter(path=self.npexp_path)
        self._platform_json.operatorID = str(self.session.user)
        self._platform_json.mouseID = str(self.session.mouse)
        self._platform_json.sessionID = str(self.session)
        return self.platform_json
    
    @property
    def session(self) -> np_session.Session:
        return self._session
        
    @session.setter
    def session(self, value: str | np_session.Session | pathlib.Path | int | np_session.LIMS2SessionInfo):
        self._session = np_session.Session(value) if not isinstance(value, np_session.Session) else value
        logger.debug('Set experiment.session to %r', self._session)
    
    @property
    def session_type(self) -> Literal['ecephys', 'hab']:
        with contextlib.suppress(AttributeError):
            return self._session_type
        if self.session:
            if self.session.is_ecephys_session:
                return 'ecephys'
            return 'hab'
        raise AttributeError('Session has not been set')
    
    @session_type.setter
    def session_type(self, value: Literal['ecephys', 'hab']):
        if value not in ('ecephys', 'hab'):
            raise ValueError('Session type must be either "ecephys" or "hab"')
        self._session_type = value
        logger.debug('Set session_type to %r', value)
        
    @property
    def rig(self) -> np_config.Rig | None:
        "Computer hostnames and configuration for the rig we're currently on."
        with contextlib.suppress(AttributeError):
            return self._rig
        with contextlib.suppress(ValueError):
            self._rig = np_config.Rig()
            return self.rig
    
    # @property
    # def services(self) -> tuple[Service, ...]:
    #     with contextlib.suppress(AttributeError):
    #         return self._services
    #     return ()
    
    @property
    def imager(self) -> Type[np_services.Cam3d] | Type[np_services.ImageMVR]:
        if not self.rig:
            raise ValueError('Rig has not been set')
        if self.rig.idx == 0:
            return np_services.Cam3d
        return np_services.ImageMVR
    
    @property
    def config(self) -> dict[Any, Any]:
        "Top-level keys include names of Services. Each Service then has a config dict."
        with contextlib.suppress(AttributeError):
            return self._config
        if self.rig:
            self._config = self.rig.config
        return self.config
        
    def configure_services(self) -> None:
        """For each service, apply every key in self.config['service'] as an attribute."""

        def apply_config(service):
            if config := self.config["services"].get(service.__name__):
                for key, value in config.items():
                    setattr(service, key, value)
                    logger.debug(
                        f"{self.__class__.__name__} | Set {service.__name__}.{key} = {getattr(service, key)}"
                    )

        for service in self.services:
            for base in service.__class__.__bases__:
                apply_config(base)
            apply_config(service)

    def initialize_and_test_services(self) -> None:

        self.configure_services()
        for service in self.services:

            if isinstance(service, Initializable):
                service.initialize()

            if isinstance(service, Testable):
                service.test()

    def pretest_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Pretestable)):
            service.pretest()
    
    def start_recording(self, *recorders: Startable) -> None:
        if not recorders and hasattr(self, 'recorders'):
            recorders = self.recorders
        stoppables = tuple(_ for _ in recorders if isinstance(_, Stoppable))
        with np_services.stop_on_error(*stoppables):
            for recorder in recorders:
                recorder.start()
                time.sleep(2)
                if isinstance(recorder, Verifiable):
                    recorder.verify()
        self.platform_json.ExperimentStartTime = npxc.now()        
        self.platform_json.write()
    
    def stop_recording(self, *recorders: Startable) -> None:
        if not recorders and hasattr(self, 'recorders'):
            recorders = self.recorders
        for stoppable in (_ for _ in recorders if isinstance(_, Stoppable)):
            stoppable.stop()
        self.platform_json.ExperimentCompleteTime = npxc.now()        
        self.platform_json.write()
                
    def start_services(self, *services: Service) -> None:
        if not services:
            services = self.services
        for service in (_ for _ in services if isinstance(_, Startable)):
            service.start()
            if isinstance(service, Verifiable):
                service.verify()

    def stop_services(self) -> None:
        while not np_services.ScriptCamstim.is_ready_to_start():
            time.sleep(10)
        for service in (_ for _ in self.services if isinstance(_, Stoppable)):
            service.stop()
            if isinstance(service, Finalizable):
                service.finalize()

    def validate_services(self, *services: Service) -> None:
        if not services:
            services = self.services
        for service in (_ for _ in services if isinstance(_, Validatable)):
            service.validate()
            
    def finalize_services(self, *services: Service) -> None:
        if not services:
            services = self.services
        for service in (_ for _ in services if isinstance(_, Finalizable)):
            service.finalize()

    def shutdown_services(self) -> None:
        for service in (_ for _ in self.services if isinstance(_, Shutdownable)):
            service.shutdown()
            
    def copy_files(self, services: Sequence[Service], session_folder: pathlib.Path):
        """Copy files from raw data storage to session folder for all services."""
        npxc.copy_files(self.services, self.session.npexp_path)
            
    def photodoc(self, img_label: Optional[str] = None) -> pathlib.Path:
        """Capture image with `img_label` appended to filename, and return the filepath."""        
        if img_label:
            self.imager.label = img_label
        
        if isinstance(self.imager, Initializable) and not getattr(self.imager, 'initialization', None):
            self.imager.initialize()
            
        self.imager.start()
        
        if isinstance(self.imager, Finalizable):
            self.imager.finalize()
            
        if (recorder := np_services.NewScaleCoordinateRecorder) in self.services:
            if img_label:
                recorder.label = img_label
            recorder.start()
            
        return self.imager.data_files[-1]
    
class Ecephys(WithLims):
    pass
    
class Hab(WithLims):
    pass