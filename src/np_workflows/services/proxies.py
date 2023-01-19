"""
Proxy classes for interacting with devices via zro/zmq.

Proxy class names must match the name of the proxy key in the config dict.
"""
from __future__ import annotations

import abc
import contextlib
import csv
import datetime
import json  # loading config from Sync proxy will instantiate datetime objects
import logging
import pathlib
import re
import time
from typing import Any, ClassVar, Literal, Mapping, Optional, Sequence

import np_logging
import np_config
import fabric 

from .config import Rig
from . import zro
from . import mvr
from . import utils
from .protocols import *

# TODO start proxies remotely if not connected

logger = np_logging.getLogger(__name__)

CONFIG = utils.config_from_zk()

ProxyState = tuple[Literal['', 'READY', 'BUSY'], str]

class Proxy(abc.ABC):
    
    # req proxy config - hardcode or overload ensure_config() 
    host: ClassVar[str]
    port: ClassVar[int]
    timeout: ClassVar[float]
    serialization: ClassVar[Literal['json', 'pickle']]
    
    # if device records:
    gb_per_hr: ClassVar[int | float]
    min_rec_hr: ClassVar[int | float]
    pretest_duration_sec: ClassVar[int | float]
    
    # for resulting data, if device records:
    data_root: ClassVar[Optional[pathlib.Path]] = None
    data_files: ClassVar[Optional[Sequence[pathlib.Path]]] = None
    
    # info
    exc: ClassVar[Optional[Exception]] = None
    
    @classmethod
    def ensure_config(cls) -> None:
        """Updates any missing parameters for class proxy. 
        
        Is called in `get_proxy()` so any time we need the proxy, we have a
        correct config, without remembering to run `initialize()` or some such.
        """
        config = CONFIG.get(__class__.__name__, {}) # class where this function is defined 
        config.update(**CONFIG.get(cls.__name__, {})) # the calling class, if different

        # for proxy (reqd):
        if not hasattr(cls, 'host'):
            cls.host = config['host']
        if not hasattr(cls,'port'):
            cls.port = int(config['port'])
        if not hasattr(cls, 'timeout'):
            cls.timeout = float(config.get('timeout', 10.0))
        if not hasattr(cls, 'serialization'):    
            cls.serialization = config.get('serialization', 'json')
        
        # for pretest (reqd, not used if device doesn't record)
        if not hasattr(cls, 'pretest_duration_sec'):
            cls.pretest_duration_sec = config.get('pretest_duration_sec', 5)
        if not hasattr(cls, 'gb_per_hr'):
            cls.gb_per_hr = config.get('gb_per_hr', 2.0)
        if not hasattr(cls, 'min_rec_hr'):
            cls.min_rec_hr = config.get('min_rec_hr', 3.0)
            
        # for resulting data (optional):
        if not cls.data_root or cls.host not in cls.data_root.parts:
            relative_path = config.get('data', None)
            if relative_path:
                root = pathlib.Path(f"//{cls.host}/{relative_path}")
                try:
                    _ = root.exists()
                except OSError as exc:
                    cls.exc = exc
                    logger.exception("Error accessing %s data path: %s", cls.__name__, root)
                    raise FileNotFoundError(f"{cls.__name__} data path is not accessible: {root}") from exc
                else:
                    cls.data_root = root
        if hasattr(cls, 'data_root'):
            cls.data_root.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def initialize(cls) -> None:
        with contextlib.suppress(AttributeError):
            del cls.proxy
        cls.proxy = cls.get_proxy()
        cls.test()
        if isinstance(cls, Startable) and not cls.is_ready_to_start():
            if isinstance(cls, Finalizable):
                cls.finalize()
            if not cls.is_ready_to_start():
                logger.warning("%s not ready to start: %s", cls.__name__, cls.get_state())
                return
        if cls.data_root:
            cls.data_files = []
        cls.initialization = time.time()
        logger.info("%s initialized: ready for use", cls.__name__)

    @classmethod                      
    def test(cls) -> None:
        "Quickly verify service is working and ready for use, or raise `TestFailure`."
        logger.debug('Testing %s proxy', cls.__name__)
        if not cls.is_connected():
            raise TestFailure(f"{cls.__name__} not connected to {cls.host}:{cls.port}") from cls.exc
        logger.debug('%s proxy connection to %s:%s confirmed', cls.__name__, cls.host, cls.port)
        gb = cls.get_required_disk_gb()
        if not cls.is_disk_space_ok():
            raise TestFailure(f"{cls.__name__} free disk space on {cls.data_root.drive} doesn't meet minimum of {gb} GB") from cls.exc
        logger.debug('%s tested successfully', cls.__name__)     
    
    @classmethod
    def get_proxy(cls) -> zro.DeviceProxy:
        "Return a proxy to the service without re-creating unnecessarily."
        with contextlib.suppress(AttributeError):
            return cls.proxy
        cls.ensure_config()
        logger.debug('Creating %s proxy to %s:%s', cls.__name__, cls.host, cls.port)
        cls.proxy = zro.DeviceProxy(cls.host, cls.port, cls.timeout, cls.serialization)
        return cls.get_proxy()
               
    @classmethod
    def get_state(cls) -> ProxyState | dict:
        "Dict may be deprecated: is no longer returned by Sync or Camstim proxies."
        return cls.get_proxy().get_state()
    
    @classmethod
    def get_latest_data(cls: Recorder, glob:Optional[str]=None, subfolders:str='') -> list[pathlib.Path] | None:
        cls.ensure_config()
        if not cls.data_root:
            return None
        if subfolders == '/': # can alter path to drive root 
            subfolders = ''
        if not glob:
            glob = f'*{cls.raw_suffix}' if hasattr(cls, 'raw_suffix') else '*'
        if not hasattr(cls, 'latest_start'):
            return [max(utils.get_files_created_between(cls.data_root / subfolders, glob), key=lambda x: x.stat().st_ctime)]
        return utils.get_files_created_between(cls.data_root / subfolders, glob, cls.latest_start)
         
    @classmethod
    def get_required_disk_gb(cls) -> float:
        "Return the minimum disk space required prior to start (to .1 GB). Returns `0.0` if service generates no data."
        cls.ensure_config()
        if not isinstance(cls, Startable):
            return 0.0
        return round(cls.min_rec_hr * cls.gb_per_hr, 1)
    
    @classmethod
    def is_disk_space_ok(cls) -> bool:
        required = cls.get_required_disk_gb()
        if required == 0.0:
            return True
        try:
            free = utils.free_gb(cls.data_root)
        except FileNotFoundError as exc:
            cls.exc = exc
            logger.exception(f"{cls.__name__} data path not accessible: {cls.data_root}")
            return False
        else:
            logger.debug('%s free disk space on %s: %s GB', cls.__name__, cls.data_root.drive, free)
            return free > required
            
    @classmethod
    def is_connected(cls) -> bool:
        if not utils.is_online(cls.host):
            cls.exc = ConnectionError(f"No response from {cls.host}: may be offline or unreachable")
            return False
        try:
            _ = cls.get_proxy().uptime
        except zro.ZroError as exc:
            cls.exc = exc
            logger.exception(f"{cls.__name__} proxy connection to {cls.host}:{cls.port} failed")
            return False
        try:
            _ = cls.get_state()
        except zro.ZroError as exc:
            cls.exc = exc
            logger.exception(f"{cls.__name__} proxy connection to {cls.host}:{cls.port} failed")
            return False
        return True


