import json
import logging
import sys
import time
from enum import Enum
from typing import Optional
from abc import ABC

import requests

sys.path.append("..")
try:
    # get protobufs module if available, for Router implementation
    from . import ephys_edi_pb2 as ephys_messages
except:
    ...

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
    
    
class EphysRouter(Ephys):
    @staticmethod
    def start_ecephys_recording():
        return ephys_messages.recording(command=1)

    @staticmethod
    def stop_ecephys_recording():
        return ephys_messages.recording(command=0)

    @staticmethod
    def start_ecephys_acquisition():
        return ephys_messages.acquisition(command=1)

    @staticmethod
    def stop_ecephys_acquisition():
        return ephys_messages.acquisition(command=0)

    @staticmethod
    def set_open_ephys_name(path):
        return ephys_messages.set_data_file_path(path=path)

    @staticmethod
    def clear_open_ephys_name():
        return ephys_messages.set_data_file_path(path='')

    @staticmethod
    def request_open_ephys_status():
        return ephys_messages.request_system_status(path='')

    @staticmethod
    def reset_open_ephys(io):
        io.write(EphysRouter.clear_open_ephys_name())
        time.sleep(.5)
        io.write(EphysRouter.start_ecephys_recording())
        time.sleep(3)
        io.write(EphysRouter.stop_ecephys_recording())
        time.sleep(.5)


class EphysHTTP(Ephys):
    try:
        hostname = config["components"]["OpenEphys"]["host"]
    except:
        hostname = "localhost"
    
    server = f"http://{hostname}:37497/api"
    status_endpoint = f"{server}/status"
    recording_endpoint = f"{server}/recording"
    processors_endpoint = f"{server}/processors"

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
    def set_open_ephys_name(path: Optional[str] = None,  prepend_text: Optional[str] = None, append_text: Optional[str] = None):
        recording = requests.get(EphysHTTP.recording_endpoint).json()
        if path:
            recording['current_directory_name'] = path
        if prepend_text:
            recording['prepend_text'] = prepend_text
        if append_text:
            recording['append_text'] = append_text
        return requests.put(EphysHTTP.recording_endpoint, json.dumps(recording))

    @staticmethod
    def clear_open_ephys_name():
        return EphysHTTP.set_data_file_path(path='')

    @staticmethod
    def request_open_ephys_status():
        return EphysHTTP.get_mode()

    @staticmethod
    def reset_open_ephys():
        EphysHTTP.clear_open_ephys_name()
        time.sleep(.5)
        EphysHTTP.start_ecephys_acquisition()
        time.sleep(3)
        EphysHTTP.stop_ecephys_recording()
        time.sleep(.5)
