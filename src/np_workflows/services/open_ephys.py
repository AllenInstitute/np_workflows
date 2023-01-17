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

import config
import utils
    
exc: BaseException = Exception()  
    
host: str
port: str | int = 37497 # 1-800-EPHYS
latest_start: float

# device records:
gb_per_hr: int | float
min_rec_hr: int | float
pretest_duration_sec: int | float

# for resulting data:
folder: str
"The string that will be sent to Open Ephys to name the recording: typically `0123456789_366122_20220618`"
data_root: Optional[pathlib.Path] = None
data_files: Optional[Sequence[pathlib.Path]] = None

class State(enum.Enum):
    idle = "IDLE"
    acquire = "ACQUIRE"
    record = "RECORD"

class Endpoint(enum.Enum):
    status = "status"
    recording = "recording"
    processors = "processors"
    message = "message"

def url(endpoint: Endpoint):
    return f"http://{host}:{port}/api/{endpoint.value}"

def get_state() -> requests.Response:
    return requests.get(url(Endpoint.status)).json().get('mode')

def set_state(state: State) -> requests.Response:
    msg = {"mode": state.value}
    return requests.put(url(Endpoint.status), json.dumps(msg))

def is_connected() -> bool:
    global exc
    if not utils.is_online(host):
        exc = ConnectionError(f"Ephys | No response from {host}: may be offline or unreachable")
        return False
    try:
        state = get_state()
    except requests.RequestException as exc:
        return False
    else:
        if not any(_.value == state for _ in State):
            exc = ValueError(f"Ephys | Unexpected state: {state}")
            return False
    return True
        
def is_started() -> bool:
    if get_state() == State.record.value:
        return True
    return False

def is_ready_to_start() -> bool:
    if get_state() == State.acquire.value:
        return True
    return False

def start() -> None:
    if is_started():
        logging.warning("Open Ephys is already started")
        return
    if not is_ready_to_start():
        set_state(State.acquire)
        time.sleep(.5)
    latest_start = time.time()
    set_state(State.record)
    
def stop() -> None:
    set_state(State.acquire)
    
def set_open_ephys_name(path: Optional[str] = "_",  prepend_text: Optional[str] = "", append_text: Optional[str] = ""):
    
    recording = requests.get(url(Endpoint.recording)).json()
    
    if path == "":
        path = "_" # filename cannot be zero length
    path.replace(".","_")
    print(f"setting open ephys directory name - cannot contain periods -> replaced with underscores: {path}")
    recording['base_text'] = path
    recording['prepend_text'] = prepend_text
    recording['append_text'] = append_text
    return requests.put(url(Endpoint.recording), json.dumps(recording))

def clear_open_ephys_name():
    return set_open_ephys_name(path="_temp_", prepend_text="", append_text="")

def set_idle():
    "Should be called before sending any configuration to Open Ephys"
    set_state(State.idle)
    
def reset_open_ephys():
    if is_started():
        stop()
    time.sleep(.5)
    set_idle()
    time.sleep(.5)
    clear_open_ephys_name()
    time.sleep(.5)
    start()
    time.sleep(1)
    stop()
    time.sleep(.5)

def get_record_nodes() -> list[dict[str, Any]]:
    """Returns a list of record node info dicts, incl keys `node_id, `parent_directory`"""
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
            
def verify():
    # check dir on disk is growing at all recording locations
    for data_dir in get_latest_data_dirs():
        for npy in data_dir.rglob('*sample_numbers.npy'): # some file that should always be present 
            if utils.is_file_growing(npy):
                break
        else:
            return False
    return True
    
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
    #logging.info(f"sending ...
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
        
    r = EphysHTTP.set_open_ephys_name(path = "mouseID_", prepend_text="sessionID", append_text="_date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    r = EphysHTTP.set_open_ephys_name(path = "mouse", prepend_text="session", append_text="date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    print((r.json()['base_text'])) # fails as of 06/23 https://github.com/open-ephys/plugin-GUI/pull/514
"""