class CamstimSyncShared(Proxy):
    
    started_state: ClassVar[Sequence[str]]
    
    @classmethod
    def is_ready_to_start(cls) -> bool:
        if cls.is_started():
            return False
        state = cls.get_state()
        if isinstance(state, Mapping) and state.get('message', '') == 'READY':
            return True
        if isinstance(state, Sequence) and 'READY' in state:
            return True
        return False
        
    @classmethod
    def is_started(cls) -> bool:
        return len(state := cls.get_state()) and all(msg in state for msg in cls.started_state)
        
    @classmethod
    def start(cls) -> None:
        logger.debug("Starting %s", cls.__name__)
        if cls.is_started():
            logger.warning("%s already started - should be stopped manually", cls.__name__)
            return
            # otherwise, Sync - for example - would stop current recording and start another
        if not cls.is_ready_to_start():
            logger.error("%s not ready to start: %s", cls.__name__, cls.get_state())
            raise AssertionError(f"{cls.__name__} not ready to start: {cls.get_state()}")
        cls.latest_start = time.time()
        cls.get_proxy().start()
    
    @classmethod
    def pretest(cls) -> None:
        "Test all critical functions"
        with utils.debug_logging():
            logger.debug('Starting %s pretest', cls.__name__)
            cls.initialize() # calls test()
            with utils.stop_on_error(cls):
                cls.start()
                time.sleep(1)
                cls.verify()
                time.sleep(cls.pretest_duration_sec)
                # stop() called by context manager at exit, regardless
            cls.finalize()
            cls.validate()
        logger.info("%s pretest complete", cls.__name__)
                       
    @classmethod
    def verify(cls) -> None:
        "Assert latest data file is currently increasing in size, or raise AssertionError."
        if not cls.is_started():
            logger.warning("Cannot verify %s if not started: %s", cls.__name__, cls.get_state())
            return
        if cls.data_root and not utils.is_file_growing(cls.get_latest_data()[-1]):
            raise AssertionError(f"{cls.__name__} latest data file is not increasing in size: {cls.get_latest_data()[-1]}")
        logger.debug("%s latest data file is increasing in size", cls.__name__)
    
    @classmethod
    def stop(cls) -> None:
        logger.debug("Stopping %s", cls.__name__)
        cls.get_proxy().stop()
    
    # --- End of possible Camstim/Sync shared methods ---
 
    # --- Sync-specific methods ---
