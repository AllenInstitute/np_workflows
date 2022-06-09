import time

from . import ephys_edi_pb2 as ephys_messages

class ephys:
    def __init__(self):
        pass
    
    def start_ecephys_recording():
        return ephys_messages.recording(command=1)

    def stop_ecephys_recording():
        return ephys_messages.recording(command=0)

    def start_ecephys_acquisition():
        return ephys_messages.acquisition(command=1)

    def stop_ecephys_acquisition():
        return ephys_messages.acquisition(command=0)

    def set_open_ephys_name(path):
        return ephys_messages.set_data_file_path(path=path)

    def clear_open_ephys_name():
        return ephys_messages.set_data_file_path(path='')

    def request_open_ephys_status():
        return ephys_messages.request_system_status(path='')

    def reset_open_ephys(io):
        io.write(clear_open_ephys_name())
        time.sleep(.5)
        io.write(start_ecephys_recording())
        time.sleep(3)
        io.write(stop_ecephys_recording())
        time.sleep(.5)

