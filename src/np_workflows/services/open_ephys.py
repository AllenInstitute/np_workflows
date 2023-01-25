from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import socket
import sys
import time
import enum
from typing import Any, Optional, Sequence

import requests
import np_logging

from . import protocols
from . import config
from . import utils

# global vars -------------------------------------------------------------------------- #
logger = np_logging.getLogger(__name__) # logs will show full module path

__name__ = "OpenEphys" # Service protocol operations will see just the 'class' name

exc: Optional[BaseException] = None
initialized: float = 0
"`time.time()` when the service was initialized."

host: str = config.Rig.Acq.host
port: str | int = 37497 # 1-800-EPHYS
latest_start: float = 0
"`time.time()` when the service was last started via `start()`."

# device records:
gb_per_hr: int | float = 250 # per drive
min_rec_hr: int | float = 2
pretest_duration_sec: int | float = .5

# for resulting data:
folder: str #! required
"The string that will be sent to Open Ephys to name the recording: typically `0123456789_366122_20220618`"
data_files: Sequence[pathlib.Path] = []
"Storage for paths collected over the experiment."
data_root: Optional[pathlib.Path] = None
# -------------------------------------------------------------------------------------- #

class State(enum.Enum):
    idle = "IDLE"
    acquire = "ACQUIRE"
    record = "RECORD"

class Endpoint(enum.Enum):
    status = "status"
    recording = "recording"
    processors = "processors"
    message = "message"

def pretest() -> None:
    initialize()
    test()
    start()
    time.sleep(pretest_duration_sec)
    verify()
    stop()
    finalize()
    validate()

def url(endpoint: Endpoint):
    return f"http://{host}:{port}/api/{endpoint.value}"

def get_state() -> requests.Response:
    mode = requests.get(u := url(Endpoint.status)).json().get('mode')
    logger.debug('%s -> get mode: %s', u, mode)
    return mode

def set_state(state: State) -> requests.Response:
    msg = {"mode": state.value}
    mode = requests.put(u := url(Endpoint.status), json.dumps(msg))
    logger.debug('%s <- set mode: %s', u, state.value)
    return mode

def is_connected() -> bool:

    global exc

    if not utils.is_online(host):
        exc = protocols.TestFailure(f"OpenEphys | No response from {host}: may be offline or unreachable")
        return False

    try:
        state = get_state()
    except requests.RequestException:
        exc = protocols.TestFailure(f"OpenEphys | No response from Open Ephys http server: is the software started?")
        return False
    else:
        if not any(_.value == state for _ in State):
            exc = protocols.TestFailure(f"OpenEphys | Unexpected state: {state}")
            return False

    return True

def initialize() -> None:
    logger.info("OpenEphys | Initializing")
    global data_files
    data_files = []
    global initialized
    initialized = time.time()
    test()
    set_folder(folder)

def test() -> None:
    logger.info("OpenEphys | Testing")
    if not is_connected():
        if exc:
            raise exc

def is_started() -> bool:
    if get_state() == State.record.value:
        return True
    return False

def is_ready_to_start() -> bool:
    if get_state() == State.acquire.value:
        return True
    return False

def start() -> None:
    logger.info('OpenEphys | Starting recording')
    if is_started():
        logger.warning("OpenEphys is already started")
        return
    if not is_ready_to_start():
        set_state(State.acquire)
        time.sleep(.5)
    global latest_start
    latest_start = time.time()
    set_state(State.record)
    
def stop() -> None:
    logger.info('OpenEphys | Stopping recording')
    set_state(State.acquire)

def finalize() -> None:
    logger.info('OpenEphys | Finalizing')
    data_files.extend(get_latest_data_dirs())
    unlock_previous_recording()