class Sync(CamstimSyncShared):
    host = Rig.Sync.host
    started_state = ("BUSY", "RECORDING")
    raw_suffix: ClassVar[int | float] = '.sync'
     
    @classmethod
    def ensure_config(cls) -> None:
        """Updates any missing parameters for class proxy. 
        
        Is called in `get_proxy()` so any time we need the proxy, we have a
        correct config, without remembering to run `initialize()` or some such.
        """
        config = CONFIG.get(__class__.__name__, {}) # class where this function is defined 
        config.update(**CONFIG.get(cls.__name__, {})) # the calling class, if different

        # for proxy (reqd):
        if not hasattr(cls, 'host'):
            cls.host = config['host']
        if not hasattr(cls,'port'):
            cls.port = int(config['port'])
        if not hasattr(cls, 'timeout'):
            cls.timeout = float(config.get('timeout', 10.0))
        if not hasattr(cls, 'serialization'):    
            cls.serialization = config.get('serialization', 'json')
        
        # for pretest (reqd, not used if device doesn't record)
        if not hasattr(cls, 'pretest_duration_sec'):
            cls.pretest_duration_sec = config.get('pretest_duration_sec', 5)
        if not hasattr(cls, 'gb_per_hr'):
            cls.gb_per_hr = config.get('gb_per_hr', 2.0)
        if not hasattr(cls, 'min_rec_hr'):
            cls.min_rec_hr = config.get('min_rec_hr', 3.0)
            
        # for resulting data (optional):
        if not cls.data_root or cls.host not in cls.data_root.parts:
            relative_path = config.get('data', None)
            if relative_path:
                root = pathlib.Path(f"//{cls.host}/{relative_path}")
                try:
                    _ = root.exists()
                except OSError as exc:
                    cls.exc = exc
                    logger.exception("Error accessing %s data path: %s", cls.__name__, root)
                    raise FileNotFoundError(f"{cls.__name__} data path is not accessible: {root}") from exc
                else:
                    cls.data_root = root
        if hasattr(cls, 'data_root'):
            cls.data_root.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def finalize(cls) -> None:
        logger.debug('Finalizing %s', cls.__name__)
        if cls.is_started():
            cls.stop()
        while not cls.is_ready_to_start():
            logger.debug("Waiting for %s to finish processing", cls.__name__)
            time.sleep(1) # TODO add backoff module
        if not hasattr(cls,'data_files'):
            cls.data_files = []
        cls.data_files.extend(new := cls.get_latest_data('*.h5'))
        logger.debug("%s processing finished: %s", cls.__name__, [_.name for _ in new])
        
    @classmethod
    def shutdown(cls) -> None:
        logger.debug("Shutting down %s", cls.__name__)
        cls.stop()
        try:
            del cls.proxy
        except Exception as exc:
            logger.debug("Failed to delete %s proxy: %s", cls.__name__, exc)
            cls.exc = exc        
            
    @classmethod
    def get_config(cls) -> dict[str, Any | datetime.datetime]:
        "Sync config, including `line_labels` and `frequency`"
        if cls.serialization in ("json", "j"):
            return eval(cls.get_proxy().config)
        if cls.serialization in ("pickle", "pkl", "p"):
            return cls.get_proxy().config
    
    @classmethod
    def validate(cls, data: Optional[pathlib.Path] = None) -> None:
        "Check that data file is valid, or raise AssertionError."
        logger.debug('Validating %s data', cls.__name__)
        if not data and bool(files := cls.get_latest_data('*.h5')):
            data = files[-1]
            logger.debug('No data file provided: validating most-recent data in %s: %s', cls.data_root, data.name)
            if cls.is_started():
                logger.warning(f"Attempted to validate current data file while recording")
                return
            elif not cls.is_ready_to_start():
                cls.finalize()
        try:
            import h5py
        except ImportError:
            logger.warning('h5py not installed: cannot open Sync data')
            cls.min_validation(data)
        else:
            cls.full_validation(data)
    
    @classmethod
    def full_validation(cls, data: pathlib.Path) -> None:
        line_labels: dict = cls.get_config()['line_labels']
        # TODO 
        pass
        
    @classmethod
    def min_validation(cls, data: pathlib.Path) -> None:
        if data.stat().st_size == 0:
            raise AssertionError(f"Empty file: {data}")
        if data.suffix != '.h5':
            raise FileNotFoundError(f"Expected .sync to be converted to .h5 immediately after recording stopped: {data}")
        logger.debug('%s minimal validation passed for %s', cls.__name__, data.name)

    
