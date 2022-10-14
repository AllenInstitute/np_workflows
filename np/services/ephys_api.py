import json
import logging
import os
import pathlib
import shutil
import socket
import sys
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

import requests

sys.path.append("c:/program files/aibs_mpe/workflow_launcher")
from np.services import config

sys.path.append("..")
sys.path.append(".")
try:
    # get protobufs module if available, for Router implementation
    from np.services import ephys_edi_pb2 as ephys_messages
except:
    try:
        from . import ephys_edi_pb2 as ephys_messages
    except:
        try:
            import ephys_edi_pb2 as ephys_messages
        except:
            pass
    
class Ephys(ABC):
    """ Base class for communication with open ephys """
    
    @abstractmethod
    def start_ecephys_recording():
        pass
    
    @abstractmethod
    def stop_ecephys_recording():
        pass

    @abstractmethod
    def start_ecephys_acquisition():
        pass

    @abstractmethod
    def stop_ecephys_acquisition():
        pass

    @abstractmethod
    def set_open_ephys_name(path):
        pass

    @abstractmethod
    def clear_open_ephys_name():
        pass

    @abstractmethod
    def request_open_ephys_status():
        pass

    @abstractmethod
    def reset_open_ephys():
        pass
    
    @abstractmethod
    def is_recording():
        pass
    
class EphysRouter(Ephys):
    """ Original ZMQ protobuf implementation - requires ephys_edi_pb2.py output from ephys_edi.proto """ 
    
    io = None
    
    @classmethod
    def setup_proxy(cls, io):
        cls.io = io
        cls.io.add_message_bundle(ephys_messages)
        def handle_message(message_id, message, timestamp, io):
            print(f'{timestamp}: Received message {message_id} from router')
            print(message)
        cls.io.register_for_message('system_info', handle_message)
        cls.io.register_for_message('system_status', handle_message)
        cls.io.register_for_message('set_data_file_path', handle_message)
        cls.io.register_for_message('acquisition', handle_message)
        cls.io.register_for_message('recording', handle_message)
    
    @classmethod    
    def connected(cls):
        if cls.io is None:
            return False        
        return cls.request_open_ephys_status()

    @classmethod
    def start_ecephys_recording(cls):
        EphysRouter.copy_xml_to_recording_folders()
        return cls.io.write(ephys_messages.recording(command=1))

    @classmethod
    def stop_ecephys_recording(cls):
        EphysRouter.copy_xml_to_recording_folders()
        return cls.io.write(ephys_messages.recording(command=0))

    @classmethod
    def start_ecephys_acquisition(cls):
        return cls.io.write(ephys_messages.acquisition(command=1))

    @classmethod
    def stop_ecephys_acquisition(cls):
        return cls.io.write(ephys_messages.acquisition(command=0))

    @classmethod
    def set_open_ephys_name(cls,path):
        return cls.io.write(ephys_messages.set_data_file_path(path=path))

    @classmethod
    def clear_open_ephys_name(cls):
        return cls.io.write(ephys_messages.set_data_file_path(path='_temp_'))

    @classmethod
    def request_open_ephys_status(cls):
        return cls.io.write(ephys_messages.request_system_status())

    @classmethod
    def reset_open_ephys(cls):
        # if EphysHTTP.request_open_ephys_status() == "RECORD":
        EphysRouter.stop_ecephys_recording()
        time.sleep(.5)
        # if EphysHTTP.request_open_ephys_status() == "ACQUIRE":
        EphysRouter.stop_ecephys_acquisition()
        time.sleep(.5)
        EphysRouter.clear_open_ephys_name()
        time.sleep(.5)
        EphysRouter.start_ecephys_acquisition()
        time.sleep(.5)
        EphysRouter.start_ecephys_recording()
        time.sleep(3)
        EphysRouter.stop_ecephys_recording()
        time.sleep(.5)
    
    @staticmethod
    def latest_recording_path() -> pathlib.Path:
        """Find path on A:/.
        
        Hard-coding A: and B: drive letter assumptions for these functions
        - this API ( EphysRouter ) won't be used
        once we upgrade to OE v0.6.0 with HTTP server
        """
        subfolders = [sub for sub in pathlib.Path(f"//{config.Rig.Acq.host}/A").iterdir() if sub.is_dir() and not any(_ in str(sub) for _ in ["System Volume Information", "$RECYCLE.BIN"])]
        if subfolders:
            subfolders.sort(key=os.path.getmtime,reverse=True)            
            return subfolders[0]
        
    @staticmethod
    def is_recording() -> bool:
        # check dir on disk is growing 
        gen = EphysRouter.latest_recording_path().rglob('*.npx2')
        for npx2_file in gen:
            if not npx2_file:
                break
            st_size_0 = npx2_file.stat().st_size 
            time.sleep(0.1)
            if npx2_file.stat().st_size > st_size_0: # check again, should have increased
                return True
            else:
                continue
            
    @staticmethod 
    def copy_xml_to_recording_folders():
        try:
            xml_repo = pathlib.Path(f"//{config.Rig.Acq.host}/c$/Users/svc_neuropix/Desktop/open-ephys-neuropix")
            src = xml_repo / EphysRouter.latest_recording_path().name / "settings.xml"
            
            for drive_letter in ["A", "B"]:
                drive = pathlib.Path(f"//{config.Rig.Acq.host}/{drive_letter}")
                dest = drive / EphysRouter.latest_recording_path().name
                
                shutil.copy2(src, dest)
        except:
            print(f"failed to copy {src} to {dest}")
            pass
                
