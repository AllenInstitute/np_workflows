import json

from enum import Enum

import logging

import requests
import time
from . import ephys_edi_pb2 as ephys_messages
from typing import Optional

class EphysRouter:
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


class EphysHTTP:
    server = f"http://localhost:37497/api"
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
        mode_msg = bytes('{"mode":"{0}"}'.format(mode.value), 'utf-8')
        logging.info(f"sending: {mode_msg} --> {EphysHTTP.status_endpoint}")
        return requests.put(EphysHTTP.status_endpoint, mode_msg)

    @staticmethod
    def get_data_file_path():
        # TODO update to get folder name / directory / append as reqd
        return requests.get(EphysHTTP.recording_endpoint).json()['current_directory_name']

    @staticmethod
    def set_data_file_path(path:str, prepend_text: Optional[str] = None, append_text: Optional[str] = None):
        recording = requests.get(EphysHTTP.recording_endpoint).json()
        if prepend_text:
            recording['prepend_text'] = prepend_text
        if append_text:
            recording['append_text'] = append_text
        recording['current_directory_name'] = path
        return requests.put(EphysHTTP.recording_endpoint, json.dumps(recording))

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
    def set_open_ephys_name(path: str,  prepend_text: Optional[str] = None, append_text: Optional[str] = None):
        return EphysHTTP.set_data_file_path(path=path, prepend_text=prepend_text, append_text=append_text)

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