class Camstim(CamstimSyncShared):
    
    host = Rig.Stim.host
    started_state = ("BUSY", "Script in progress.")
    
    @classmethod
    def get_config(cls) -> dict[str, Any]:
        return cls.get_proxy().config
    
    @classmethod
    def ensure_config(cls) -> None:
        """Updates any missing parameters for class proxy. 
        
        Is called in `get_proxy()` so any time we need the proxy, we have a
        correct config, without remembering to run `initialize()` or some such.
        """
        config = CONFIG.get(__class__.__name__, {}) # class where this function is defined 
        config.update(**CONFIG.get(cls.__name__, {})) # the calling class, if different

        # for proxy (reqd):
        if not hasattr(cls, 'host'):
            cls.host = config['host']
        if not hasattr(cls,'port'):
            cls.port = int(config['port'])
        if not hasattr(cls, 'timeout'):
            cls.timeout = float(config.get('timeout', 10.0))
        if not hasattr(cls, 'serialization'):    
            cls.serialization = config.get('serialization', 'json')
        
        # for pretest (reqd, not used if device doesn't record)
        if not hasattr(cls, 'pretest_duration_sec'):
            cls.pretest_duration_sec = config.get('pretest_duration_sec', 5)
        if not hasattr(cls, 'gb_per_hr'):
            cls.gb_per_hr = config.get('gb_per_hr', 2.0)
        if not hasattr(cls, 'min_rec_hr'):
            cls.min_rec_hr = config.get('min_rec_hr', 3.0)
            
        # for resulting data (optional):
        if not cls.data_root or cls.host not in cls.data_root.parts:
            relative_path = config.get('data', None)
            if relative_path:
                root = pathlib.Path(f"//{cls.host}/{relative_path}")
                try:
                    _ = root.exists()
                except OSError as exc:
                    cls.exc = exc
                    logger.exception("Error accessing %s data path: %s", cls.__name__, root)
                    raise FileNotFoundError(f"{cls.__name__} data path is not accessible: {root}") from exc
                else:
                    cls.data_root = root
        if hasattr(cls, 'data_root'):
            cls.data_root.mkdir(parents=True, exist_ok=True)
    
class ScriptCamstim(Camstim):
    script: ClassVar[str]
    "path to script on Stim computer"
    params: ClassVar[dict[str, Any]] = {}
    
    @classmethod
    def start(cls):
        cls.get_proxy().start_script_from_path(cls.script)#, cls.params)
        
class SessionCamstim(Camstim):
    lims_user_id: ClassVar[str]
    labtracks_mouse_id: ClassVar[int]
    @classmethod
    def start(cls):
        cls.get_proxy().start_session(cls.labtracks_mouse_id, cls.lims_user_id)#, cls.params)
    
