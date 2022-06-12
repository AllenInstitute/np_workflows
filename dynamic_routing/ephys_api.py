import time
import requests
import json

class ephys:
    
    # TODO check response.status_code == 200 within object
    
    def __init__(self, host=None):
        if host is None:
            self.host = "W10SV108131" # btTest
        else:
            self.host = host
            
        self.http_server = f"http://{self.host}:37497/api/"
        
        # content discovered so far:
        self.http_status = self.http_server + "status"
        self.http_recording = self.http_server + "recording"
        self.http_processors = self.http_server + "processors"

    def get_mode(self):
        
        print(f"request: 'mode' <-- {self.http_status}")
        return requests.get(self.http_status).json()['mode']


    def set_mode(self, msg: str):
        
        modes = ["IDLE","ACQUIRE","RECORD"]
        
        if msg not in modes:
            raise ValueError(f"{msg=}: must be one of {modes=}")
        
        else:
            mode_msg = bytes('{"mode":"' + msg + '"}', 'utf-8')           
            
            print(f"sending: {mode_msg} --> {self.http_status}")
            return requests.put(self.http_status, mode_msg)
        
        
    def get_data_file_path(self):
        # TODO update to get folder name / directory / append as reqd
        return requests.get(self.http_recording).json()['current_directory_name']
        
        
    def set_data_file_path(self,path):
        
        recording = requests.get(self.http_recording).json()
        
        
        sessionID = '0'*10 + "_"
        mouseID = "_" + "366122" 
        recording.update({'prepend_text':sessionID})
        recording.update({'append_text':mouseID})
     
        # TODO update to set folder name / parent directory as reqd
        #! folder name only seems to update when text field is clicked in gui:
        #! does not take effect when starting a new recording
        recording.update({'current_directory_name':path})
        
        return requests.put(self.http_recording, json.dumps(recording))
    
    
def start_ecephys_recording():
    return ephys().set_mode("RECORD")

def stop_ecephys_recording():
    return ephys().set_mode("ACQUIRE")

def start_ecephys_acquisition():
    return ephys().set_mode("ACQUIRE")

def stop_ecephys_acquisition():
    return ephys().set_mode("IDLE")

def set_open_ephys_name(path):
    return ephys().set_data_file_path(path=path)

def clear_open_ephys_name():
    return ephys().set_data_file_path(path='')

def request_open_ephys_status():
    return ephys().get_mode()

def reset_open_ephys():
    clear_open_ephys_name()
    time.sleep(.5)
    start_ecephys_acquisition()
    time.sleep(3)
    stop_ecephys_recording()
    time.sleep(.5)
        


set_open_ephys_name("sessionID_yyyymmdd_mouseID")
start_ecephys_recording()

reset_open_ephys()