import abc
import contextlib
import functools
import getpass
import pathlib
import re
import shutil
import time
from typing import Any, ClassVar, Iterable, Literal, Optional, Protocol, Sequence, Type, Union

import fabric 
import ipylab

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
    
    default_session_type: Literal['ecephys', 'hab'] = 'ecephys'
    
    services: tuple[Service, ...] = ()
    "Devices, databases, etc."

    def __init__(self, 
        mouse: Optional[str | int |  np_session.LIMS2MouseInfo] = None,
        operator: Optional[str | np_session.LIMS2UserInfo] = None, 
        session: Optional[str | pathlib.Path | int | np_session.LIMS2SessionInfo] = None,
        session_type: Literal['ecephys', 'hab'] = default_session_type,
        **kwargs,
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

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.session})'
    
    @property
    def platform_json(self) -> np_session.PlatformJson:
        self.session.platform_json.update('operatorID', str(self.user))
        self.session.platform_json.update('mouseID', str(self.mouse))
        self.session.platform_json.update('sessionID', self.session.id)
        self.session.platform_json.update('rig_id', str(self.rig))
        return self.session.platform_json
    
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
    
    @property
    def mouse(self) -> np_session.Mouse:
        return self.session.mouse
    
    @property
    def user(self) -> np_session.User | None:
        return self.session.user
    
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
                        f"{self.__class__.__name__} | Configuring {service.__name__}.{key} = {getattr(service, key)}"
                    )

        for service in self.services:
            for base in service.__class__.__bases__:
                apply_config(base)
            apply_config(service)

    def initialize_and_test_services(self) -> None:

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
    
    def stop_recording_after_stim_finished(self, 
            recorders: Optional[Iterable[Startable]] = None, 
            stims: Optional[Iterable[Startable]] = None,
        ) -> None:
        """Stop recording after all stims have finished."""
        if not recorders and hasattr(self, 'recorders'):
            recorders = self.recorders
        if not stims and hasattr(self, 'stims'):
            stims = self.stims
        while not all(_.is_ready_to_start() for _ in stims):
            time.sleep(5)
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
            
    def copy_files(self) -> None:
        """Copy files from raw data storage to session folder for all services."""
        self.copy_data_files()
        self.copy_workflow_files()
        self.platform_json.fix_D1_files()
        self.copy_ephys()
    
    def copy_data_files(self) -> None:
        """Copy data files from raw data storage to session folder for all services."""
        for service in self.services:
            match service.__name__:
                case "np_services.open_ephys":
                    continue # copy ephys after other files
                case _:
                    files = None
                    with contextlib.suppress(AttributeError):
                        files = service.data_files or service.get_latest_data('*')
                        if not files:
                            continue
                        files = set(files)
                        logger.info("%s | Copying files %r", service.__name__, files)
                        for file in files:
                        renamed = None
                            if file.suffix == '.h5':
                                renamed = f'{self.session.folder}.sync'
                            elif file.suffix == '.pkl':
                                for _ in ('opto', 'main', 'mapping'):
                                    if _ in file.name:
                                        renamed = f'{self.session.folder}.{"stim" if _ == "main" else _}.pkl'
                            elif file.suffix in ('.json', '.mp4') and (cam_label := re.match('Behavior|Eye|Face',file.name)):
                                renamed = f'{self.session.folder}.{cam_label.group().lower()}{file.suffix}'
                        elif service in (np_services.Cam3d, np_services.MVR):
                            for lims_label, img_label  in {
                                    'pre_experiment_surface_image_left': '_surface-image1-left.png',
                                    'pre_experiment_surface_image_right': '_surface-image1-right.png',
                                    'brain_surface_image_left': '_surface-image2-left.png',
                                    'brain_surface_image_right': '_surface-image2-right.png',
                                    'pre_insertion_surface_image_left': '_surface-image3-left.png',
                                    'pre_insertion_surface_image_right': '_surface-image3-right.png',
                                    'post_insertion_surface_image_left': '_surface-image4-left.png',
                                    'post_insertion_surface_image_right': '_surface-image4-right.png',
                                    'post_stimulus_surface_image_left': '_surface-image5-left.png',
                                    'post_stimulus_surface_image_right': '_surface-image5-right.png',
                                    'post_experiment_surface_image_left': '_surface-image6-left.png',
                                    'post_experiment_surface_image_right': '_surface-image6-right.png',
                                }.items():
                                if lims_label in file.name:
                                    renamed = f'{self.session.folder}{img_label}{file.suffix}'
                        shutil.copy2(file, self.session.npexp_path / (renamed or file.name))
                        
    def copy_workflow_files(self) -> None:
        """Copy working directory (with ipynb, logs folder) and lock/pyproject files
        from np_notebooks root."""

        self.save_current_notebook()

        cwd = pathlib.Path('.').resolve()
        dest = self.session.npexp_path / 'exp'
        dest.mkdir(exist_ok=True, parents=True)

        shutil.copytree(cwd, dest, dirs_exist_ok=True)

        lock = cwd.parent / 'pdm.lock'
        pyproject = cwd.parent / 'pyproject.toml'

        for _ in (lock, pyproject):
            shutil.copy2(_, dest)
            
    def save_current_notebook(self) -> None:
        app = ipylab.JupyterFrontEnd()
        app.commands.execute('docmanager:save')
        
    def copy_ephys(self) -> None:
        # copy ephys       
        password = getpass.getpass(f'Enter password for svc_neuropix:')
        ssh = fabric.Connection(host=np_services.OpenEphys.host, user='svc_neuropix', connect_kwargs=dict(password=password))
        for ephys_folder in np_services.OpenEphys.data_files:

            if ephys_folder.drive.endswith("A"):
                probes = '_probeABC'
            elif ephys_folder.drive.endswith("B"):
                probes = '_probeDEF'
            else:
                probes = '_probes'
                
            with contextlib.suppress(Exception):
                with ssh:
                    ssh.run(
                    f'robocopy "{ephys_folder}" "{self.session.npexp_path / (self.session.npexp_path.name + probes)}" /j /s /xo' 
                    # /j unbuffered, /s incl non-empty subdirs, /xo exclude src files older than dest
                    ) 
                           
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
    default_session_type = 'ecephys'

class Hab(WithLims):
    default_session_type = 'hab'
