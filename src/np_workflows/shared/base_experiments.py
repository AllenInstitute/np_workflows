import abc
import configparser
import contextlib
import enum
import functools
import pathlib
import re
import shutil
import time
from typing import Any, ClassVar, Iterable, Literal, Optional, Protocol, Sequence, Type

import fabric
import invoke
import ipylab
import np_config
import np_logging
import np_services
import np_session
import upath
from np_services import (
    Finalizable,
    Initializable,
    Pretestable,
    Service,
    Shutdownable,
    Startable,
    Stoppable,
    Testable,
    Validatable,
    Verifiable,
)

import np_workflows.shared.npxc as npxc

logger = np_logging.getLogger(__name__)

class WithSessionInfo(Protocol):
    @property
    def session(self) -> np_session.Session: ...
    @property
    def mouse(self) -> np_session.Mouse: ...
    @property
    def user(self) -> np_session.User: ...
    @property
    def rig(self) -> np_config.Rig: ...
    

class WithSession(abc.ABC):
    
    default_session_subclass: ClassVar[Type[np_session.Session]] = np_session.PipelineSession
    default_session_type: Literal['ephys', 'hab'] = 'ephys'
    
    services: tuple[Service, ...] = ()
    "All services. Devices, databases, etc."
    
    workflow: enum.Enum = enum.Enum('BaseWithSessionWorkflow', ('BASECLASS')).BASECLASS # type: ignore
    """Enum for workflow type, e.g. PRETEST, HAB_AUD, HAB_VIS, EPHYS_ etc."""

    def log(self, message: str, weblog_name: Optional[str] = None) -> None:
        logger.info(message)
        if not weblog_name:
            weblog_name = self.workflow.name
        with contextlib.suppress(AttributeError):
            np_logging.web(f'{weblog_name.lower()}_{self.mouse}').info(message)
    
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
        elif not session:
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
            recorders: Optional[Iterable[Stoppable]] = None, 
            stims: Optional[Iterable[Stoppable]] = None,
        ) -> None:
        """Stop recording after all stims have finished.
        
        - object's `.recorders` attr used by default, stopped in reverse order.
        - stims will be awaited
        """
        if not recorders and hasattr(self, 'recorders'):
            recorders = reversed(self.recorders)
        if not stims and hasattr(self, 'stims'):
            stims = self.stims
        while not all(_.is_ready_to_start() for _ in stims):
            time.sleep(5)
        for stoppable in (_ for _ in recorders if isinstance(_, Stoppable)):
            stoppable.stop()
            if 'mvr' in stoppable.__class__.__name__.lower():
                time.sleep(v := 3)
                logger.info(f'Waiting additional {v}s for MVR to finish writing...')
                    
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
        # TODO use the following to export to html (shows input to widgets and
        # output of cells)
        #! currently can't be run automatically as save as path dialog opens 
        # app.commands.execute('notebook:export-to-format', {
        #     'format': 'html',
        #     # 'download': 'false',
        #     # 'path': 'c:/users/svc_neuropix/documents/github/np_notebooks/task_trained_network/ttn_pilot.html',
        # })   
            
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
            recorders: Optional[Iterable[Stoppable]] = None, 
            stims: Optional[Iterable[Stoppable]] = None,
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
                        elif file.suffix in ('.json', '.mp4') and (cam_label := re.match('BEH|EYE|FACE',file.name)):
                            file_label = {'BEH':'behavior', 'EYE':'eye', 'FACE':'face'}
                            renamed = f'{self.session.folder}.{file_label[cam_label.group()]}{file.suffix}'
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

class PipelineEphys(PipelineExperiment):
    default_session_type = 'ephys'

class PipelineHab(PipelineExperiment):
    default_session_type = 'hab'