class NoCamstim(Camstim):
    "Run remote files (.bat) without sending directly to Camstim Agent"
    
    remote_file: ClassVar[str | pathlib.Path]
    ssh: ClassVar[fabric.Connection]
    user: ClassVar[str] = 'svc_neuropix'
    password: ClassVar[str]
    
    @classmethod
    def get_ssh(cls) -> fabric.Connection:
        with contextlib.suppress(AttributeError):
            return cls.ssh
        cls.initialize()
        return cls.ssh  

    @classmethod
    def initialize(cls) -> None:
        if not hasattr(cls, 'password'):
            cls.password = input(f"{cls.__name__} | Enter password for {cls.host}: ")
        cls.remote_file = utils.unc_to_local(pathlib.Path(cls.remote_file))
        cls.ssh = fabric.Connection(cls.host, cls.user, connect_kwargs=dict(password=cls.password))
        super().initialize()
        cls.test()
        
    @classmethod
    def test(cls) -> None:
        super().test()
        logger.debug(f"{cls.__name__} | Testing")
        try:
            result = cls.get_ssh().run('hostname', hide=True)
        except Exception as exc:
            raise TestFailure(f"{cls.__name__} Error connecting to {cls.host} via ssh: {exc!r}. Is this password correct? {cls.password}")
        else:
            if result.exited != 0:
                raise TestFailure(f"{cls.__name__} Error connecting to {cls.host} via ssh: {result}")
            logger.debug(f"{cls.__name__} | Connected to {cls.host} via ssh")
        
        try:
            result = cls.get_ssh().run(f'type {cls.remote_file}', hide=True)
        except Exception as exc:
            extra = f" | '{exc.result.command}': {exc.result.stderr.strip()!r}" if hasattr(exc, 'result') else ""
            raise TestFailure(f"{cls.__name__} | Error calling ssh-executed command{extra}")
        else:
            if result.exited != 0:
                raise TestFailure(f"{cls.__name__} Error accessing {cls.remote_file} on {cls.host} - is filepath correct? {result}")
            logger.debug(f"{cls.__name__} | {cls.remote_file} is accessible via ssh on {cls.host}")
            
    @classmethod
    def start(cls):
        if cls.is_started():
            logger.warning(f"{cls.__name__} already started")
            return 
        logger.debug(f"{cls.__name__} | Starting {cls.remote_file} on {cls.host}")
        cls.get_ssh().run(f'call {cls.remote_file}')
    
class MouseDirector(CamstimSyncShared):
    host = Rig.Mon.host
    gb_per_hr = 0
    serialization = "json"
    
class Cam3d(CamstimSyncShared):
    host = Rig.Mon.host
    serialization = 'json'
    started_state = ("READY", "CAMERAS_OPEN,CAMERAS_ACQUIRING")
    
    @classmethod
    def initialize(cls) -> None:
        cls.get_proxy().enable_cameras()


class MVR(CamstimSyncShared):
    # req proxy config - hardcode or overload ensure_config() 
    host: ClassVar[str] = Rig.Mon.host
    port: ClassVar[int]
    
    re_aux: re.Pattern = re.compile('aux|USB!', re.IGNORECASE)
    
    @classmethod
    def is_connected(cls) -> bool:
        if not utils.is_online(cls.host):
            cls.exc = ConnectionError(f"No response from {cls.host}: may be offline or unreachable")
            return False
        if not cls.get_proxy()._mvr_connected:
            cls.exc = ConnectionError(f"MVR likely not running on {cls.host}")
            return False
        try:
            _ = cls.get_camera_status()
        except ConnectionError as exc:
            cls.exc = exc
            return False
        return True
    

    @classmethod
    def initialize(cls) -> None:
        with contextlib.suppress(AttributeError):
            del cls.proxy
        cls.proxy = cls.get_proxy()
        cls.test()
        cls.configure_cameras()
        _ = cls.get_proxy().read() # empty buffer
        if isinstance(cls, Startable) and not cls.is_ready_to_start():
            if cls.is_started() and isinstance(cls, Stoppable):
                cls.stop()
            if isinstance(cls, Finalizable):
                cls.finalize()
            if not cls.is_ready_to_start():
                logger.warning("%s not ready to start: %s", cls.__name__, cls.get_state())
                return
        if cls.data_root:
            cls.data_files = []
        cls.initialization = time.time()
        logger.info("%s initialized: ready for use", cls.__name__)
        
    @classmethod
    def shutdown(cls) -> None:
        cls.get_proxy()._mvr_sock.close()  
        del cls.proxy
        
    @classmethod
    def get_proxy(cls) -> mvr.MVRConnector:
        with contextlib.suppress(AttributeError):
            return cls.proxy
        cls.ensure_config()
        logger.debug('Creating %s proxy to %s:%s', cls.__name__, cls.host, cls.port)
        cls.proxy = mvr.MVRConnector({'host':cls.host, 'port':cls.port})
        cls.proxy._mvr_sock.settimeout(cls.timeout)
        return cls.get_proxy()
    
    @classmethod
    def get_cameras(cls) -> list[dict[str, str]]:
        if not hasattr(cls, 'all_cameras'):
            cls.get_proxy().read()
            cls.all_cameras = cls.get_proxy().request_camera_ids()[0]['value']
        return cls.all_cameras
    
    @classmethod
    def get_camera_status(cls) -> list[dict[str, str]]:
        _ = cls.get_proxy().read() # empty buffer
        _ = cls.get_proxy()._send({"mvr_request":"get_camera_status"})
        for msg in reversed(cls.get_proxy().read()):
            if msg.get('mvr_response', '') == 'get_camera_status' and (cams := msg.get('value', [])):
                break
        else:
            logger.error("Could not get camera status from %s", cls.host)
            raise ConnectionError(f"Could not get camera status from {cls.host}")
        return [_ for _ in cams if any(_['camera_id'] == __['id'] for __ in cls.get_cameras())]
    
    @classmethod
    def get_state(cls) -> ProxyState:
        if not cls.is_connected():
            return '', 'MVR_CLOSED'
        status = cls.get_camera_status()
        # cam status could change between calls, so only get once  
        if any(not _['is_open'] for _ in status):
            return '', 'CAMERA_CLOSED'
        if any(not _['is_streaming'] for _ in status):
            return '', 'CAMERA_NOT_STREAMING'
        if cls.get_cameras_recording(status):
            return 'BUSY', 'RECORDING'
        return 'READY', ''
        
    @classmethod
    def get_cameras_recording(cls, status=None) -> list[dict[str, str]]:
        return [_ for _ in status or cls.get_camera_status() if _['is_recording']]
    
    @classmethod
    def is_ready_to_start(cls) -> bool:
        if cls.is_started():
            return False
        return all(_['is_open'] and _['is_streaming'] and not _['is_recording'] for _ in cls.get_camera_status())
    
    @classmethod
    def configure_cameras(cls) -> None:
        "Set MVR to record video from subset of all cameras, via `get_cameras` (implemented by subclass)"
        cam_ids = [_['id'] for _ in cls.get_cameras()]
        cls.get_proxy().define_hosts(cam_ids)
        cls.get_proxy().start_display()
    
    
