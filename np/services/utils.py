from __future__ import annotations

import contextlib
import datetime
import logging
import pathlib
import time
import logging
import math
import os
import shutil
import subprocess
import sys
from typing import Any, Generator, Literal, Mapping, Optional, Sequence

import mpetk.mpeconfig
from config import Rig

from protocols import *

def config_from_zk(rig: Optional[Literal[0, 1, 2]]=None) -> Mapping[str, Any]:
    config = mpetk.mpeconfig.source_configuration(
            'np_workflows', 
            hosts='eng-mindscope.corp.alleninstitute.org', 
            fetch_logging_config=False,
            send_start_log=False,
            rig_id=rig
        )
    # params common across rigs are returned from mpeconfig['proxies], such as `port` 
    # - we need to add rig-specific cfg, such as `host`, confusingly found in the `shared` section of the mpeconfig
    for k, v in config['proxies'].items():
        if rig_config := config['shared'].get('proxies', {}).get(k, {}):
            v.update(**rig_config)
        if 'host' not in v:
            try:
                v['host'] = Rig[k].host
            except KeyError:
                logging.warning("No host found in config for %s - will need adding manually, e.g. `%s.host = 'W10DTSM18307'`", k, k)
            
    return config['proxies']

CONFIG = config_from_zk()


@contextlib.contextmanager
def debug_logging() -> Generator[None, None, None]:
    logger = logging.getLogger()
    level_0 = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.setLevel(level_0)

@contextlib.contextmanager
def stop_on_exit_or_error(obj: Stoppable):
    if not isinstance(obj, Stoppable):
        raise TypeError(f"{obj} does not support stop()")
    try:
        yield
    except Exception as exc:
        with contextlib.suppress(Exception):
            obj.exc = exc
            logging.getLogger().error("%s interrupted as a result of error", obj.__name__ if isinstance(obj, type) else obj.__class__.__name__, exc_info=exc)
        raise
    finally:
        obj.stop()

@contextlib.contextmanager
def suppress(*exceptions: Type[BaseException]):
    try:
        yield
    except exceptions or Exception as exc:
        with contextlib.suppress(Exception):        
            logging.getLogger().error("Error suppressed: continuing despite raised exception", exc_info=exc)
    finally:
        return
    
def is_online(host: str) -> bool:
    "Use OS's `ping` cmd to check if `host` is online."
    command = ["ping", "-n" if "win" in sys.platform else "-c", "1", host]
    try:
        return subprocess.call(command, stdout=subprocess.PIPE, timeout=1.0) == 0
    except subprocess.TimeoutExpired:
        return False
    
def unc_to_local(path: pathlib.Path) -> pathlib.Path:
    "Convert UNC path to local path if on Windows."
    if 'win' not in sys.platform:
        return path
    comp = os.environ["COMPUTERNAME"]
    if comp in path.drive:
        drive = path.drive.split('\\')[-1]
        drive = drive - '$' if drive[-1] == '$' else drive
        drive = drive + ':' if drive[-1] != ':' else drive 
        drive += '\\'
        path =  pathlib.Path(drive, path.relative_to(path.drive))
    return path

def free_gb(path: str|bytes|os.PathLike) -> float:
    "Return free space at `path`, to .1 GB. Raises FileNotFoundError if `path` not accessible."
    path = pathlib.Path(path)
    path = unc_to_local(path)
    return round(shutil.disk_usage(path).free / 1e9, 1)

def get_files_created_between(
    path: str | bytes | os.PathLike, 
    glob: str = '*',
    start: int | datetime.datetime = 0, 
    end: int | datetime.datetime = None,
) -> list[pathlib.Path]:
    path = pathlib.Path(path)
    if not path.is_dir():
        path = path.parent
    if not end:
        end = time.time()
    start = start.timestamp() if isinstance(start, datetime.datetime) else start
    end = end.timestamp() if isinstance(end, datetime.datetime) else end
    ctime = lambda x: x.stat().st_ctime
    files = (file for file in path.glob(glob) if int(start) <= ctime(file) <= end)
    return sorted(files, key=ctime)

def is_file_growing(path: str|bytes|os.PathLike) -> bool:
    "Compares size of most recent .sync data file at two time-points - will block for up to 20s depending on file-size."
    path = pathlib.Path(path)
    size_0 = path.stat().st_size
    # for sync: file is appended periodically in chunks that scale non-linearly with size
    time.sleep(2 * math.log10(size_0)) 
    if path.stat().st_size == size_0:
        return False
    return True