class EphysHTTP(Ephys):
    """ Interface for HTTP server introduced in open ephys v0.6.0 (2022) """
    #TODO wait on return msgs from requests between changing modes etc
    # we don't want to send multiple put requests so quickly that the server can't respond
    
    #? is it necessary to transition idle>acquire>record (and reverse)? is idle>record possible?
    #? can we set rec dir name while in acquire mode?
    
    #TODO get broadcast message working to put plugin settings
    
    try:
        hostname = config.Rig.Acq.host
        print(f'auto-setting Acq computer: {hostname}')
    except (NameError,KeyError):
        pc = socket.gethostname()
        acq = {
            "W10DTSM112719":"W10DT05515", # NP0
            "W10DTSM18306":"W10DT05501", # NP1
            "W10DTSM18307":"W10DT713844", # NP2
            "W10DTMJ0AK6GM":"W10SV108131", #ben-desktop:btTest
        }
        hostname = acq.get(pc,"localhost")
    
    server = f"http://{hostname}:37497/api"
    status_endpoint = f"{server}/status"
    recording_endpoint = f"{server}/recording"
    processors_endpoint = f"{server}/processors"
    message_endpoint = f"{server}/message"
    
    class EphysModes(Enum):
        idle = "IDLE"
        acquire = "ACQUIRE"
        record = "RECORD"

    @staticmethod
    def set_URL(url):
        EphysHTTP.server = url
        EphysHTTP.status_endpoint = f"{EphysHTTP.server}/status"
        EphysHTTP.recording_endpoint = f"{EphysHTTP.server}/recording"
        EphysHTTP.processors_endpoint = f"{EphysHTTP.server}/processors"
        EphysHTTP.message_endpoint = f"{EphysHTTP.server}/message"

    @staticmethod
    def get_mode() -> requests.Response:
        logging.info(f"request: 'mode' <-- {EphysHTTP.status_endpoint}")
        return requests.get(EphysHTTP.status_endpoint).json()['mode']

    @staticmethod
    def set_mode(mode: EphysModes) -> requests.Response:
        if not isinstance(mode, EphysHTTP.EphysModes):
            raise TypeError(f"Expected mode of type EphysModes but found {type(mode)}")
        mode_msg = {"mode":mode.value}
        logging.info(f"sending: {mode_msg} --> {EphysHTTP.status_endpoint}")
        return requests.put(EphysHTTP.status_endpoint, json.dumps(mode_msg))

    @staticmethod
    def start_ecephys_recording():
        return EphysHTTP.set_mode(EphysHTTP.EphysModes.record)

    @staticmethod
    def stop_ecephys_recording():
        return EphysHTTP.set_mode(EphysHTTP.EphysModes.acquire)

    @staticmethod
    def start_ecephys_acquisition():
        return EphysHTTP.set_mode(EphysHTTP.EphysModes.acquire)

    @staticmethod
    def stop_ecephys_acquisition():
        return EphysHTTP.set_mode(EphysHTTP.EphysModes.idle)

    @staticmethod
    def set_open_ephys_name(path: Optional[str] = "_",  prepend_text: Optional[str] = "", append_text: Optional[str] = ""):
        
        recording = requests.get(EphysHTTP.recording_endpoint).json()
        
        if path == "":
            path = "_" # filename cannot be zero length
        path.replace(".","_")
        print(f"setting open ephys directory name - cannot contain periods -> replaced with underscores: {path}")
        recording['base_text'] = path
        recording['prepend_text'] = prepend_text
        recording['append_text'] = append_text
        return requests.put(EphysHTTP.recording_endpoint, json.dumps(recording))

    @staticmethod
    def clear_open_ephys_name():
        return EphysHTTP.set_open_ephys_name(path="_temp_", prepend_text="", append_text="")

    @staticmethod
    def request_open_ephys_status():
        return EphysHTTP.get_mode()

    @staticmethod
    def reset_open_ephys():
        if EphysHTTP.request_open_ephys_status() == "RECORD":
            EphysHTTP.stop_ecephys_recording()
            time.sleep(.5)
        if EphysHTTP.request_open_ephys_status() == "ACQUIRE":
            EphysHTTP.stop_ecephys_acquisition()
            time.sleep(.5)
        EphysHTTP.clear_open_ephys_name()
        time.sleep(.5)
        EphysHTTP.start_ecephys_acquisition()
        time.sleep(.5)
        EphysHTTP.start_ecephys_recording()
        time.sleep(3)
        EphysHTTP.stop_ecephys_recording()
        time.sleep(.5)

    @staticmethod
    def latest_recording_path() -> pathlib.Path:
        """For the first record node found, return the folder path it's currently
        recording to - useful for confirming that a recording is ongoing.
        
        Since the path that's set in open ephys is appended by a number (1) if it
        already exists, we're better off finding the latest folder in the directory """
        recording = requests.get(EphysHTTP.recording_endpoint).json()
        record_node_info = recording.get('record_nodes',None)
        if record_node_info:
            first_record_node_info = record_node_info[0]
            
            # assemble current record folder path
            record_root = pathlib.Path(f"//{config.Rig.Acq.host}/{first_record_node_info['parent_directory'][0]}")
            subfolders = [sub for sub in record_root.iterdir() if sub.is_dir() and not any(_ in str(sub) for _ in ["System Volume Information", "$RECYCLE.BIN"])]
            
            if subfolders:
                subfolders.sort(key=os.path.getmtime,reverse=True)            
                return subfolders[0]
            
        return None
    
    @staticmethod
    def is_recording() -> bool:
        "Tests wheter Open Ephys is currently recording and confirms files are being written to disk"
        # check mode is RECORD
        if EphysHTTP.request_open_ephys_status() != EphysHTTP.EphysModes.record.value:
            return False
        
        # check dir on disk is growing 
        gen = EphysHTTP.latest_recording_path().rglob('*sample_numbers.npy')
        for sample_numbers in gen:
            if not sample_numbers:
                break
            st_size_0 = sample_numbers.stat().st_size 
            time.sleep(0.1)
            if sample_numbers.stat().st_size > st_size_0: # check again, should have increased
                return True
            else:
                continue
        # mtime_0 = EphysHTTP.latest_recording_path().stat().st_mtime # time last modified
        # if EphysHTTP.latest_recording_path().stat().st_mtime > mtime_0: # check again, mtime should have increased
        #     return True
        return False
        
    """  
    @staticmethod
    def set_ref(ext_tip="TIP"):
        # for port in [0, 1, 2]: 
        #     for slot in [0, 1, 2]: 
                
        slot = 2 #! Test
        port = 100 #! Test
        dock = 0 # TODO may be 1 or 2 with firmware upgrade 
        tip_ref_msg = {"text": f"NP REFERENCE {slot} {port} {dock} {ext_tip}"}
        #logging.info(f"sending ...
        print(f"sending: {tip_ref_msg} --> {EphysHTTP.message_endpoint}")
        # return 
        requests.put(EphysHTTP.message_endpoint, json.dumps(tip_ref_msg))
        time.sleep(3)
    """
    
    
# TODO set up everything possible from here to avoid accidentally changed settings ? 
# probe channels
# sampling rate 
# tip ref
# signal chain?
# acq drive letters

"""
if __name__ == "__main__":
    r = requests.get(EphysHTTP.recording_endpoint)
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
        
    r = EphysHTTP.set_open_ephys_name(path = "mouseID_", prepend_text="sessionID", append_text="_date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    r = EphysHTTP.set_open_ephys_name(path = "mouse", prepend_text="session", append_text="date")
    print((r.json()['current_directory_name'], r.json()['prepend_text'], r.json()['append_text']))
    
    print((r.json()['base_text'])) # fails as of 06/23 https://github.com/open-ephys/plugin-GUI/pull/514
"""

    

    