class ImageMVR(MVR):
    gb_per_hr: ClassVar[int | float]
    min_rec_hr: ClassVar[int | float]
    
    # TODO ready state is if Aux cam is_open
    @classmethod
    def get_cameras(cls) -> list[dict[str, str]]:
        "Aux cam only"
        cams = super().get_cameras()
        return [_ for _ in cams if cls.re_aux.search(_['label'])]
        
    @classmethod
    def start(cls):
        if not cls.is_ready_to_start():
            # TODO display state, wait on user input to continue
            logger.error("%s not ready to start: %s", cls.__name__, cls.get_state())
            raise AssertionError(f"{cls.__name__} not ready to start: {cls.get_state()}")
        cls.latest_start = time.time()
        cls.get_proxy().take_snapshot()
        
    @classmethod
    def is_started(cls) -> bool:
        for msg in cls.get_proxy().read():
            if msg.get('mvr_broadcast', '') == 'snapshot_converted':
                return True 
            if msg.get('mvr_broadcast', '') == 'snapshot_failed':
                return False
        return False
    
    # TODO
    @classmethod
    def verify(cls) -> None:
        logger.warning('%s.verify() not implemented', cls.__name__)
    @classmethod
    def stop(cls) -> None:
        logger.warning('%s.stop() not implemented', cls.__name__)
    @classmethod
    def validate(cls) -> None: 
        logger.warning('%s.validate() not implemented', cls.__name__)
    
    @classmethod
    def finalize(cls) -> None:
        logger.debug('Finalizing %s', cls.__name__)
        t0 = time.time()
        timedout = lambda: time.time() > t0 + 5
        while cls.is_started() and not timedout():
            logger.debug("Waiting for %s to finish processing", cls.__name__)
            time.sleep(1) # TODO add backoff module
        if timedout:
            logger.warning("Timed out waiting for %s to finish processing", cls.__name__)
            return
        if not hasattr(cls, 'data_files'):
            cls.data_files = []
        cls.data_files.extend(new := cls.get_latest_data('*'))
        logger.debug("%s processing finished: %s", cls.__name__, [_.name for _ in new])
            
