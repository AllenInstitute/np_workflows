import argparse
import json
import logging
import os
import sys
from pprint import pformat
from socket import *

from mpetk import mpeconfig

R = {'mvr_request': ''}
encoding = 'utf-8'


class MVRConnector:
    def __init__(self, args=None):
        self._errors_since_last_success: int = 0
        self._delete_on_copy: bool = True
        self._recording: bool = False
        self._args = args
        self._mvr_sock = None
        self._host_to_camera_map = {}
        self._mvr_connected = False
        self.connect_to_mvr()

    def _recv(self):
        if not self._mvr_connected:
            self.connect_to_mvr()
            # logging.warning('Cannot receive because the MVR Reader is not connected to MVR')
            return
        try:
            ret_val = self._mvr_sock.recv(1024)
        except:
            # logging.error('Error receiving response from MVR.  An attempt to connect will be made on the next write.')
            # self._mvr_connected = False
            return []
        else:
            return ret_val
            ret_val = json.loads(ret_val)
            logging.info(f'Receiving: {pformat(ret_val)}')
            return ret_val

    def _send(self, msg):
        """
        msg is a dictionary.
        _send creates json from the dictionary and sends it as a byte object
        """
        try:  # hackneyed attempt to proof the connection on every write b/c there is no read cycle in NP Workflow
            vers = self.get_version()
        except Exception:
            self.connect_to_mvr()

        msg = json.dumps(msg).encode()
        logging.info(f'Sending: {pformat(msg)}')
        if not self._mvr_connected:
            self.connect_to_mvr()
        if not self._mvr_connected:
            return
        self._mvr_sock.send(msg)

    def connect_to_mvr(self):
        """
        Creates a STREAM Socket connection to the MultiVideoRecorder
        """
        self._mvr_sock = socket(AF_INET, SOCK_STREAM)
        self._mvr_sock.settimeout(20.0)
        if self._errors_since_last_success == 0:
            logging.info(f'Connecting to MVR on {self._args["host"]}:{self._args["port"]}', extra={'weblog': True})
        try:
            self._mvr_sock.connect((self._args['host'], self._args['port']))
            self._mvr_connected = True
        except OSError:
            if self._errors_since_last_success == 0:
                logging.error('Failed to connect to MVR.  An attempt to connect will be made on the next write.')
            self._errors_since_last_success += 1
        else:
            self._errors_since_last_success = 0
            logging.info(self._recv())

    def get_version(self):
        msg = {'mvr_request': 'get_version'}
        self._send(msg)
        return self._recv()

    def start_display(self):
        msg = {'mvr_request': 'start_display'}
        self._send(msg)
        return self._recv()

    def stop_display(self):
        msg = {'mvr_request': 'stop_display'}
        self._send(msg)
        return self._recv()

    def start_record(self, file_name_prefix='', sub_folder='.', record_time=4800):
        self._send({"mvr_request": "start_record",
                    "sub_folder": sub_folder,
                    "file_name_prefix": file_name_prefix,
                    "recording_time": record_time,
                    })

    def start_single_record(self, host, file_name_prefix='', sub_folder='.', record_time=4800):
        if host not in self._host_to_camera_map:
            logging.warning(f'Can not find host {host} associated with a camera.')
            return

        self._send({"mvr_request": "start_record",
                    "camera_indices": [{"camera_index": f"Camera {self._host_to_camera_map[host]}"}],
                    "sub_folder": sub_folder,
                    "file_name_prefix": file_name_prefix,
                    "recording_time": record_time,
                    })

    def set_automated_ui(self, state):
        if state:
            message = {"mvr_request": "set_automated_ui"}

        else:
            message = {"mvr_request": "set_unautomated_ui"}

        self._send(message)
        return self._recv()

    def stop_record(self):
        msg = {'mvr_request': 'stop_record'}
        self._send(msg)
        # return self._recv()

    def stop_single_record(self, host):
        if host not in self._host_to_camera_map:
            logging.warning(f'Can not find host {host} associated with a camera.')
            return

        cam_id = self._host_to_camera_map[host]
        message = {"mvr_request": "stop_record",
                   "camera_indices": [f"Camera {cam_id}",
                                      ]
                   }
        self._send(message)

    def define_hosts(self, hosts):
        self._host_to_camera_map = {}
        for idx, host in enumerate(hosts):
            self._host_to_camera_map[host] = idx + 1

    def highlight_camera(self, col, row):
        cam_map = {(0, 0): 1,
                   (0, 1): 2,
                   (0, 2): 3,
                   (1, 0): 4,
                   (1, 1): 5,
                   (1, 2): 6
                   }

        camera = cam_map[(row, col)]
        msg = {'mvr_request': 'toggle_highlight_camera',
               'camera': f'Camera {camera}'
               }
        self._send(msg)
        try:
            x = self._recv()
        except Exception:
            x = None
        # return x

    def unhighlight_camera(self, panel):
        msg = {'mvr_request': 'unhighlight_panel'}
        self._send(msg)
        return self._recv()

    def get_state(self):
        if self._recording:
            return 'BUSY', 'RECORDING'
        else:
            return 'READY', ''

    def shutdown(self):
        self._rep_sock.close()

    def _onclose(self):
        self.shutdown()

    @property
    def platform_info(self):
        return '0.1.0'


# Need this for icons
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath("../..")

    return os.path.join(base_path, relative_path)


def main():
    config = mpeconfig.source_configuration('vmon_shim', version='0.3.3')
    parser = argparse.ArgumentParser(
        description="Stand in proxy to connect to MVR from WSE workflows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", type=str, default=config['mvr_host'], help='Hostname to connect to MVR on')
    parser.add_argument("--port", '-p', type=int, default=config['mvr_port'],
                        help="The port to expect MVR to connect on.")
    parser.add_argument('-v', '--version', action='version', version='0.1.0')
    parser.add_argument("--shim_port", '-s', type=int, default=config['shim_port'], help="The port to run the shim on")
    args = parser.parse_args(sys.argv[1:])


if __name__ == '__main__':
    main()
