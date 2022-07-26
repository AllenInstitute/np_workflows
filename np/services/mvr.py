import argparse
import json
import logging
from mpetk import mpeconfig
import sys
from pprint import pprint, pformat
from socket import *
import os

R = {'mvr_request': ''}
encoding = 'utf-8'


class ResponseBuffer:
    def __init__(self):
        self.read_buffer = []

    def parse_buffer(self, buf):
        if not buf:
            return []
        buf = buf.decode()
        read_bracket_count = 0
        self.read_buffer.extend(buf)
        count = 0
        messages = []
        for i, c in enumerate(self.read_buffer):  # should maintain an internal pointer so it doesn't redo the list
            count += 1
            # update the "bracket stack"
            if c == '{':
                read_bracket_count += 1
            elif c == '}':
                read_bracket_count -= 1

            if read_bracket_count == 0:  # a full JSON string is available
                try:

                    messages.append(json.loads(''.join(self.read_buffer[i - count + 1: i + 1])))
                except TypeError:
                    logging.warning('Error parsing MVR message:', self.read_buffer)
                count = 0

        # strip prior json messages off the buffer
        if count == 0 and read_bracket_count == 0:  # There must be a better way
            self.read_buffer = []
        else:
            self.read_buffer = self.read_buffer[-count:]

        return messages


class MVRConnector:
    def __init__(self, args=None):
        self.response_buffer = ResponseBuffer()
        self.device_index_map = {}
        self._errors_since_last_success: int = 0
        self._delete_on_copy: bool = True
        self._recording: bool = False
        self._args = args
        self._mvr_sock = None
        self._host_to_camera_map = {}
        self._mvr_connected = False
        self.comp_ids = []  # temporary to allow for some debugging
        self.output_dir = "c$/ProgramData/AIBS_MPE/MVR/data/"
        try:
            self.connect_to_mvr()
            logging.info("Connected to mvr")
        except Exception as err:
            logging.error(f'failed to connect to mvr:{err}')
            exit()

    def _recv(self):
        if not self._mvr_connected:
            self.connect_to_mvr()
            return

        try:
            ret_val = self._mvr_sock.recv(1024)
        except ConnectionResetError as e:
            # logging.error('Error receiving response from MVR.  An attempt to connect will be made on the next write.')
            logging.info('MVR Connection Reset Error')
            self._mvr_connected = False
            return []
        except:
            return []
        return ret_val

    def read(self):
        buf = self._recv()
        return self.response_buffer.parse_buffer(buf)

    def _send(self, msg):
        """
        msg is a dictionary.
        _send creates json from the dictionary and sends it as a byte object
        """
        msg = json.dumps(msg).encode()
        logging.info(f'Sending: {pformat(msg)}')
        if not self._mvr_connected:
            logging.info("Connecting to MVR")
            self.connect_to_mvr()
        if not self._mvr_connected:
            return
        try:
            self._mvr_sock.send(msg)
        except (ConnectionResetError, ConnectionRefusedError):
            self._mvr_connected = False

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
            logging.info(self.read())

    def get_version(self):
        msg = {'mvr_request': 'get_version'}
        self._send(msg)
        return self.read()

    def start_display(self):
        msg = {'mvr_request': 'start_display'}
        self._send(msg)
        return self.read()

    def stop_display(self):
        msg = {'mvr_request': 'stop_display'}
        self._send(msg)
        return self.read()

    def start_record(self, file_name_prefix='', sub_folder='.', record_time=4800):
        self._send({"mvr_request": "start_record",
                    "sub_folder": sub_folder,
                    "file_name_prefix": file_name_prefix,
                    "recording_time": record_time,
                    })

    def start_single_record(self, host, file_name_prefix='', sub_folder='.', record_time=4800):
        print(f'start single record on {host}')
        if host not in self._host_to_camera_map:
            comp = self.host_to_comp['host']
            logging.warning(f'Start Single Record: Can not find host {host} ({comp}) associated with a camera.')
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
        return self.read()

    def stop_record(self):
        msg = {'mvr_request': 'stop_record'}
        self._send(msg)
        # return self.read()

    def stop_single_record(self, host):
        if host not in self._host_to_camera_map:
            comp = self.host_to_comp['host']
            logging.warning(f'Stop Single Record: Can not find host {host} ({comp}) associated with a camera.')
            return

        cam_id = self._host_to_camera_map[host]
        message = {"mvr_request": "stop_record",
                   "camera_indices": [f"Camera {cam_id}",
                                      ]
                   }
        self._send(message)

    def take_snapshot(self):
        self._send({"mvr_request": "take_snapshot"})

    def define_hosts(self, hosts):
        self._host_to_camera_map = {}
        for idx, host in enumerate(hosts):
            self._host_to_camera_map[host] = idx + 1

        # print(f'Host To Camera Map: {pformat(self._host_to_camera_map)}')
        self.host_to_comp = list(zip(hosts, self.comp_ids))

    def request_camera_ids(self):
        self._send({"mvr_request": "get_camera_ids"})
        return self.read()
    
    def highlight_camera(self, device_name):
        print(self.device_index_map)
        index = self.device_index_map[device_name]
        msg = {'mvr_request': 'toggle_highlight_camera',
               'camera': index
               }
        self._send(msg)

    def unhighlight_camera(self, panel):
        msg = {'mvr_request': 'unhighlight_panel'}
        self._send(msg)
        return self.read()

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
        base_path = os.path.abspath(".")

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