class VideoMVR(MVR):
    pretest_duration_sec: ClassVar[int | float]
    gb_per_hr: ClassVar[int | float]
    min_rec_hr: ClassVar[int | float]
    
    raw_suffix: ClassVar[int | float] = '.mp4'
    
    started_state = ('BUSY', 'RECORDING')
    
    @classmethod
    def get_cameras(cls) -> list[dict[str, str]]:
        "All available cams except Aux"
        cams = super().get_cameras()
        return [_ for _ in cams if cls.re_aux.search(_['label']) is None]

    @classmethod
    def start(cls) -> None:
        cls.latest_start = time.time()
        cls.get_proxy().start_record(
            record_time=365 * 60, #? copied from WSE - mins or sec? default is 80 * 60
        )

    @classmethod
    def verify(cls) -> None:
        "Assert data exists since latest start, or raise AssertionError."
        # files grow infrequently while MVR's recording - checking their size
        # is unreliable
        if not cls.is_started():
            logger.warning("Cannot verify %s if not started: %s", cls.__name__, cls.get_state())
            return
        if datetime.datetime.fromtimestamp(cls.latest_start) < datetime.datetime.now() - datetime.timedelta(seconds=cls.pretest_duration_sec):
            time.sleep(cls.pretest_duration_sec)
        if not (files := cls.get_latest_data()) or len(files) < len(cls.get_cameras_recording()):
            raise AssertionError(f"{cls.__name__} files do not match the number of cameras: {files}")
        logger.debug("%s verified: %s cameras recording to disk", cls.__name__, len(files))
    
    @classmethod
    def stop(cls) -> None:
        cls.get_proxy().stop_record()

    @classmethod
    def is_started(cls) -> bool:       
        if len(state := cls.get_state()) and all(msg in state for msg in cls.started_state):
            return True
        return False 
    
    @classmethod
    def finalize(cls) -> None:
        logger.debug('Finalizing %s', cls.__name__)
        if cls.is_started():
            cls.stop()
        t0 = time.time()
        timedout = lambda: time.time() > t0 + 30
        while not cls.is_ready_to_start() and not timedout():
            logger.debug("Waiting for %s to finish processing", cls.__name__)
            time.sleep(1) # TODO add backoff module
        if timedout:
            logger.warning("Timed out waiting for %s to finish processing", cls.__name__)
            return
        if not hasattr(cls, 'data_files'):
            cls.data_files = []
        cls.data_files.extend(new := (cls.get_latest_data('*.mp4') + cls.get_latest_data('*.json')))
        logger.debug("%s processing finished: %s", cls.__name__, [_.name for _ in new])
        
    @classmethod
    def validate(cls) -> None:
        logger.warning('%s.validate() not implemented', cls.__name__)