class DynamicRoutingExperiment(WithSession):
    
    default_session_subclass: ClassVar[Type[np_session.Session]]
    
    
    use_github: bool = True
    
    class Workflow(enum.Enum):
        """Enum for the different sessions available. 
        
        Used in the workflow to determine branches (e.g. HAB workflow should
        skip set up and use of OpenEphys).
        
        Can also be used to switch different task names, scripts, etc. 
        
        - names are used to set the experiment subclass
        - values must be unique! 
        """
        PRETEST = "test EPHYS"
        HAB = "EPHYS minus probes"
        EPHYS = "opto in task optional"
        OPTO = "opto in task, no ephys"
    
    workflow: Workflow
    
    @property
    def preset_task_names(self) -> tuple[str, ...]:
        return tuple(np_config.fetch('/projects/dynamicrouting')['preset_task_names'])
        
    @property
    def commit_hash(self) -> str:
        if hasattr(self, '_commit_hash'):
            return self._commit_hash
        self._commit_hash = self.config['dynamicrouting_task_script']['commit_hash']
        return self.commit_hash
    
    @commit_hash.setter
    def commit_hash(self, value: str):
        self._commit_hash = value
        
    @property
    def github_url(self) -> str:
        if hasattr(self, '_github_url'):
            return self._github_url
        self._github_url = self.config['dynamicrouting_task_script']['url']
        return self.github_url
    
    @github_url.setter
    def github_url(self, value: str):
        self._github_url = value
    
    @property
    def base_url(self) -> upath.UPath:
        return upath.UPath(self.github_url) / self.commit_hash
    
    @property
    def base_path(self) -> pathlib.Path:
        return pathlib.Path('//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/')
    
    @property
    def is_pretest(self) -> bool:
        return 'PRETEST' in self.workflow.name
    
    @property
    def is_hab(self) -> bool:
        return 'HAB' in self.workflow.name
    
    @property
    def is_opto(self) -> bool:
        """Opto will run during behavior task trials - independent of `is_ephys`."""
        return 'opto' in self.task_name
    
    @property
    def is_ephys(self) -> bool:
        return 'EPHYS' in self.workflow.name
    
    @property
    def task_name(self) -> str:
        """For sending to runTask.py and controlling implementation details of the task."""
        if hasattr(self, '_task_name'): 
            return self._task_name 
        return ""

    @task_name.setter
    def task_name(self, task_name: str) -> None:
        self._task_name = task_name
        if task_name not in self.preset_task_names:
            print(f"{task_name = !r} doesn't correspond to a preset value, but the attribute is updated anyway!")
        else:
            print(f"Updated {self.__class__.__name__}.{task_name = !r}")

    services = (
        np_services.Sync,
        np_services.VideoMVR,
        np_services.ImageMVR,
        np_services.OpenEphys, 
        np_services.NewScaleCoordinateRecorder,
        np_services.ScriptCamstim, 
        np_services.MouseDirector,
        )
    
    stims = (np_services.ScriptCamstim,)
    
    @property
    def recorders(self) -> tuple[Service, ...]:
        """Services to be started before stimuli run, and stopped after. Session-dependent."""
        if self.is_hab:
            return (np_services.Sync, np_services.VideoMVR)
        return (np_services.Sync, np_services.VideoMVR, np_services.OpenEphys)
    
    @property
    def hdf5_dir(self) -> pathlib.Path:
        return self.base_path / 'Data' /  str(self.mouse)
    
    @property
    def task_script_base(self) -> upath.UPath:
        return self.base_url if self.use_github else upath.UPath(self.base_path)
    
    @property
    def task_params(self) -> dict[str, str | bool]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'DynamicRouting1.py',
                taskVersion = self.task_name,
                saveSoundArray = True,
        )
        
    @property
    def spontaneous_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'TaskControl.py',
                taskVersion = 'spontaneous',
        )
        
    @property
    def spontaneous_rewards_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'TaskControl.py',
                taskVersion = 'spontaneous rewards',
                rewardSound = "device",
        )
    
    def get_latest_optogui_txt(self, opto_or_optotagging: Literal['opto', 'optotagging']) -> pathlib.Path:
        dirname = dict(opto='optoParams', optotagging='optotagging')[opto_or_optotagging]
        file_prefix = dirname
        
        rig = str(self.rig).replace('.', '')
        locs_root = self.base_path / 'OptoGui' / f'{dirname}'
        available_locs = sorted(tuple(locs_root.glob(f"{file_prefix}_{self.mouse.id}_{rig}_*")), reverse=True)
        if not available_locs:
            raise FileNotFoundError(f"No optotagging locs found for {self.mouse}/{rig} - have you run OptoGui?")
        return available_locs[0]
        
        
    @property
    def optotagging_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'OptoTagging.py',
                optoTaggingLocs = self.get_latest_optogui_txt('optotagging').as_posix(),
        )

    @property
    def opto_params(self) -> dict[str, str | bool]:
        """Opto params are handled by runTask.py and don't need to be passed from
        here. Just check they exist on disk here.
        """
        _ = self.get_latest_optogui_txt('opto') # raises FileNotFoundError if not found
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'DynamicRouting1.py',
                saveSoundArray = True,
            )

    @property
    def mapping_params(self) -> dict[str, str | bool]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = str(self.mouse),
                taskScript = 'RFMapping.py',
                saveSoundArray = True,
            )

    @property
    def sound_test_params(self) -> dict[str, str]:
        """For sending to runTask.py"""
        return dict(
                rigName = str(self.rig).replace('.',''),
                subjectName = 'sound',
                taskScript = 'TaskControl.py',
                taskVersion = 'sound test',
        )
        
    def get_github_file_content(self, address: str) -> str:
        import requests
        response = requests.get(address)
        if response.status_code not in (200, ):
            response.raise_for_status()
        return response.content.decode("utf-8")
    
    @property
    def camstim_script(self) -> upath.UPath:
        return self.task_script_base / 'runTask.py'
    
    def run_script(self, stim: Literal['sound_test', 'mapping', 'task', 'opto', 'optotagging', 'spontaneous', 'spontaneous_rewards']) -> None:
        
        params = getattr(self, f'{stim.replace(" ", "_")}_params')
        
        # add mouse and user info for MPE
        params['mouse_id'] = str(self.mouse.id)
        params['user_id'] = self.user.id if self.user else 'ben.hardcastle'
        
        script: str = params['taskScript']
        params['taskScript'] = (self.task_script_base / script).as_posix()
        
        if self.is_pretest:
            params['maxFrames'] = 60 * 15
            params['maxTrials'] = 3
        
        if self.use_github:
        
            params['GHTaskScriptParams'] =  {
                'taskScript': params['taskScript'],
                'taskControl': (self.task_script_base / 'TaskControl.py').as_posix(),
                'taskUtils': (self.task_script_base / 'TaskUtils.py').as_posix(),
                }
            params['task_script_commit_hash'] = self.commit_hash

            np_services.ScriptCamstim.script = self.camstim_script.read_text()
        else:
            np_services.ScriptCamstim.script = self.camstim_script.as_posix()
        
        np_services.ScriptCamstim.params = params
        
        self.update_state()
        self.log(f"{stim} started")

        np_services.ScriptCamstim.start()

        while not np_services.ScriptCamstim.is_ready_to_start():
            time.sleep(1)
            
        self.log(f"{stim} complete")

        np_services.ScriptCamstim.finalize()
        
    run_mapping = functools.partialmethod(run_script, 'mapping')
    run_sound_test = functools.partialmethod(run_script, 'sound_test')
    run_task = functools.partialmethod(run_script, 'task')
    run_opto = functools.partialmethod(run_script, 'opto') # if opto params are handled by runTask then this is the same as run_task
    run_optotagging = functools.partialmethod(run_script, 'optotagging')
    run_spontaneous = functools.partialmethod(run_script, 'spontaneous')
    run_spontaneous_rewards = functools.partialmethod(run_script, 'spontaneous_rewards')
    
        
    def update_state(self) -> None:
        "Persist useful but non-essential info."
        self.mouse.state['last_session'] = self.session.id
        self.mouse.state['last_workflow'] = str(self.workflow.name)
        self.mouse.state['last_task'] = str(self.task_name)
        
    def initialize_and_test_services(self) -> None:
        """Configure, initialize (ie. reset), then test all services."""
        
        np_services.ScriptCamstim.script = '//allen/programs/mindscope/workgroups/dynamicrouting/DynamicRoutingTask/runTask.py'
        np_services.ScriptCamstim.data_root = self.hdf5_dir

        np_services.MouseDirector.user = self.user.id
        np_services.MouseDirector.mouse = self.mouse.id

        np_services.OpenEphys.folder = self.session.folder

        np_services.NewScaleCoordinateRecorder.log_root = self.session.npexp_path

        self.configure_services()

        super().initialize_and_test_services()

    def copy_ephys(self) -> None:
        # copy ephys       
        password = np_config.fetch('/logins')['svc_neuropix']['password']
        ssh = fabric.Connection(host=np_services.OpenEphys.host, user='svc_neuropix', connect_kwargs=dict(password=password))
        for ephys_folder in np_services.OpenEphys.data_files:
            if isinstance(self.session, np_session.TempletonPilotSession):
                ephys_folder = next(ephys_folder.glob('Record Node*'))
            with ssh, contextlib.suppress(invoke.UnexpectedExit):
                ssh.run(
                f'robocopy "{ephys_folder}" "{self.session.npexp_path / ephys_folder.name}" /j /s /xo' 
                # /j unbuffered, /s incl non-empty subdirs, /xo exclude src files older than dest
                )
            

    def copy_data_files(self) -> None:
        """Copy files from raw data storage to session folder for all services
        except Open Ephys."""
        
        # copy vimba files:
        for file in pathlib.Path(
            np_config.local_to_unc(self.rig.mon, np_services.config_from_zk()['ImageVimba']['data'])
        ).glob(f'{self.session.npexp_path.name}*'):
            shutil.copy2(file, self.session.npexp_path)
            npxc.validate_or_overwrite(self.session.npexp_path / file.name, file)
            print(file)
            continue

        for service in self.services:
            match service.__name__:
                case "ScriptCamstim" | "SessionCamstim":
                    files = tuple(_ for _ in self.hdf5_dir.glob('*') if _.stat().st_ctime > self.stims[0].initialization)
                case "np_services.open_ephys":
                    continue # copy ephys after other files
                case "NewScaleCoordinateRecorder":
                    files = tuple(service.data_root.glob('*')) + tuple(self.rig.paths['NewScaleCoordinateRecorder'].glob('*'))
                case _:
                    files: Iterable[pathlib.Path] = service.data_files or service.get_latest_data('*')
            if not files:
                continue
            files = set(files)
            print(files)
            for file in files:
                shutil.copy2(file, self.session.npexp_path)
                npxc.validate_or_overwrite(self.session.npexp_path / file.name, file)
    
    #TODO move this to a dedicated np_service class instead of using ScriptCamstim
    def run_stim_desktop_theme_script(self, selection: str) -> None:     
        np_services.ScriptCamstim.script = '//allen/programs/mindscope/workgroups/dynamicrouting/ben/change_desktop.py'
        np_services.ScriptCamstim.params = {'selection': selection}
        np_services.ScriptCamstim.start()
        while not np_services.ScriptCamstim.is_ready_to_start():
            time.sleep(0.1)

    set_grey_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'grey')
    set_dark_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'dark')
    reset_desktop_on_stim = functools.partialmethod(run_stim_desktop_theme_script, 'reset')
        