def set_folder(name: str,  prepend_text: Optional[str] = "", append_text: Optional[str] = "") -> None:
    """Recording folder string"""
    recording = requests.get(url(Endpoint.recording)).json()
    
    if name == "":
        name = "_" 
        logger.warning("OpenEphys | Recording directory cannot be empty, replaced with underscore: %s" , name)
    if "." in name:
        name.replace(".","_")
        logger.warning("OpenEphys | Recording directory cannot contain periods, replaced with underscores: %s" , name)
    
    recording['base_text'] = name
    recording['prepend_text'] = prepend_text
    recording['append_text'] = append_text
    
    response = requests.put(url(Endpoint.recording), json.dumps(recording))
    time.sleep(.1)
    if (actual := response.json().get('base_text')) != name:
        raise protocols.TestFailure(f'OpenEphys | Set folder to {name}, but software shows: {actual}')

def get_folder() -> str | None:
    return requests.get(url(Endpoint.recording)).json().get('base_text')

def clear_open_ephys_name() -> None:
    set_folder("_temp_")

def set_idle():
    "Should be called before sending any configuration to Open Ephys"
    if is_started():
        stop()
    time.sleep(.5)
    set_state(State.idle)
    
def unlock_previous_recording():
    "stop rec/acquiring | set name to _temp_ | record briefly | acquire"
    set_idle()
    time.sleep(.5)
    clear_open_ephys_name()
    time.sleep(.5)
    start()
    time.sleep(.5)
    stop()
    time.sleep(.5)

def get_record_nodes() -> list[dict[str, Any]]:
    """Returns a list of record node info dicts, incl keys `node_id`, `parent_directory`"""
    return requests.get(url(Endpoint.recording)).json().get('record_nodes', None) or []

def get_data_roots() -> list[pathlib.Path]:
    return [pathlib.Path(f"//{host}/{_['parent_directory'].replace(':','')}") for _ in get_record_nodes()]

def get_latest_data_dirs() -> list[pathlib.Path]:
    """Returns the path to the latest data folder, based on the latest modified time"""
    dirs = []
    for root in get_data_roots():
        if subfolders := [sub for sub in root.iterdir() if sub.is_dir() and not any(_ in str(sub) for _ in ["System Volume Information", "$RECYCLE.BIN"])]:
            subfolders.sort(key=lambda _:_.stat().st_ctime)
            dirs.append(subfolders[-1])
    return dirs
            
def verify() -> None:
    logger.info('OpenEphys | Verifying')
    for data_dir in get_latest_data_dirs():
        for file in reversed(utils.get_files_created_between(data_dir, '*/*/*/continuous/*/sample_numbers.npy', latest_start)):
            if utils.is_file_growing(file):
                break
        else:
            raise protocols.TestFailure(f"OpenEphys | Data file(s) not increasing in size in {data_dir}")

def validate() -> None:
    logger.warning('OpenEphys | validate() not implemented')

if __name__ == "__main__":
    # testing on np.0
    host = 'W10DT713842'
    
    print(get_latest_data_dirs())
    
    # msg = {"mode": State.acquire.value}
    # print(requests.put(url(Endpoint.status), json.dumps(msg)).json())
    # print(get_state())
    # start()
    # print(get_state())
    # time.sleep(.5)
    # stop()
    # print(get_state())
    
    

  
def set_ref(ext_tip="TIP"):
    # for port in [0, 1, 2]: 
    #     for slot in [0, 1, 2]: 
            
    slot = 2 #! Test
    port = 1 #! Test
    dock = 1 # TODO may be 1 or 2 with firmware upgrade 
    tip_ref_msg = {"text": f"NP REFERENCE {slot} {port} {dock} {ext_tip}"}
    #logger.info(f"sending ...
    # return 
    requests.put('http://localhost:37497/api/processors/100/config', json.dumps(tip_ref_msg))
    time.sleep(3)


    
# TODO set up everything possible from here to avoid accidentally changed settings ? 
# probe channels
# sampling rate 
# tip ref
# signal chain?
# acq drive letters

"""
if __name__ == "__main__":
    r = requests.get(Endpoint.recording)
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
        
    r = EphysHTTP.set_folder(path = "mouseID_", prepend_text="sessionID", append_text="_date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    r = EphysHTTP.set_folder(path = "mouse", prepend_text="session", append_text="date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    print((r.json()['base_text'])) # fails as of 06/23 https://github.com/open-ephys/plugin-GUI/pull/514
"""