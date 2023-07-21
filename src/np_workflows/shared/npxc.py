import contextlib
import datetime
import inspect
import pathlib
import shutil
import sys
import time
from typing import Any, Generator, Sequence, Type
import zlib

import fabric
import np_config
import np_logging
import np_session
from np_services import Initializable, Testable, TestError, Finalizable, Service
from np_services import Sync, VideoMVR, ImageMVR, Cam3d, ScriptCamstim, OpenEphys  
import np_services 
from np_config import Rig

logger = np_logging.getLogger(__name__)

# Assign default values to global variables so they can be imported elsewhere
# experiment: Experiment | str = ''
operator: np_session.User | str = ""
mouse: np_session.Mouse | str = ""
is_mouse_in_lims: bool | None = None
session: np_session.Session | str | int = ""

# experiments: tuple[Type[Experiment], ...] = (
#     classes.Pretest,
#     classes.NpUltra,
#     ) # TODO plug-in experiments

RIG = np_config.Rig()
CONFIG = RIG.config

lims_user_ids: tuple[str, ...] = tuple(
    sorted(
        CONFIG.get('lims_user_ids',
            [
            "hannah.belski",
            "hannah.cabasco",
            "ryan.gillis",
            "henry.loeffler",
            "corbettb",
            "ben.hardcastle",
            "samg",
            "ethan.mcbride",
            "jackie.kuyat",
            "andrew.shelton",
            ]
        )
    )
)

default_mouse_id: int = int(CONFIG.get('default_mouse_id', 366122))


def get_operators() -> list[str]:
    return list(lims_user_ids)


def print_countdown_timer(seconds: int | float | datetime.timedelta = 0, **kwargs):
    """Block execution for a given number of seconds (or any timedelta kwargs), printing a countdown timer to the console."""
    if isinstance(seconds, datetime.timedelta):
        wait = seconds
    else:
        wait = datetime.timedelta(seconds=seconds, **kwargs)
    time_0: float = time.time()
    time_remaining = lambda: datetime.timedelta(
        seconds=wait.total_seconds() - (time.time() - time_0)
    )
    while time_remaining().total_seconds() > 0:
        print(f"Waiting {wait} \t{time_remaining()}", end="\r", flush=True)
        time.sleep(0.1)

def photodoc(img_name: str) -> pathlib.Path:
    """Capture image with `label` appended to filename, and return the filepath.
            
    If multiple images are captured, only the last will remain in the Imager.data_files list.
    """
    if RIG.idx == 0:
        from np_services import Cam3d as ImageCamera
    else:
        from np_services import ImageMVR as ImageCamera
    from np_services import NewScaleCoordinateRecorder
    
    ImageCamera.label = img_name
    if isinstance(ImageCamera, Initializable) and not getattr(ImageCamera, 'initialization', None):
        ImageCamera.initialize()
        
    ImageCamera.start()
    
    if isinstance(ImageCamera, Finalizable):
        ImageCamera.finalize()
        
    if isinstance(NewScaleCoordinateRecorder, Initializable) and not getattr(NewScaleCoordinateRecorder, 'initialization', None):
        NewScaleCoordinateRecorder.initialize()
    NewScaleCoordinateRecorder.label = img_name
    NewScaleCoordinateRecorder.start()
    
    # remove all but latest file with the current label
    if img_name and ImageCamera.data_files:
        views = 2 if ImageCamera.__name__ == 'Cam3d' else 1
        def files_with_label():
            return sorted([_ for _ in ImageCamera.data_files if img_name in _.name])
        while len(files_with_label()) > views:
            ImageCamera.data_files.remove(files_with_label()[0])
            
    return ImageCamera.data_files[-1]

def copy_files(services: Sequence[Service], session_folder: pathlib.Path):
    """Copy files from raw data storage to session folder for all services."""
    password = input(f'Enter password for svc_neuropix:')
    for service in services:
        match service.__class__.__name__:
            case "OpenEphys" | "open_ephys":
                continue # copy ephys after other files
            case _:
                with contextlib.suppress(AttributeError):
                    files = service.data_files or service.get_latest_data('*')
                    if not files:
                        continue
                    files = set(files)
                    print(files)
                    for file in files:
                        shutil.copy2(file, session_folder)
    
    password = np_config.fetch('/logins')['svc_neuropix']['password']
    ssh = fabric.Connection(host=np_services.OpenEphys.host, user='svc_neuropix', connect_kwargs=dict(password=password))
    for ephys_folder in np_services.OpenEphys.data_files:

        with contextlib.suppress(Exception):
            with ssh:
                ssh.run(
                f'robocopy "{ephys_folder}" "{session_folder / ephys_folder.name}" /j /s /xo' 
                # /j unbuffered, /s incl non-empty subdirs, /xo exclude src files older than dest
                )
            
            
import warnings

def hide_warning_lines(msg:str,category:str,*args,**kwargs):
    print("\n{}: {}\n".format(category.__name__, msg))
warnings.showwarning = hide_warning_lines

import IPython
def toggle_tracebacks() -> Generator[None, None, None]:
    if ipython := IPython.get_ipython():
        show_traceback = ipython.showtraceback
        
        def hide_traceback(exc_tuple=None, filename=None, tb_offset=None,
                        exception_only=False, running_compiled_code=False):
            etype, value, tb = sys.exc_info()
            return ipython._showtraceback(etype, value, ipython.InteractiveTB.get_exception_only(etype, value))
        
        hidden = True
        while True:
            ipython.showtraceback = hide_traceback if hidden else show_traceback
            hidden = yield
    else:
        raise RuntimeError("Not in IPython")
    
with contextlib.suppress(RuntimeError):
    toggle_tb = toggle_tracebacks()

    toggle_tb.send(None)
    def show_tracebacks():
        toggle_tb.send(False)
    def hide_tracebacks():
        toggle_tb.send(True)
        
def now() -> str:
    return np_services.utils.normalize_time(time.time())

def validate_or_overwrite(validate: str | pathlib.Path, src: str | pathlib.Path):
    "Checksum validate against `src`, (over)write `validate` as `src` if different."
    validate, src = pathlib.Path(validate), pathlib.Path(src)
    def copy():
        logger.debug("Copying %s to %s", src, validate)
        shutil.copy2(src, validate)
    while (
        validate.exists() == False
        or (v := zlib.crc32(validate.read_bytes())) != (c := zlib.crc32(pathlib.Path(src).read_bytes()))
        ):
        copy()
    logger.debug("Validated %s CRC32: %08X", validate, (v & 0xFFFFFFFF) )
