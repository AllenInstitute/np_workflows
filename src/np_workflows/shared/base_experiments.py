import abc
import configparser
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
import np_workflows.shared.npxc as npxc
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


class WithSession(abc.ABC):
    
    default_session_subclass: ClassVar[Type[np_session.Session]] = np_session.PipelineSession
    default_session_type: Literal['ephys', 'hab'] = 'ephys'
    
    services: tuple[Service, ...] = ()
    "All services. Devices, databases, etc."

    @property
    @abc.abstractmethod
    def recorders(self) -> tuple[Startable | Stoppable, ...]:
        """Services that record data. These are started and stopped as a group."""
        return NotImplemented
    
    def __init__(self, 
        mouse: Optional[str | int |  np_session.LIMS2MouseInfo] = None,
        operator: Optional[str | np_session.LIMS2UserInfo] = None, 
        session: Optional[str | pathlib.Path | int | np_session.PipelineSession] = None,
        session_type: Optional[Literal['ephys', 'hab']] = None,
        **kwargs,
        ):
        
        if session and not isinstance(session, np_session.Session):
            session = np_session.Session(session)
            logger.debug('%s | Initialized with existing session %s', self.__class__.__name__, session)
            if session_type and ((a := session_type == 'hab') != (b := session.is_hab)):
                logger.warning('session_type arg specified (%r) does not match that of supplied %r: %r', a, b, session)
        elif operator and mouse:
            logger.debug('%s | Creating new session for mouse %r, operator %r', self.__class__.__name__, mouse, operator)
            session = self.generate_session(mouse, operator, session_type or self.default_session_type)
        else:
            raise ValueError('Must specify either a mouse + operator, or an existing session')

        self.session = session
            
        self.configure_services()
        self.session.npexp_path.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.session})'
    
    @classmethod
    def generate_session(cls, *args, **kwargs):
        return cls.default_session_subclass.new(*args, **kwargs)
        
    @property
    def session(self) -> np_session.PipelineSession:
        return self._session
        
    @session.setter
    def session(self, value: str | np_session.Session | pathlib.Path | int | np_session.LIMS2SessionInfo):
        self._session = np_session.Session(value) if not isinstance(value, np_session.Session) else value
        logger.debug('Set experiment.session to %r', self._session)
    
    @property
    def session_type(self) -> Literal['ephys', 'hab']:
        with contextlib.suppress(AttributeError):
            return self._session_type
        if self.session:
            if self.session.is_hab:
                return 'hab'
            return 'ephys'
        raise AttributeError('Session has not been set')
    
    @session_type.setter
    def session_type(self, value: Literal['ephys', 'hab']):
        if value not in ('ephys', 'hab'):
            raise ValueError(f'Session type must be either "ephys" or "hab": got {value!r}')
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
        if isinstance(self.session.mouse, str | int):
            return np_session.Mouse(self.session.mouse)
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
        if self.session_type != 'hab':
            self.copy_ephys()
    
    @abc.abstractmethod
    def copy_data_files(self) -> None:
        """Copy files from raw data storage to session folder for all services."""
        return NotImplemented
    
    @abc.abstractmethod
    def copy_ephys(self) -> None:
        """Copy ephys data from Acq to session folder."""
        return NotImplemented
    
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
        
        
class PipelineExperiment(WithSession):
    @property
    def platform_json(self) -> np_session.PlatformJson:
        self.session.platform_json.update('rig_id', str(self.rig))
        return self.session.platform_json
    
    def start_recording(self, *recorders: Startable) -> None:
        super().start_recording(*recorders)
        self.platform_json.ExperimentStartTime = npxc.now()        
        self.platform_json.write()


    def stop_recording_after_stim_finished(self, 
            recorders: Optional[Iterable[Startable]] = None, 
            stims: Optional[Iterable[Startable]] = None,
        ) -> None:
        """Stop recording after all stims have finished."""
        super().stop_recording_after_stim_finished(recorders, stims)
        self.platform_json.ExperimentCompleteTime = npxc.now()        
        self.platform_json.write()
                                   
    def rename_split_ephys_folders(self) -> None:
        "Add `_probeABC` or `_probeDEF` to ephys folders recorded on two drives."
        folders = np_services.OpenEphys.data_files
        if not folders:
            logger.info('Renaming: no ephys folders have been recorded')
        renamed_folders = []
        for name in set(_.name for _ in folders):
            if '_probeABC' in name or '_probeDEF' in name:
                logger.debug(f'Renaming: {name} already has probe letter suffix - aborted')
                continue
            if length := len(split_folders := [_ for _ in folders if _.name == name]) != 2:
                logger.info(f'Renaming: {length} folders found for {name}, expected 2 - aborted')
                continue
            logger.debug('Renaming split ephys folders %r', split_folders)
            for folder, probe_letters in zip(sorted(split_folders, key=lambda x: x.as_posix()), ('ABC', 'DEF')):
                renamed = folder.replace(folder.with_name(f'{name}_probe{probe_letters}'))
                renamed_folders.append(renamed)
            logger.info('Renamed split ephys folders %r', split_folders)
        np_services.OpenEphys.data_files = renamed_folders
        
    def copy_data_files(self) -> None:
        """Copy data files from raw data storage to session folder for all services."""
        for service in self.services:
            match service.__name__:
                case "np_services.open_ephys":
                    continue # copy ephys after other files
                case _:
                    files = None
                    with contextlib.suppress(AttributeError):
                        files = service.data_files
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
                        elif service in (np_services.NewScaleCoordinateRecorder, ):
                            renamed = f'{self.session.folder}.motor-locs.csv'
                        elif service in (np_services.Cam3d, np_services.MVR):
                            for lims_label, img_label  in {
                                    'pre_experiment_surface_image_left': '_surface-image1-left',
                                    'pre_experiment_surface_image_right': '_surface-image1-right',
                                    'brain_surface_image_left': '_surface-image2-left',
                                    'brain_surface_image_right': '_surface-image2-right',
                                    'pre_insertion_surface_image_left': '_surface-image3-left',
                                    'pre_insertion_surface_image_right': '_surface-image3-right',
                                    'post_insertion_surface_image_left': '_surface-image4-left',
                                    'post_insertion_surface_image_right': '_surface-image4-right',
                                    'post_stimulus_surface_image_left': '_surface-image5-left',
                                    'post_stimulus_surface_image_right': '_surface-image5-right',
                                    'post_experiment_surface_image_left': '_surface-image6-left',
                                    'post_experiment_surface_image_right': '_surface-image6-right',
                                }.items():
                                if lims_label in file.name:
                                    renamed = f'{self.session.folder}{img_label}{file.suffix}'
                        shutil.copy2(file, self.session.npexp_path / (renamed or file.name))
                        
    def copy_ephys(self) -> None:
        # copy ephys       
        self.rename_split_ephys_folders()
        password = np_config.fetch('/logins')['svc_neuropix']['password']
        ssh = fabric.Connection(host=np_services.OpenEphys.host, user='svc_neuropix', connect_kwargs=dict(password=password))
        for ephys_folder in np_services.OpenEphys.data_files:
            with contextlib.suppress(Exception):
                with ssh:
                    ssh.run(
                    f'robocopy "{ephys_folder}" "{self.session.npexp_path / ephys_folder.name}" /j /s /xo' 
                    # /j unbuffered, /s incl non-empty subdirs, /xo exclude src files older than dest
                    )
    
    @functools.cached_property
    def system_camstim_params(self) -> dict[str, Any]:
        """Try to load defaults from camstim config file on the Stim computer.
        
        May encounter permission error if not running as svc_neuropix.
        """
        with contextlib.suppress(OSError):
            parser = configparser.RawConfigParser()
            parser.read(
                (self.rig.paths["Camstim"].parent / "config" / "stim.cfg").as_posix()
            )

            camstim_default_config = {}
            for section in parser.sections():
                camstim_default_config[section] = {}
                for k, v in parser[section].items():
                    try:
                        value = eval(
                            v
                        )  # this removes comments in config and converts values to expected datatype
                    except:
                        continue
                    else:
                        camstim_default_config[section][k] = value
            return camstim_default_config
        logger.warning("Could not load camstim defaults from config file on Stim computer.")
        return {}

class PipelineEphys(PipelineExperiment):
    default_session_type = 'ephys'


class PipelineHab(PipelineExperiment):
    default_session_type = 'hab'