class JsonRecorder:
    "Just needs a `start` method that calls `write()`."
    
    log_name: ClassVar[str] 
    log_root: ClassVar[pathlib.Path]
    
    @classmethod
    def pretest(cls) -> None:
        cls.initialize()
        cls.start()
        cls.validate()
        
    @classmethod
    def ensure_config(cls)  -> None:
                
        config = CONFIG.get(__class__.__name__, {}) # class where this function is defined 
        config.update(**CONFIG.get(cls.__name__, {})) # the calling class, if different

        if not hasattr(cls, 'log_name'):
            cls.log_name = config.get('log_name', '{}_.json')
        cls.log_name = cls.log_name.format(datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
            
        if not hasattr(cls, 'log_root'):
            cls.log_root = config.get('log_root', '.')
        cls.log_root = pathlib.Path(cls.log_root).resolve()
               
    @classmethod
    def initialize(cls) -> None:
        logger.debug('%s initializing', __class__.__name__)
        cls.ensure_config()
        cls.initialization = time.time()
        log = (cls.log_root / cls.log_name).with_suffix('.json')
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        cls.all_files = [log]
        cls.test()
        
    @classmethod
    def test(cls) -> None:
        logger.debug('%s testing', __class__.__name__)
        try:
            _ = cls.get_current_log().open('w')
        except OSError as exc:
            raise TestFailure(f'{__class__.__name__} failed to open {cls.get_current_log()}') from exc
        
    @classmethod
    def get_current_log(cls) -> pathlib.Path:
        if not hasattr(cls, 'initialization'):
            cls.initialize()
        return cls.all_files[-1]

    @classmethod
    def read(cls) -> dict[str, str|float]:
        try:
            data = json.loads(cls.get_current_log().read_bytes())
        except json.JSONDecodeError:
            if cls.get_current_log().stat().st_size:
                raise
            data = {} # file was empty
        else:
            logger.debug('%s read from %s', __class__.__name__, cls.get_current_log())
        return data
    
    @classmethod
    def write(cls, new: dict) -> None:
        try:
            data = cls.read()
        except json.JSONDecodeError:
            data = {}
            file = cls.get_current_log().with_suffix('.new.json')
            file.touch()
            cls.all_files.append(file)
        else:
            file = cls.get_current_log()
        data.update(new)
        file.write_text(json.dumps(data, indent=4, sort_keys=False, default=str))
        logger.debug('%s wrote to %s', __class__.__name__, file)
     
    @classmethod
    def validate(cls) -> None:
        if not (log := cls.read()):
            cls.exc = TestFailure(f'{cls.__name__} failed to validate because log is empty: {cls.get_current_log()}')
            logger.error('%s failed to validate: log is empty %s', cls.__name__, cls.get_current_log(), exc_info=cls.exc)
        logger.debug('%s validated', __class__.__name__)
        
class YamlRecorder(JsonRecorder):
    
    @classmethod
    def test(cls) -> None:
        logger.debug('%s testing', __class__.__name__)
        super().test()
        try:
            import yaml
        except ImportError as exc:
            raise TestFailure(f'{__class__.__name__} failed to import yaml') from exc
        
    @classmethod
    def finalize(cls) -> None:
        logger.debug('Finalizing %s', __class__.__name__)
        log = json.load(cls.get_current_log().read_bytes())
        with utils.suppress(AttributeError, OSError): # if this fails we still have the json file
            yaml.dump(log, cls.get_current_log().with_suffix('.yaml').read_bytes())
        
class NewScaleCoordinateRecorder(JsonRecorder):
    "Gets current manipulator coordinates and stores them in a file with a timestamp."
    
    host: ClassVar[str] = Rig.Mon.host
    data_root: ClassVar[pathlib.Path]
    data_name: ClassVar[str] = 'log.csv'
    data_fieldnames: ClassVar[Sequence[str]] = ('last_moved', 'manipulator', 'x', 'y', 'z', 'x_virtual', 'y_virtual', 'z_virtual')
                
    num_probes: ClassVar[int] = 6
    max_travel: ClassVar[float]
    log_name: ClassVar[str] = 'newscale_coords_{}.json'
    log_root: ClassVar[pathlib.Path] = pathlib.Path('.').resolve() #! move to config after testing
    

    @classmethod
    def get_current_data(cls) -> pathlib.Path:
        cls.ensure_config()
        return cls.data_root / cls.data_name
    
    @classmethod
    def get_coordinates(cls) -> dict[str, float]:
        with cls.get_current_data().open('r') as _:
            reader = csv.DictReader(_, fieldnames=cls.data_fieldnames)
            rows = list(reader)
        coords = {}
        for row in reversed(rows):
            if len(coords.keys()) == cls.num_probes:
                break
            if (m := row.pop(cls.data_fieldnames[1]).strip()) not in coords:
                coords[m] = {}
                for k, v in row.items():
                    if 'virtual' in k:
                        continue
                    v = v.strip()
                    with contextlib.suppress(ValueError):
                        v = float(v)
                    coords[m].update({k: v})
                
        logger.debug('%s retrieved coordinates: %s', cls.__name__, coords)
        return coords
    
    @classmethod
    def start(cls):
        cls.write({str(datetime.datetime.now()): cls.get_coordinates()})
        
    @classmethod
    def test(cls) -> None:
        super().test()
        logger.debug('%s testing', __class__.__name__)
        try:
            _ = cls.get_current_data().open('r')
        except OSError as exc:
            raise TestFailure(f'{cls.__name__} failed to open {cls.get_current_data()}') from exc
        try:
            _ = cls.get_coordinates()
        except Exception as exc:
            raise TestFailure(f'{cls.__name__} failed to get coordinates') from exc
        else:
            logger.info('%s test passed', cls.__name__)

    @classmethod
    def ensure_config(cls)  -> None:
        
        super().ensure_config()
        
        if CONFIG.get('services', {}):
            config = CONFIG['services'].get(__class__.__name__, {})
            config.update(**CONFIG['services'].get(cls.__name__, {}))
        else:
            config = CONFIG.get(__class__.__name__, {}) # class where this function is defined 
            config.update(**CONFIG.get(cls.__name__, {})) # the calling class, if different

        if not hasattr(cls, 'host'):
            cls.host = config['host']
            
        # for resulting data
        if not hasattr(cls, 'data_root') or cls.host not in pathlib.Path(cls.data_root).parts:
            relative_path = config['data']
            if relative_path:
                root = pathlib.Path(f"//{cls.host}/{relative_path}")
                try:
                    _ = root.exists()
                except OSError as exc:
                    cls.exc = exc
                    logger.exception("Error accessing %s data path: %s", cls.__name__, root)
                    raise FileNotFoundError(f"{cls.__name__} data path is not accessible: {root}") from exc
                else:
                    cls.data_root = root
                    
        if not hasattr(cls, 'data_name'):
            cls.data_name = config['data_name']
        if not hasattr(cls, 'data_fieldnames'):
            cls.data_fieldnames = config['data_fieldnames']
        
