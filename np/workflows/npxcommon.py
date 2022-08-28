import glob
import json
import logging
import os
import pathlib
import pickle
import re
import shutil
import subprocess
import time
import traceback
from collections import namedtuple
from datetime import date as date
from datetime import datetime as dt
from datetime import timedelta as timedelta
from math import floor
from shutil import copyfile, disk_usage
from typing import List, Union

import numpy
import psutil
import yaml
import zmq
from np.models.model import \
    Model  # this is the type for concrete experiment models below
from np.models.model import (  # these are the currently supported exps
    Behavior, DynamicRouting, Passive)
# sys.path.append("..")
from np.services import \
    ephys_edi_pb2 as \
    ephys_messages  # ! TODO remove this - communicate through API instead
from np.services.config import Rig
from np.services.ephys_api import EphysHTTP, EphysRouter
from np.services.mtrain import MTrain
from np.services.mvr import MVRConnector
from PIL import Image
from wfltk import middleware_messages_pb2 as wfltk_msgs

messages = wfltk_msgs
from mpetk import limstk, mpeconfig, zro
from mpetk.aibsmw.routerio.router import ZMQHandler
from mpetk.zro import Proxy

# The first thing that should be done in the .py file is to set the experiment variable to an instance of the model classes
global experiment
experiment: Model = None

global config
config: dict = None

global io
io: ZMQHandler = None

def get_config() -> dict:
    if not experiment:
        raise ValueError("Experiment model is not set")    

    global config
    config = mpeconfig.source_configuration(
        project_name=experiment.mpe_config.project_name,
        version=experiment.mpe_config.version)

    if Rig.ID == 'NP.0' and isinstance(experiment, Passive):
        local_config = "neuropixels_np0_passive"
    elif Rig.ID == 'NP.0' and isinstance(experiment, Behavior):
        local_config = "neuropixels_np0_behavior"
    elif Rig.ID == 'NP.1':
        local_config = "neuropixels_np1"
    elif Rig.ID == 'NP.2' and isinstance(experiment, Passive):
        local_config = "neuropixels_np2_passive"
        
    with open(f"np/config/{local_config}.yml") as f:   
        yaml_config = yaml.safe_load(f)
    config.update(yaml_config)  
    config["serialized_states_folder"] = ""
    
    return config
        # # pdb.set_trace()
        # def jsonrep(o):
        #     if isinstance(o, datetime.datetime):
        #         return o.__repr__()
            
        # with open('np/config/dump.json','w') as f:
        #     json.dump(config, f, default=jsonrep, indent = 4)
            
    
global mvr_writer
try:

    mvr_writer = MVRConnector(args={"host": Rig.Mon.host, "port": 50000})
    
    if not mvr_writer._mvr_connected:
        raise ConnectionError
        
    # set MVR to record video from all cams except Aux (currently Eye, Face, Behavior)
    mvr_response = mvr_writer.request_camera_ids()[0]
    mvr_writer.exp_cam_ids = [x['id'] for x in mvr_response['value'] if not re.search('aux', x['label'], re.IGNORECASE)]
    mvr_writer.exp_cam_labels = [x['label'] for x in mvr_response['value'] if not re.search('aux', x['label'], re.IGNORECASE)]
    mvr_writer.define_hosts(mvr_writer.exp_cam_ids)
    
except Exception:
    print("Failed to connect to mvr")
    logging.info("Failed to connect to mvr")
    # component_errors.append(f"Failed to connect to MVR.")
        
global_processes = {}



# ---------------- Network Service Objects ----------------

mouse_director_proxy = None



def interlace(left, right, stereo):
    lefta = numpy.asarray(Image.open(left))
    righta = numpy.asarray(Image.open(right))
    stereoa = numpy.copy(lefta)
    count = 0

    for row in righta:
        if count % 2 == 0:
            stereoa[count, :, :] = row
        count += 1
    stereoi = Image.fromarray(stereoa)
    stereoi.save(stereo)


def handle_message(message_id, message, timestamp, io):
    print(f'{timestamp}: Received message {message_id} from router')
    print(message)


def default_exit(state_globals, label):
    """
    Exit function for state initialize
    """
    # print(f'{label}_exit <<')
    pass


def default_enter(state_globals, label):
    """
    Exit function for state initialize
    """
    # print(f'>> {label}_enter')
    pass


def default_input(state_globals, label):
    # print(f'>> {label}_input <<')
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def make_values_strings(dict_in):
    for key, value in dict_in.items():
        dict_in[key] = str(value)


def make_keys_and_values_strings(dict_in):
    new_dict = {}
    for key, value in dict_in.items():
        new_dict[str(key)] = str(value)
    return new_dict

def take_left_snapshot(state_globals, photo_path="C:/ProgramData/AIBS_MPE/wfltk/temp/left_snapshot.jpg"):
    if Rig.ID == "NP.0":
        state_globals["component_proxies"]["Cam3d"].save_left_image(photo_path)
    else: 
        mvr_capture(photo_path) # this returns error msgs, but cam3d proxy doesn't 
        
def take_right_snapshot(state_globals, photo_path="C:/ProgramData/AIBS_MPE/wfltk/temp/right_snapshot.jpg"):
    if Rig.ID == "NP.0":
        state_globals["component_proxies"]["Cam3d"].save_right_image(photo_path)
    else: 
        mvr_capture(photo_path) #  this returns error msgs, but cam3d proxy doesn't 

def mvr_capture(photo_path="C:/ProgramData/AIBS_MPE/wfltk/temp/last_snapshot.jpg", timeout=30):
    """standard mvr image snapshot func, returning error mesg or img  """
    mvr_writer.take_snapshot()
    
    def wait_on_snapshot():
        return_msg = "snapshot timed out"
        t0 = time.time()
        while time.time()-t0 < timeout:
            try:
                for message in mvr_writer.read():
                    if message.get('mvr_broadcast', "") == "snapshot_converted":
                        drive, filepath = os.path.splitdrive(message['snapshot_filepath'])
                        source_photo_path = os.path.join(Rig.Mon.path,f"{drive[0]}${filepath}")
                        # MVR has responded too quickly.  It hasn't let go of the file so we must wait.
                        time.sleep(1)
                        pathlib.Path(photo_path).parent.mkdir(parents=True, exist_ok=True)
                        dest_photo_path = shutil.copy(source_photo_path, photo_path)
                        logging.info(f"Copied: {source_photo_path} -> {dest_photo_path}")
                        return True, dest_photo_path
                    elif message.get('mvr_broadcast', "") == "snapshot_failed":
                        return False, message['error_message'] or "snapshot failed"
            except Exception as e:
                return_msg = e
        return False, return_msg
    
    success, mesg_or_img = wait_on_snapshot()
    if not success:
        print(f"Error taking snapshot: {mesg_or_img}")
    else:
        return mesg_or_img  # return the captured image
  
    
def save_state(state_globals,state_transition_function):
    if state_globals['external']['next_state'] \
        and not state_globals['external']['next_state'] == ''\
        :
        print('>> save_state <<')
        state_name = '_'.join(state_transition_function.__name__.split('_')[0:-1])
        if state_name == 'default':
            return None        
        state_folder = config.get('serialized_states_folder',"C:/ProgramData/AIBS_MPE/wfltk/resume")
        os.makedirs(state_folder, exist_ok=True, parents=True)
        
        with open(f'{state_folder}/{time.strftime("%H-%M-%S",time.localtime())}_{state_name}.pkl', 'wb') as f:
            x = [{k:state_globals[k]} for k in state_globals.keys() if k not in ['resources','component_proxies']]        
            pickle.dump(x, f)

def find_prior_states():
    state_folder = config['serialized_states_folder']   
    if os.path.exists(state_folder):
        return glob.glob(f'{state_folder}/*.pkl')
    else:
        return None
    
def load_prior_state_enter(state):
    print('>> load_prior_state_enter <<')
    next_state_default = state['external']['next_state']
    print(f'next state on enter {next_state_default}')
    previous_states = find_prior_states()
    if previous_states:
        state['external']['prior_states'] = previous_states
    else: 
        state['external']['prior_states'] = ['-- none available --']
    
def load_prior_state_input(state):
    print('>> load_prior_state_input <<')
    next_state_default = state['external']['next_state']
    print(f'next state on input {next_state_default}')
    if state['external'].get('load_prior_state', False):
        next_state = state['external']['prior_state_selected']
        with open(next_state, "rb") as f:
            loaded_state = pickle.load(f)
        # pdb.set_trace()
        for s in loaded_state:
            if s:
                state.update(s)
        print(f"next state on exit {state['external']['next_state']}")
        
                
    else:
        #  I think you may want to remove all the files here ...
        for file in glob.glob(f'{config["serialized_states_folder"]}/*.pkl'):
            os.unlink(file)
        state['external']['next_state'] = next_state_default
        print(next_state_default)   
        state['external']['next_state'] = 'scan_mouse_id' #TODO update

      


def initialize_enter(state_globals):
    """
    Entry function for state initialize
    """




    # logging.start_stop('Starting neuropixels project code', extra={'weblog': True})
    print('>> Starting neuropixels code <<')

    state_globals["external"]["workflow_start_time"] = dt.now().strftime('%Y%m%d%H%M%S')

    compStatusArray = {}
    state_globals["external"]["component_status"] = {}
    state_globals["external"]["water_calibration_heights"] = []
    state_globals["external"]["water_calibration_volumes"] = []
    state_globals["external"]["exp_monitor_time"] = 10
    state_globals['external']['foraging_id'] = ''
    state_globals['external']['foraging_id_list'] = []
    state_globals['external']['high_zoom_level'] = get_from_config(['high_zoom_level'], default=4)
    state_globals['external']['low_zoom_level'] = get_from_config(['high_zoom_level'], default=2.5)
    state_globals['external']['colon'] = ':'
    state_globals['external']['backup_location'] = config['backup_location']
    eye_default = "Eye dichroic in place and adjusted so that the eye is centered?"
    state_globals['external']['eye_dichroic_string'] = get_from_config(['eye_dichroic_string'], default=eye_default)
    cleanup_str_default = "Empty the poop tray"
    state_globals['external']['cleanup_str'] = get_from_config(['cleanup_str'], default=cleanup_str_default)

    pre_exp_qc_string_default = "Pay attention to the running wheel plot for encoder failure, and behavior plot for signs of poor lickspout and wheel positioning"
    state_globals['external']['pre_exp_qc_string'] = get_from_config(['pre_exp_qc_string'],
                                                                     default=pre_exp_qc_string_default)

    state_globals['external']['lowering_distance'] = get_from_config(['lowering_distance'], default=r"200um/min")
    state_globals['external']['lowering_speed'] = get_from_config(['lowering_speed'], default='1000um')

    # key = 'Post Processing Validation Agent'
    # if not (key in config['components']):
    #     config['components'][key] = {'desc': key, 'host': 'localhost', 'port': 1234, 'version': '0.1.0'}
    global ephys 
    if Rig.ID == "NP.0":
        ephys = EphysRouter
        # add proxy for EphysRouter - not used in EphysHTTP
        ephys.setup_proxy(state_globals['resources']['io'])
    else:
        ephys = EphysHTTP

    
    establish_proxies(state_globals)

    global mouse_director_proxy
    service = config['components']['MouseDirector'] # new
    md_host = service['host']
    md_port = service['port']
    print(f'connecting to MD on {md_port}')
    mouse_director_proxy = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'], serialization='json')
    logging.info(f'MouseDirector Uptime: {mouse_director_proxy.uptime}')

    mvr_connected = False
    try:
        message = mvr_writer.get_version() #! not working: 
        # Version check error:'<' not supported between instances of 'NoneType' and 'str'

        print(f'connection message: {message}')
        mvr_connected = True
    except Exception as E:
        try:
            mvr_writer.connect_to_mvr()
            message = mvr_writer.get_version()
            print(f'connection message: {message}')
            mvr_connected = True
        except Exception as E:
            mvr_connected = False
            
    state_globals['external']['component_status']['MVR'] = mvr_connected

    # set up the neuropixel data location
    data_location = f'{config["windows_install_paths"]["install"]}_data'
    # make sure the data_location exist...if not, create them
    if not os.path.exists(data_location):
        os.makedirs(data_location)

    state_globals['external']['data_location'] = data_location
    state_globals['external']['probe_list'] = config['probe_slots']
    make_values_strings(state_globals['external']['probe_list'])

    print('config:' + str(state_globals['external']['probe_list']))
    state_globals['external']['PXI'] = make_keys_and_values_strings(config['slot_drives'])
    print('PXI')
    print('config: ' + str(state_globals['external']['PXI']))
    slots = set(state_globals['external']['probe_list'].values())
    reverse_mapping = {slot: [] for slot in slots}
    for probe, slot in state_globals['external']['probe_list'].items():
        reverse_mapping[slot].append(probe)
    state_globals['external']['reverse_mapping'] = reverse_mapping
    probes_in_slot_strings = {slot: 'probe' + ''.join(probe_list) for slot, probe_list in reverse_mapping.items()}
    state_globals['external']['probes_in_slot'] = probes_in_slot_strings
    print('probes in slot')
    print('config: ' + str(state_globals['external']['probes_in_slot']))

    for slot, drive in state_globals['external']['PXI'].items():
        state_globals['external']['slot_' + slot + '_drive'] = drive

    # set the lims locations based on what's in the config
    state_globals['external']['trigger_dir'] = config['default_paths']['trigger']
    state_globals['external']['lims_location'] = config['default_paths']['incoming']

    # look for the hardware configuration to be added to the platform json file
    state_globals['hardware_config'] = config['hardware']

    # get the location of the external open ephys drives
    state_globals['openephys_drives'] = config['openephys_drives']
    print('config: ' + str(state_globals['openephys_drives']))

    local_lims_head = config['mapped_lims_location']
    os.makedirs(local_lims_head, exist_ok=True)
    state_globals["external"]["local_lims_head"] = local_lims_head

    # get the AIBS ID, if set.  Will be used in the platform json file
    state_globals['external']['rig_id'] = os.environ.get("AIBS_RIG_ID", os.environ.get("COMPUTERNAME", "TEST_RIG"))

    state_globals["external"]["Manual.Project.Code"] = "None"
    state_globals["external"]["Manual.Date.String"] = "None"
    ExperimentNotes = {'BleedingOnInsertion': {}, 'BleedingOnRemoval': {}}
    state_globals['external']['ExperimentNotes'] = ExperimentNotes

    # initialize some variables
    for probe in state_globals['external']['probe_list']:
        key = 'probe_' + probe + '_DiI_depth'
        state_globals["external"][key] = '6000'
        # TODO would be great if we had this read the file we are going to produce from the targeting to read in the maximums
        # might get confusing because those aren't hard limits if we hit the brain deeper
    


    global mtrain
    mtrain = MTrain()
    # set the mouse ID from the workflow py
    if not mtrain.connected():
        print("Failed to connect to MTrain")
    else:
        print("MTrain connected")
    
    # initialize some choice fields
    # state_globals['external']['components_run'] = True
    print('Done with initialize_enter')

def initialize_input(state_globals):
    try:
        mouse_director_proxy.set_user_id(state_globals["external"]["user_id"])
    except Exception:
        alert_string = f'Failed to communicate with MouseDirector'
        alert_text(alert_string, state_globals)  # Todo put this back
        print('########################################################')
        logging.debug(alert_string, exc_info=True)


def assess_previous_sessions(state_globals):
    state_globals['external']['exp_sessions'] = {}
    state_globals['external']['hab_sessions'] = {}
    state_globals['external']['all_sessions'] = {}
    state_globals['external']['mouse_sessions'] = {}

    exp_sessions = {}
    try:
        exp_backup = get_backup_location('exp')
        for exp_session in os.listdir(exp_backup):
            full_path = os.path.join(exp_backup, exp_session)
            exp_sessions[exp_session] = full_path
    except Exception as E:
        message = "Unable to read past exp sessions"
        alert_text(message, state_globals)

    hab_sessions = {}
    try:
        hab_backup = get_backup_location('hab')
        for hab_session in os.listdir(hab_backup):
            full_path = os.path.join(hab_backup, hab_session)
            hab_sessions[hab_session] = full_path
    except Exception as E:
        message = "Unable to read past hab sessions"
        alert_text(message, state_globals)

    all_sessions = exp_sessions.copy()
    all_sessions.update(hab_sessions)

    mouse_sessions = {}
    mouse_num = '_' + state_globals['external']['mouse_id'] + '_'
    for session, path in all_sessions.items():
        if mouse_num in session:
            mouse_sessions[session] = path

    state_globals['external']['exp_sessions'] = exp_sessions
    state_globals['external']['hab_sessions'] = hab_sessions
    state_globals['external']['all_sessions'] = all_sessions
    state_globals['external']['mouse_sessions'] = mouse_sessions

    try:
        last_exp_date, path = get_most_recent_session(exp_sessions, state_globals)
        last_exp_int = int(last_exp_date)
        current_date_int = int(dt.now().strftime("%Y%m%d%H%M%S"))
        if last_exp_int == current_date_int - 1:
            state_globals['external']['probes_need_cleaning'] = True
    except Exception as E:
        message = "Unable to determine if probes need to be cleaned"
        logging.debug(message, exc_info=True)
        alert_text(message, state_globals)

    try:
        last_mouse_date, path = get_most_recent_session(mouse_sessions, state_globals)
        for file in os.listdir(path):
            if '_platformD1.json' in file:
                fullpath = os.path.join(path, file)
                with open(fullpath, 'r') as f:
                    platform_json = json.load(f)
                if 'wheel_height' in platform_json:
                    state_globals['external']['wheel_height'] = platform_json['wheel_height']
                if 'mouse_weight_pre' in platform_json:
                    state_globals['external']['previous_mouse_weight_pre'] = platform_json['mouse_weight_pre']
                if 'water_calibration_warning' in platform_json:
                    state_globals['external']['water_calibration_warning'] = platform_json['water_calibration_warning']
    except Exception as E:
        message = "A failure occurred atempting to load the wheel height or mouse weight from the last session"
        alert_text(message, state_globals)
        logging.debug(message, exc_info=True)
    save_platform_json(state_globals, manifest=False)


def get_created_timestamp_from_file(file, date_format='%Y%m%d%H%M'):

    t = os.path.getctime(str(file))
    # t = os.path.getmtime(file)
    t = time.localtime(t)
    t = time.strftime(date_format, t)

    return t


def get_newest_mvr_img(host):
    img_output_dir = pathlib.Path(f"//{host}/c/ProgramData/AIBS_MPE/mvr/data")
    paths_all = pathlib.Path(img_output_dir).glob('*.jpg')
    time_created = 0
    for path in paths_all:
        if time_created < int(get_created_timestamp_from_file(path)):
            newest_file = path
    return str(newest_file)
        
        
        


    
def get_most_recent_session(session_dict, state_globals):
    exp_dates = {}
    last_date = 0
    path = None
    try:
        for session, path in session_dict.items():
            if len(session.split('_')) == 3:
                lims_id, mouse_num, date = session.split('_')
                if len(date) == 8 and date.isdigit() and len(mouse_num) == 6 and mouse_num.isdigit():
                    exp_dates[date] = path
                    print(exp_dates)
        last_dates = list(exp_dates.keys())
        last_dates.sort()
        last_date = last_dates[-1]
        path = exp_dates[last_date]
    except Exception as E:
        message = "Error getting recent session session"
        alert_text(message, state_globals)
        logging.debug(message, exc_info=True)
    return last_date, path
    # this could be actually useful if we wantto put in QC reminders at some point....


def count_sessions(session_dict, mouse_num):
    count = 0
    try:
        sessions_list = list(session_dict)

        mouse_num = '_'+mouse_num+'_'
        for session in sessions_list:
            if mouse_num in session:
                count += 1
    except Exception as E:
        message = "Error coutning past sessions"
        alert_text(message, state_globals)
    return count


def handle_2_choice_button(choice_field, choice_state, default_state, state_globals):
    if choice_field in state_globals['external'] and state_globals['external'][choice_field]:
        print('go back to ' + choice_state)
        state_globals['external']['next_state'] = choice_state
        state_globals['external'].pop(choice_field, None)
    else:
        state_globals['external']['next_state'] = default_state
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def components_error_input(state_globals, next_state):
    print('>> components_error_input <<')
    handle_2_choice_button('components_retry', 'initialize', next_state, state_globals)


def prepare_for_pretest_input(state_globals):
    state_globals['external']['clear_sticky'] = True


def pretest_input(state_globals):
    print('>> pretest_error_input <<')
    # message = ephys_messages.acquisition(command=0)
    # state_globals['resources']['io'].write(message)

    # handle_2_choice_button('pretest_failed', 'pretest_error', 'configure_hardware_videomon', state_globals)


def start_ecephys_recording(state_globals):
    print('Attempting to start ecephys acquisiton')
    ephys.start_ecephys_recording()
    # send_ecephys_message(state_globals, 'recording', command=1)
    # time.sleep(15) #- the process can take this long but its annoying to have the WSE wait...


def stop_ecephys_recording(state_globals):
    ephys.stop_ecephys_recording()
    # send_ecephys_message(state_globals, 'recording', command=0)
    # print('Attempting to stop ecephys acquisiton')

def start_ecephys_acquisition(state_globals):
    print('Attempting to start ecephys acquisiton')
    return ephys.start_ecephys_acquisition()
    # ephys.()message 
    # = send_ecephys_message(state_globals, 'acquisition', command=1)
    # time.sleep(15) # - the process can take this long but its annoying to have the WSE wait...

def stop_ecephys_acquisition(state_globals):
    print('Attempting to stop ecephys acquisiton')
    ephys.stop_ecephys_acquisition()
    # send_ecephys_message(state_globals, 'acquisition', command=0)
        


def set_open_ephys_name(state_globals, add_prefix:str=''):
    try:
        print('Attempting to set openephys session name to ' + str(state_globals["external"]["session_name"]))
        
        # TODO shift naming to workflow, and consider using path = session_name, instead of prepend_base_append to avoid adding '_' sep
        folder_str = state_globals["external"]["session_name"]
        # mouseID = state_globals["external"]["mouse_id"]
        # sessionID = state_globals["external"]["ecephys_session_id"] 
        # date = state_globals["external"]["sessionNameTimestamp"]
        add_prefix = add_prefix + '_' if add_prefix else ''
        path = f"{add_prefix}{folder_str}"
        ephys.set_open_ephys_name(path=path)
        # send_ecephys_message(state_globals, 'set_data_file_path', path=state_globals["external"]["session_name"])
            

    except Exception as E:
        print(f'Failed to set open ephys name: {E}')


def clear_open_ephys_name(state_globals):
    try:
        print('Attempting to clear openephys session name')
        ephys.clear_open_ephys_name()
        # send_ecephys_message(state_globals, 'set_data_file_path', path='')
            
    except Exception as E:
        print(f'Failed to set open ephys name: {E}')


def request_open_ephys_status(state_globals):
    try:
        print('checking open ephys status')
        # if isinstance(ephys, EphysHTTP):
        import pdb; pdb.set_trace()
        message = ephys.request_open_ephys_status()
        # else:
            # message = send_ecephys_message(state_globals, 'REQUEST_SYSTEM_STATUS', path='')
        return message
    except Exception as E:
        print(f'Failed to get open ephys status')


def send_ecephys_message(state_globals, message_type, **kwargs):
    message = None
    try:
        try:
            try:
                method_to_call = getattr(ephys_messages, message_type)
                if 'path' in kwargs:
                    message = method_to_call(path=kwargs['path'])
                elif 'command' in kwargs:
                    message = method_to_call(command=kwargs['command'])
            except Exception as e:
                print(f'Failed to generate openephys message:{e}!')
                state_globals['external']['status_message'] = f'OpenEphys proxy failure:{e}'
                state_globals['external']['component_status']["OpenEphys"] = False
            state_globals['resources']['io'].write(message)
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            state_globals['external']['status_message'] = f'OpenEphys acquisition stop failure:{e}'
            state_globals['external']['component_status']["OpenEphys"] = False
    except Exception as e:
        print(f'OpenEphys proxy failure:{e}!')
        state_globals['external']['status_message'] = f'OpenEphys proxy failure:{e}'
        state_globals['external']['component_status']["OpenEphys"] = False
    return message


def pretest_error_input(state_globals):
    print('>> pretest_error_input <<')
    handle_2_choice_button('pretest_override', 'configure_hardware_videomon', 'start_pretest', state_globals)

def circshift_to_item(items: List, item: Union[str, int]):
    """Circshift items to put specified item at index 0.
    If item isn't found, return items unchanged."""
    if item in items:
        return items[items.index(item):] + items[:items.index(item)]
    return items


def dir_failed_message(probeDir):
    message = f"""Failed to find data directory {probeDir}\n
        Click restart connection in open ephys and try again.\n
        If Open ephys recorded briefly and created the directory as expected,\n
        then it is possible the recording drive needs to be shared.\n
    """
    return message


def no_space_message(probeDir):
    message = f"""Not enough space for data directory {probeDir}\n
        Please delete other files that have already been backed up to make some space\n
    """
    return message


def no_settings(probeDir):
    message = f"""Failed to find settings file in {probeDir}\n
        Is the destination location shared so the WSE has write permissions?\n
        You may have to move it manually from the \n
    """
    return message


def no_copy_settings(settings_path):
    message = f"""Failed to move the settings file from {settings_path}\n
        Is the source location shared so the WSE has read permissinos?\n
        You may have to move it manually to the recording directory\n
    """
    return message


def no_found_settings(settings_path):
    message = f"""Failed to find the settings file at {settings_path}\n
        Is the source path correct?\n
         If not you will have to move it manually to the recording directory\n
        If so is the source location shared so the WSE has read permissinos?
    """
    return message


def check_data(probeDir, state_globals, label, settings_path=None):
    if settings_path is None:
        settings_path = os.path.join(probeDir, 'settings.xml')
    probe_drive, tail = os.path.split(probeDir)
    failed = {}
    try:
        if not (os.path.isdir(probeDir)):
            failed[f'{label} data dir_found'] = dir_failed_message(probeDir)
        space = disk_usage(probe_drive).free
        needed = 600 * (10 ** 9)
        if space < needed:
            failed[f'{label} disk space_large enough'] = no_space_message(probeDir)
            # settings_path = os.path.join(computer, 'C','Users','svc_neuropix','Documents','GitHub','plugin-GUI','Builds','VisualStudio2013','x64','Release64','bin',state_globals["external"]["session_name"],'settings*.xml')
        try:
            settings_path = glob.glob(settings_path)[0]
        except IndexError as E:
            failed[f'{label} settings_found'] = no_settings(settings_path)
    except Exception as E:
        message = str('Failed to check probe dir' + probeDir)
        failed[f'{label}_checked'] = message
        print('______________________________________________________________________________')
        logging.debug(message)
        print('______________________________________________________________________________')
    return failed


def check_data_drives(state_globals):
    drive_list = []
    failed = {}
    state_globals['dummy_recordings'] = {}
    if not state_globals['external']['PXI']:
        for probe in state_globals['external']['probe_list']:
            if state_globals['external'][f'probe_{probe}_surface']:
                probeDir = f'{state_globals["openephys_drives"][probe]}/{state_globals["external"]["session_name"]}_probe{probe}'
                data_failed = check_data(probeDir)
                failed.update(data_failed)
    else:
        failed = {}
        for slot, drive in state_globals['external']['PXI'].items():
            label = state_globals['external']['probes_in_slot'][slot]
            probeDir, computer = get_probeDir(state_globals, slot, drive)
            #           settings_path = os.path.join(computer, r"C\users\svc_neuropix\documents\GitHub\plugin-GUI\Builds\VisualStudio2013\x64\release64\bin",state_globals["external"]["session_name"], 'settings*.xml')
            settings_path = get_settings_path(state_globals)

            try:

                # TODO ^open ephys settings path needs to be in config
                data_failed = check_data(probeDir, state_globals, label)
                # failed.update(data_failed)
                found_key = f'{label} data dir_found'
                if not (found_key in data_failed) and (
                    f'{label} settings_found' in data_failed):  # only atempt to copy settings if the data dir exists
                    try:
                        print(f'Globbing for settings at {settings_path}')
                        settings_path = glob.glob(settings_path)[0]
                        state_globals['dummy_recordings'][slot] = os.listdir(probeDir)
                        print(os.listdir(probeDir))
                        no_copy = False
                        new_settings_path = os.path.join(probeDir, 'settings.xml')
                        if not (os.path.exists(settings_path)):
                            failed[f'settings file_in openephys dir'] = no_found_settings(settings_path)
                        copyfile(settings_path, new_settings_path)
                    except Exception as E:
                        no_copy = True
                        failed[f'copy settings {label}_sucessful'] = no_copy_settings(settings_path)
                        logging.debug(E)
                data_failed = check_data(probeDir, state_globals, label)
                failed.update(data_failed)
            except Exception as E:
                message = f'Failed to check data for slot {slot}'
                failed[slot + '_checked'] = message
    if not (failed):
        print('All data drives passed space, directory name and settings check')
    return failed


def get_settings_path(state_globals):
    slot, drive = list(state_globals['external']['PXI'].items())[0]
    probeDir, computer = get_probeDir(state_globals, slot, drive)
    data_dirname = os.path.split(probeDir)[1]
    settings_path = False
    try:
        settings_path = os.path.join(computer, config['open-ephys']['settings_dir'], data_dirname + '*',
                                     'settings*.xml')  # r"C\users\svc_neuropix\documents\GitHub\open-ephys"
        settings_path = glob.glob(settings_path)[0]
    except Exception as E:
        dir_path = r"C:\Users\svc_neuropix\Documents\GitHub\NP_pipeline_validation\validation_params"
        default = os.path.join(computer, r"C\progra~1\Open Ephys", data_dirname + '*',
                                 'settings.xml')
        if settings_path:
            message = f'Failed to find settings file at path from config: {settings_path}\n\n Using default {default} instead'
            # alert_text(message, state_globals)
            print(message)
        settings_path = default
    return settings_path


def get_probeDir(state_globals, slot, drive):
    a_probe = list(state_globals['external']['probe_list'].keys())[0]
    # slot = state_globals['external']['probe_list'][a_probe]
    # drive = state_globals['external']['PXI'][a_probe]
    # computer, x = os.path.split(state_globals["openephys_drives"][a_probe])
    # computer = r"\\" + computer.split(r'/')[2]
    # print('computer:' + computer + ', tail:' + x)
    computer = Rig.ACQ.path
    try:
        probeDirs = glob.glob(os.path.join(computer, drive, state_globals["external"]["session_name"] + '*'))
        if len(probeDirs) > 1:
            message = 'There are multiple possible data directories on the daq. please delete the false ones.'
            if not (message in state_globals['external']['message_text']):
                alert_text(message, state_globals)
        probeDir = probeDirs[-1]
    except Exception as E:
        probeDir = os.path.join(computer, drive, state_globals["external"]["session_name"])
    return probeDir, computer


def delete_dummy_recording(state_globals):
    print('Attempting to delete dummy recordings')
    for slot, drive in state_globals['external']['PXI'].items():
        try:
            probeDir, computer = get_probeDir(state_globals, slot, drive)
            print(str(state_globals['dummy_recordings'][slot]))
            #           settings_path = os.path.join(computer, r"C\users\svc_neuropix\documents\GitHub\plugin-GUI\Builds\VisualStudio2013\x64\release64\bin",state_globals["external"]["session_name"], 'settings*.xml')
            for recording in state_globals['dummy_recordings'][slot]:
                fullpath = os.path.join(probeDir, recording)
                not_setting = not ('setting' in recording)
                small_file = int(os.path.getsize(fullpath)) < (150 * (10 ** 6))
                print(int(os.path.getsize(fullpath)))
                print(150 * (10 ** 6))
                print(not_setting)
                print(small_file)
                print(fullpath)
                if not_setting and small_file:
                    os.remove(fullpath)
        except Exception as E:
            message = f'Failed to delete dummy recordings for slor {slot}'
            alert_text(message, state_globals)
            print_error(state_globals, E)


def reset_open_ephys(state_globals):
    clear_open_ephys_name(state_globals)
    time.sleep(.5)
    start_ecephys_recording(state_globals)
    time.sleep(3)
    stop_ecephys_recording(state_globals)
    time.sleep(.5)
    # stop_ecephys_acquisition(state_globals)


def remove_mouse_input(state_globals):
    """
    Input function for state remove_mouse
    """
    # recreate the proxy
    state_globals["external"]["HeadFrameExitTime"] = dt.now().strftime("%Y%m%d%H%M%S")

    try:
        mouse_weight = float(state_globals['external']['mouse_weight_post'])
        print(f'Attempting to send mouse weight: {mouse_weight}')
    except Exception as E:
        return 'The mouse weight must be a number. Please enter a number'
    else:
        try:
            pre_weight = float(state_globals['external']['mouse_weight_pre'])
            lower = pre_weight - float(get_from_config(['mouse_weight_upper_diff'], default=.2))
            upper = pre_weight + float(get_from_config(['mouse_weight_lower_diff'], default=1))
            if (mouse_weight < (lower)) or (mouse_weight > (upper)):
                message = (f"It looks like the current weight {mouse_weight} is more than {upper} or less than {lower}"
                    f" Are you sure it was typed correctly?")

                overrideable_error_state(state_globals, 'remove_mouse_and_move_files2', override_state='water_mouse',
                                         message=message)
        except Exception as E:
            message = 'Unable to verify mouse weight is reasonable'
            alert_text(message, state_globals)
        # else:
        #    get_water_supplement(state_globals, mouse_weight)


def get_water_supplement(state_globals, mouse_weight):
    try:
        mouse_director_proxy.set_mouse_id(state_globals["external"]["mouse_id"])
        mouse_director_proxy.set_mouse_weight(mouse_weight)
        try:
            water_supplement = float(mouse_director_proxy.get_water_supplement())
            print(f'Water supplement is: {water_supplement}')
            state_globals['external']['water_supplement'] = water_supplement
        except Exception as E:
            message = 'Unable to retrieve water supplement. Did mouse director crash? Make sure the mouse number has been entered'
            overrideable_error_state(state_globals, 'remove_mouse_and_move_files2',
                                     override_state='water_mouse', message=message)
    except Exception as E:
        message = 'Unable to send mouse weight. Did mouse director crash? Make sure the mouse number has been entered'
        overrideable_error_state(state_globals, 'remove_mouse_and_move_files2', override_state='water_mouse',
                                      message=message)







def monitor_experiment(state_globals, wait_time=300):
    print(f'monitoring experiment for {wait_time}')
    failed = {}
    start_time = dt.now()
    while ((dt.now() - start_time).total_seconds() < wait_time):
        camstim_is_running = camstim_running(state_globals)
        if not (camstim_is_running):
            time.sleep(20)
            camstim_is_running = camstim_running(state_globals)
            if not (camstim_is_running):
                failed['camstim_not running'] = 'Camstim appears to be finished'
                print('camstim is finished')
                break
        check_wait_time = get_from_config(['session_monitoring_stream_check_wait_time'], default=10)
        failed.update(check_data_stream_size(state_globals, wait_time=check_wait_time, reestablish_sizes=True,
                                             wait_in_between=10))
        if failed:
            return failed
        print(f'motiroring time elapsed: {str((dt.now() - start_time).total_seconds())}')
        time.sleep(60)
    return failed


def establish_data_stream_size(state_globals):
    time.sleep(1)
    failed = {}
    file_size_dict = {}
    sync_path = get_sync_location(state_globals)
    path_dict = {
        'sync': sync_path,
    }

    path_dict.update(get_video_locations(state_globals))

    for slot, drive in state_globals['external']['PXI'].items():
        a_probe = state_globals['external']['reverse_mapping'][slot]
        probeDir, computer = get_probeDir(state_globals, slot, drive)
        key = 'Ephys data ' + state_globals['external']['probes_in_slot'][slot]
        path_dict[key] = probeDir
    print()
    print(path_dict)
    already_alerted = False
    for stream, location in path_dict.items():
        try:
            state_globals['external'][location] = get_current_size(location)
            print_str = 'stream:' + str(stream) + str(state_globals['external'][location])
            print(print_str)
        except Exception as E:
            key = stream + '_found'
            logging.info(f'Could not get size of file at {location}')
            failed[key] = f'Could not get size of file at {location}'
            if not(already_alerted) and ('Ephys data' in key):
                alert_text('It looks like you may need to click restart connection in open ephys', state_globals)
                already_alerted = True
    return failed


def check_data_stream_size(state_globals, wait_time=10, reestablish_sizes=False, wait_in_between=3):
    time.sleep(1)
    failed = {}
    file_size_dict = {}
    sync_path = get_sync_location(state_globals)
    path_dict = {
        'sync': sync_path,
    }

    path_dict.update(get_video_locations(state_globals))

    min_space_dict = {
    }
    if "ExperimentStartTime_dt" in state_globals['external']:
        try:
            max_experiment_duration = config['max_experiment_duration_min'] * 60
        except Exception as E:
            max_experiment_duration = (3 * 60 * 60)

        elapsed_experiment_time = (dt.now() - state_globals['external']['ExperimentStartTime_dt']).total_seconds()
        exp_ratio = max(.05, min(1, (max_experiment_duration - elapsed_experiment_time) / max_experiment_duration))
    else:
        exp_ratio = 1

    for name in path_dict:
        min_space_dict[name] = 70

    data_c = []
    if not ('hab' in state_globals['external']['session_type']):
        for slot, drive in state_globals['external']['PXI'].items():
            a_probe = state_globals['external']['reverse_mapping'][slot]
            probeDir, computer = get_probeDir(state_globals, slot, drive)
            key = 'Ephys data ' + state_globals['external']['probes_in_slot'][slot]
            path_dict[key] = probeDir
            try:
                settings_location = glob.glob(get_settings_path(state_globals))[0]
                data_c.append(settings_location)
            except Exception as E:
                data_c.append(os.path.join(computer, 'C'))
                alert_text('Failed to find open ephys settings dir, C stability is likely to fail.', state_globals)
            if key in min_space_dict:
                min_space_dict[key] = min_space_dict[key] + 500 * exp_ratio
            else:
                min_space_dict[key] = 500 * exp_ratio
    print()
    print(path_dict)
    already_alerted = False
    for stream, location in path_dict.items():
        try:
            if (location in state_globals['external']) and not (reestablish_sizes):
                file_size_dict[stream] = state_globals['external'][location]
                print_str = 'stream:' + str(stream) + str(file_size_dict[stream])
                print(print_str)
            else:
                file_size_dict[stream] = get_current_size(location)
                print_str = 'stream:' + str(stream) + str(file_size_dict[stream])
                print(print_str)
        except Exception as E:
            key = stream + '_found'
            message = f'Could not get size of file at {location}'
            failed[key] = message
            if not(already_alerted) and ('Ephys data' in key):
                alert_text('It looks like you may need to click restart connection in open ephys', state_globals)
                already_alerted = True
        try:
            freespace = psutil.disk_usage(os.path.splitdrive(location)[0]).free
            if freespace < min_space_dict[stream] * (10 ** 9):
                key = stream + '_cleared for recording'
                message = f'Less than {min_space_dict[stream]} GB freespace at {location}'
                failed[key] = message
        except Exception as E:
            key = stream + '_cleared for recording'
            message = f'Could not check disk space at {location}'
            failed[key] = message

    c_size = {}
    for location in data_c:
        if os.path.isfile(location):
            c_size[location] = get_current_size(os.path.split(location)[0])
        else:
            c_size[location] = psutil.disk_usage(location).free

    not_changed = list(path_dict)
    start_time = dt.now()
    while ((dt.now() - start_time).total_seconds() < wait_time) and not_changed:
        print(f'elapsed time is {(dt.now() - start_time).total_seconds()}')
        print('waiting 5 seconds')
        time.sleep(wait_in_between)
        for stream in not_changed:
            location = path_dict[stream]
            try:
                current_size = get_current_size(location)
                print_str = 'stream:' + str(stream) + str(current_size)
                print(print_str)
                if not (file_size_dict[stream] == current_size):
                    not_changed.remove(stream)
            except Exception as E:
                key = stream + '_found'
                message = f'Could not get size of file at {location}'
                failed[key] = message

    for location, then_size in c_size.items():
        if os.path.isfile(location):
            now_size = get_current_size(os.path.split(location)[0])
        else:
            now_size = psutil.disk_usage(location).free
        if not (now_size == then_size):
            key = 'ACQ C drive_stable'
            message = f'Open ephys might be recording to two streams - It looks like there was a change in filesaze at {location}'
            failed[key] = message

    for stream in not_changed:
        location = path_dict[stream]
        key = stream + '_changing size'
        message = f'Unable to verify file size changing, please verify manually at {location}'
        failed[key] = message
        print(message)
    return failed


def get_current_size(data_path):

    if os.path.isdir(data_path):
        for f in os.listdir(data_path):
            # print(f'data path {data_path}')
            file_path = os.path.join(data_path, f)  # .replace('\\', '\\\\')
            # print(command_str)
            # command_str = f'wmic datafile where Name="{file_path}"'
            # subprocess.call(command_str , shell=True)#('dir '+file_path , shell=True)
            try:
                with open(file_path, 'r') as f:
                    print('opened')
            except Exception as E:
                print(f'failed to open {file_path}')
                logging.debug(E, exc_info=True)
        for i in range(10):
            size = sum(os.path.getsize(os.path.join(data_path, f)) for f in os.listdir(data_path))
            # print(size)
        # size = sum(os.path.getsize(f) for f in os.listdir(data_path) if os.path.isfile(f))
    else:
        # subprocess.call('dir '+data_path , shell=True)
        size = os.path.getsize(data_path)
        # size = os.path.getsize(data_path)
    return size


def create_file_extensions_dict():
    # TODO I'm thinking this whole dictionary should just live in the config, that would make things a lot clearner, and way easier to dupdate without messing with WSE
    # TODO everything should be re-written to rely on this. e.g. moving of avi files
    # maybe pkl file moving could rely on this dict too? right now I'm creating a dict with all falses...
    extension_params = namedtuple('extension_params',
                                  ['checkpoint', 'session_types', 'size_min_rel', 'size_min_abs', 'lims_key',
                                   'category'])

    # weight = float(state_globals['external']['mouse_weight'])
    # mouse_proxy = state_globals['component_proxies']['']

    file_extension_dict = {
        ".behavior.avi": extension_params(1, 'old_vmon', 999, 999, "behavior_tracking", 'AVI'),
        ".eye.avi": extension_params(1, 'old_vmon', 999, 999, "eye_tracking", 'AVI'),
        ".face.avi": extension_params(1, 'none', 999, 999, "face_tracking", 'AVI'),
        ".behavior.mp4": extension_params(1, 'new_vmon', 999, 999, "behavior_tracking", 'Video'),
        ".eye.mp4": extension_params(1, 'new_vmon', 999, 999, "eye_tracking", 'Video'),
        ".face.mp4": extension_params(1, 'new_vmon', 999, 999, "face_tracking", 'Video'),
        ".behavior.json": extension_params(1, 'new_vmon', 999, 999, "beh_cam_json", 'Video'),
        ".eye.json": extension_params(1, 'new_vmon', 999, 999, "eye_cam_json", 'Video'),
        ".face.json": extension_params(1, 'new_vmon', 999, 999, "face_cam_json", 'Video'),
        ".stim.pkl": extension_params(1, 'stim', 999, 999, "visual_stimulus", 'PKL'),
        ".mapping.pkl": extension_params(1, 'mapping', 999, 999, "visual_stimulus", 'PKL'),
        ".behavior.pkl": extension_params(1, 'behavior', 999, 999, "behavior_stimulus", 'PKL'),
        ".replay.pkl": extension_params(1, 'replay', 999, 999, "replay_stimulus", 'PKL'),
        # "_report.pdf": extension_params(2, 'All', 999, 999, "sync_report", 'category'),
        "_surgeryNotes.json": extension_params(1, 'All', 999, 999, "surgery_notes", 'category'),
        ".sync": extension_params(1, 'All', 999, 999, "synchronization_data", 'category'),
        "_platformD1.json": extension_params(2, 'All', 999, 999, None, 'category'),
        ".motor-locs.csv": extension_params(1, 'probeLocator', 999, 999, "newstep_csv", 'category'),
        ".opto.pkl": extension_params(1, 'Exp', 999, 999, "optogenetic_stimulus", 'PKL'),
        "_surface-image1-left.png": extension_params(1, 'Exp', 999, 999, "pre_experiment_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image1-right.png": extension_params(1, 'Exp', 999, 999, "pre_experiment_surface_image_right",
                                                      'Experiment Image'),
        "_surface-image2-left.png": extension_params(1, 'All', 999, 999, "brain_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image2-right.png": extension_params(1, 'All', 999, 999, "brain_surface_image_right",
                                                      'Experiment Image'),
        "_surface-image3-left.png": extension_params(1, 'Exp', 999, 999, "pre_insertion_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image3-right.png": extension_params(1, 'Exp', 999, 999, "pre_insertion_surface_image_right",
                                                      'Experiment Image'),
        "_surface-image4-left.png": extension_params(1, 'Exp', 999, 999, "post_insertion_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image4-right.png": extension_params(1, 'Exp', 999, 999, "post_insertion_surface_image_right",
                                                      'Experiment Image'),
        "_surface-image5-left.png": extension_params(1, 'Exp', 999, 999, "post_stimulus_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image5-right.png": extension_params(1, 'Exp', 999, 999, "post_stimulus_surface_image_right",
                                                      'Experiment Image'),
        "_surface-image6-left.png": extension_params(1, 'Exp', 999, 999, "post_experiment_surface_image_left",
                                                     'Experiment Image'),
        "_surface-image6-right.png": extension_params(1, 'Exp', 999, 999, "post_experiment_surface_image_right",
                                                      'Experiment Image'),
        ".areaClassifications.csv": extension_params(1, 'probeLocator', 1, 1, "area_classifications", 'probeLocator'),
        ".overlay.png": extension_params(1, 'probeLocator', 999, 999, "overlay_image", 'probeLocator'),
        ".fiducial.png": extension_params(1, 'probeLocator', 999, 999, "fiducial_image", 'probeLocator'),
        ".insertionLocation.png": extension_params(1, 'probeLocator', 999, 999, "insertion_location_image",
                                                   'probeLocator'),
        ".ISIregistration.npz": extension_params(1, 'probeLocator', 999, 999, "isi_registration_coordinates",
                                                 'probeLocator'),
        ".surgeryImage1.jpg": extension_params(1, 'surgery', 999, 999, "post_removal_surgery_image", 'Surgery Image'),
        ".surgeryImage2.jpg": extension_params(1, 'surgery', 999, 999, "final_surgery_image", 'Surgery Image')
    }
    return file_extension_dict


def create_session_type_mapping():
    all_options = {'Exp', 'Hab', 'surgery', 'All', 'probeLocator', 'behavior', 'stim', 'replay', 'mapping'}
    sesion_type_mapping = {
        'behavior_experiment_day1': {'Exp', 'surgery', 'All', 'probeLocator', 'behavior', 'mapping', 'replay'},
        'behavior_experiment_day2': {'Exp', 'All', 'probeLocator', 'behavior', 'mapping', 'replay'},
        'behavior_habituation_1_day_before_experiment': {'Hab', 'All', 'probeLocator', 'behavior', 'mapping'},
        'behavior_habituation': {'Hab', 'All', 'behavior', 'mapping'},
        'coding_hxperiment:': {'Exp', 'surgery', 'All', 'probeLocator', 'stim', 'old_vmon'},
        'coding_habituation_Last': {'Hab', 'All', 'probeLocator', 'stim', 'old_vmon'},
        'coding_habituation': {'Hab', 'All', 'stim', 'old_vmon'}
    }
    return sesion_type_mapping


def check_thing(key, params):
    true_list = []
    false_list = []
    for thing in params.iterable:
        if params.function(thing):
            true_list.append(thing)
        else:
            false_list.append(thing)
    if true_list:
        if false_list:
            print("ERROR - Some " + params.false_string)
            for thing in false_list:
                print("     " + thing)
        else:
            print(params.true_string)
    elif false_list:
        print("ERROR - All " + params.false_string)
    else:
        print("Nothing to check:. Did not check if " + params.false_string)
    return true_list, false_list


def dir_size(dir_path, key, probe):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(dir_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            fsize = os.path.getsize(fp)
            total_size += fsize
            self.size_dict[key][probe][f] = fsize
    return total_size


def check_local(try_file_path):
    size = False
    try:
        size = os.path.getsize(try_file_path)
    except FileNotFoundError as E:
        pass
    self.size_dict['extension'][extension] = size
    return size


def check_file_size(try_file_path, size):
    params = self.file_extension_dict[extension]
    min_size = max(params.size_min_rel, params.size_min_abs)
    return min_size < os.path.getsize(os.path.join(self.path, self.session + extension))


def get_backup_location(session_type):
    try:
        network_drive = r'\\' + config['backup_location']
    except Exception as E:
        network_drive = r"\\10.128.50.43\sd6.3"
    if 'hab' in session_type.lower():
        network_path = os.path.join(network_drive, 'habituation')
    else:
        network_path = network_drive
    print(f'network_path is {network_path}')
    return network_path


def make_category_dict(file_extensions_dict):
    category_dict = {}
    for extension, params in file_extensions_dict.items():
        category = params.category
        if not (category in category_dict):
            category_dict[category] = set()
        category_dict[category].add(extension)
    return category_dict


def check_files_input(state_globals, session_type, checkpoint):
    # TODO lets have this use NP_pipeline_validation as much as possible. for all the checks.
    print(f'Checking for files required for {session_type}')
    file_extensions_dict = create_file_extensions_dict()
    category_dict = make_category_dict(file_extensions_dict)
    missing_files = {}

    session_type_mapping = create_session_type_mapping()
    check_what = session_type_mapping[session_type]
    for extension, params in file_extensions_dict.items():
        if params.checkpoint == checkpoint and params.session_types in check_what:
            # print(f'Checking {extension}')
            file_path = os.path.join(state_globals['external']['local_lims_location'],
                                     state_globals["external"]["session_name"] + extension)
            size = False
            try:
                size = os.path.getsize(file_path)
            except FileNotFoundError as E:
                pass
            print(size)
            min_size = max(params.size_min_rel, params.size_min_abs)
            adequate_size = min_size < size
            if not (size):
                key = f'{extension}_found'
                message = f'ERROR: {extension} is missing'
                print('______________________________________________________________________________')
                print(message)
                print('')
                missing_files[key] = message
            elif not (adequate_size):
                key = f'{extension}_minimum size'
                message = f'ERROR: {extension} is too small'
                print('______________________________________________________________________________')
                print(message)
                print('')
                missing_files[key] = message
    for category, extension_set in category_dict.items():
        all_missing = True
        for extension in extension_set:
            key1 = f'{extension}_found'
            key2 = f'{extension}_minimum size'
            if not (key1 in missing_files) and not (key2 in missing_files):
                all_missing = False
                print(f'Not all missing for {category}')
        if all_missing:
            print(f'All missing for {category}')
            key = f'All {category} files_found'
            message = f'ERROR: All {category} files were missing or too small'
            missing_files[key] = message
            for extension in extension_set:
                key1 = f'{extension}_found'
                missing_files.pop(key1, None)
                key2 = f'{extension}_minimum size'
                missing_files.pop(key2, None)
    return missing_files


def check_availability(network_path, state_globals):
    backup = True
    try:
        if not (os.path.exists(network_path)):
            os.makedirs(network_path, exist_ok=True)
        freespace = psutil.disk_usage(network_path).free
        print('The backup disk is accessible')
        if freespace < 3 * (10 ** 13):
            alert_text('The network drive looks like it is getting full.', state_globals)
    except Exception as E:
        backup = False
        # print('ERROR: Cannot acess the backup drive')
        print('')
        print('______________________________________________________________________________')
        print(f'Cannot accsess the network backup: ' + network_path)
        print('______________________________________________________________________________')
        print('')
        logging.debug('Cannot acess the backup drive', exc_info=True)
    return backup


open_report_process = None


def check_files2(state_globals, session_type, checkpoint):
    # TODO This should only open sync report and handle the check with a speread call in .._workflow.py
    missing_files = check_files_input(state_globals, session_type, checkpoint)
    network_path = get_backup_location(state_globals['external']['session_type'])
    backup = check_availability(network_path, state_globals)
    stop = not (backup and not (missing_files))
    try:
        sync_report_name = f'{state_globals["external"]["session_name"]}_report.pdf'
        sync_report_path = os.path.join(state_globals['external']['local_lims_location'], sync_report_name)
        print(f'attempting to open sync report for manual insection from path {sync_report_path}')
        open_report_process = subprocess.Popen(sync_report_path, shell=True)
    except Exception as E:
        logging.debug('Error opening sync report', exc_info=True)
    return stop


def kill_open_report():
    killed = False
    try:
        # close the sync_report if it is open
        open_report_process.kill()
        killed = False
    except Exception as E:
        pass
    return killed


def videomon_copy_wrapup(state_globals, wait_time=15):
    i = 20
    time.sleep(1)
    if 'camera_copy_processes' in global_processes:
        p = global_processes['camera_copy_processes']
        while i > 0:
            i = i - 1
            try:
                # size = os.path.getsize(eye_tracking_path)

                if p.poll() is None:  # size == os.path.getsize(eye_tracking_path):
                    print(f"Videos are not fully copied")

                else:
                    print(f"Videos are fully copied")
                    rename_video_files(state_globals)
                    return None
                time.sleep(wait_time)
            except Exception as E:
                print_error(state_globals, e)
                break
                # logging.debug('Error confirming eyetracking fully copied')
    else:
        message = f'Videos are not copying automatically, could not confirm copying complete'
        alert_text(message, state_globals)
        return
    message = f'Error confirming Videos are fully copied'
    alert_text(message, state_globals)


def backup_files(state_globals):
    network_path = get_backup_location(state_globals['external']['session_type'])
    if check_availability(network_path, state_globals):
        source = state_globals['external']['local_lims_location']
        destination = os.path.join(network_path, state_globals["external"]["session_name"])
        # wait to ensure that eyetracking is fully copied
        # print('sleeping for '+str(wait_time))
        # time.sleep(wait_time)
        # eye_tracking_path = state_globals["external"]["eyetracking_local_file_path"]
        print('Proceeding with network backup')
        command_string = "robocopy " + source + " " + destination + r" /e /xo"
        print(f'Backing up files to {destination}')
        p = subprocess.Popen(command_string)
        global_processes['network_backup_process'] = p
    else:
        message = f'Canot access the network location {network_path}'
        overrideable_error_state(state_globals, 'copy_files_to_network', 'ready_to_check_network', message)


def check_files_network(state_globals, session_type, checkpoints):
    # TODO - this should be generalized, its like a specific version of check files.
    print(f'Checking for files required for {session_type}')
    file_extensions_dict = create_file_extensions_dict()
    network_location = get_backup_location(state_globals['external']['session_type'])
    missing_files = {}
    # check_what = set(check_what)
    session_type_mapping = create_session_type_mapping()
    check_what = session_type_mapping[session_type]
    for extension, params in file_extensions_dict.items():
        if (params.checkpoint in checkpoints) and (params.session_types in check_what):
            # print(f'Checking {extension}')
            file_path = os.path.join(state_globals['external']['local_lims_location'],
                                     state_globals["external"]["session_name"] + extension)
            network_path = os.path.join(network_location, state_globals["external"]["session_name"],
                                        state_globals["external"]["session_name"] + extension)
            size_match = False
            try:
                size_local = os.path.getsize(file_path)
                size_network = os.path.getsize(network_path)
                size_match = size_local == size_network
            except FileNotFoundError as E:
                pass
            print(size_match)
            if not (size_match):
                print('______________________________________________________________________________')
                message = f'ERROR: network backup of {extension} is missing or too small'
                print(message)
                print('')
                missing_files[extension + '_backed up'] = message
    return missing_files


def get_final_probeDir(state_globals, probe):
    # computer,x = os.path.split(state_globals["openephys_drives"][a_probe])
    # computer = r"\\"+computer.split(r'/')[2]
    slot = state_globals['external']['probe_list'][probe]
    drive = state_globals['external']['PXI'][slot]
    probeDir, computer = get_probeDir(state_globals, slot, drive)
    tail = state_globals["external"]["session_name"] + '_' + state_globals['external']['probes_in_slot'][slot]
    # print('computer:'+ computer + ', tail:'+tail)
    new_dir = os.path.join(computer, drive, tail)
    print(new_dir)
    return new_dir, computer


def get_processing_agents(state_globals):
    Processing_Agents = {}
    drives = get_from_config(['processing_drives'], default=config['openephys_drives'])
    if not ('processing_started' in state_globals):
        state_globals['processing_started'] = {}
    for drive in drives.values():
        computer_name = drive.split(r'/')[2]
        Processing_Agents[computer_name] = {'desc': 'ProcessingAgent', 'host': computer_name, 'port': 1234,
                                            'version': '0.1.0'}
        if not (computer_name in state_globals['processing_started']):
            state_globals['processing_started'][computer_name] = False
    return Processing_Agents


def initiate_data_processing(state_globals):
    Processing_Agents = get_processing_agents(state_globals)
    session_name = state_globals["external"]["session_name"]
    probe_recorded_dict = {}
    for probe in state_globals['external']['probe_list']:
        if state_globals['external'][f'probe_{probe}_surface']:
            probe_recorded_dict[probe] = get_final_probeDir(state_globals, probe)
    all_initiated = True
    for key, value in Processing_Agents.items():
        if True:  # not(state_globals['processing_started'][key]):
            print(
                f'Creating Proxy for device:{key} at host:{value["host"]} port:{value["port"]} device name:{value["desc"]}')
            port = str(value["port"])
            device = value['desc']

            # Create the Proxy for the dummy components
            fullport = f'{value["host"]}:{port}'
            proxy = Proxy(fullport)  # , serialization='json')

            # save the proxies for use later
            # state_globals['component_proxies'][key] = proxy

            # ** This is slow during development...leave in and uncomment when going to actual testing with hardware

            # Ping the remote computers to make sure they are alive...in dummy mode, will just be ping localhost, but we want to have the functionality here
            ping_result = os.system('ping %s -n 1' % (value["host"],))
            if ping_result:
                print(f'Host {value["host"]} Not Found')
                # LAN WAKE UP CALL GOES HERE
            else:
                print(f'Host {value["host"]} Found')

            initiated = False
            try:
                print(f'attempting to ping {value["host"]}')
                print(f'{key} uptime: {proxy.uptime}')
                # initiated = proxy.ping()
                print(f'Ping returned on {value["host"]}')
            except Exception as E:
                message = f'Processing agent uptime failed on {key}, gui is closed or not at config hostname and port: {fullport}. If the gui is closed please open it using RSC'
                message = get_from_config(['processing_ping_string'], default=message)
                logging.info(message, exc_info=True)
                overrideable_error_state(state_globals, 'initiate_data_processing', 'copy_files_to_network', message)
                initated = False
            else:
                try:
                    print(f'attempting to ping {value["host"]}')
                    # print(f'{key} uptime: {proxy.uptime}')
                    initiated = proxy.ping()
                    print(f'Ping returned on {value["host"]}')
                except Exception as E:
                    message = f'Processing agent Ping failed on {key}, processing is probabbly still running from the last session'
                    message = get_from_config(['processing_ping_string'], default=message)
                    logging.info(message, exc_info=True)
                    overrideable_error_state(state_globals, 'initiate_data_processing', 'copy_files_to_network',
                                             message)
                    initated = False
                else:
                    try:
                        print(f'attempting to initiate processing on {value["host"]}')
                        WSE_computer = os.environ['COMPUTERNAME']
                        initiated = proxy.process_npx(session_name, probe_recorded_dict, WSE_computer=WSE_computer)
                        print(f'completed attempt to initiate processing on {value["host"]}')
                    except Exception as E:
                        initiated = True
                        logging.info('Initiated processing, did not crash within timeout', exc_info=True)

                    time.sleep(15)
                    try:
                        print(f'attempting to ping {value["host"]}')
                        initiated = proxy.ping()
                        print(f'Ping returned on {value["host"]}')
                    except Exception as E:
                        initiated = True
                        logging.info('Ping failed, processing seems to be running', exc_info=True)
                    else:
                        initated = False
                        print('')
                        print('______________________________________________________________________________')
                        print('failed to start processing on ' + value["host"])
                        print('check dirnames, emsure that there is space on acquisiton, bakcup and processing drives')
                        print('Also ensure that the processing agent is open')
                        print(
                            'more details about the specific failure should be available on the command prompt assocatied with the processing agent')
                        print('______________________________________________________________________________')
                        print('')
                        message = f'It looks like processing failed to start on {key}. Please check the prompt for errors about disk space, etc'
                        message = get_from_config(['processing_ping_string'], default=message)
                        logging.info(message, exc_info=True)
                        overrideable_error_state(state_globals, 'initiate_data_processing', 'copy_files_to_network',
                                                 message)
                    state_globals['processing_started'][key] = initiated
                    all_initiated = all_initiated and initiated

    Day2_Agent = {'desc': 'Day2Agent', 'host': 'localhost', 'port': 1234, 'version': '0.1.0'}
    print(
        f'Creating Proxy for device: Day2_Agent at host:{Day2_Agent["host"]} port:{Day2_Agent["port"]} device name:{Day2_Agent["desc"]}')
    port = str(Day2_Agent["port"])
    device = Day2_Agent['desc']

    # Create the Proxy for the dummy components
    fullport = f'{Day2_Agent["host"]}:{port}'
    proxy = Proxy(fullport)  # , serialization='json')

    # save the proxies for use later
    # state_globals['component_proxies'][key] = proxy

    # ** This is slow during development...leave in and uncomment when going to actual testing with hardware

    # Ping the remote computers to make sure they are alive...in dummy mode, will just be ping localhost, but we want to have the functionality here
    ping_result = os.system('ping %s -n 1' % (Day2_Agent["host"],))
    Day2_Agent_initiated = False
    if ping_result:
        print(f'Host {Day2_Agent["host"]} Not Found')
        # LAN WAKE UP CALL GOES HERE
    else:
        print(f'Host {Day2_Agent["host"]} Found')
        try:
            print(f'attempting to initiate ready for day 2 on {Day2_Agent["host"]} with {session_name}')

            computers = list(Processing_Agents.keys())
            Day2_Agent_initiated = proxy.ready_day2(session_name, computers)
            print(f'completed attempt to initiate ready for day 2 on {Day2_Agent["host"]}')
        except Exception as E:
            logging.debug('failed to signal, make sure the Day 2 agent is open and running', exc_info=True)
            print('')
            print('______________________________________________________________________________')
            print('failed to signal, make sure the Day 2 agent is open and running')
            print('______________________________________________________________________________')
            print('')
    all_initiated = all_initiated  # and Day2_Agent_initiated
    return all_initiated


def get_start_experiment_params(state_globals):
    state_globals['external']["ExperimentStartTime_dt"] = dt.now()
    state_globals['external']["ExperimentStartTime"] = state_globals['external']["ExperimentStartTime_dt"].strftime(
        '%Y%m%d%H%M%S')
    state_globals['external']['local_log'] = f'ExperimentStartTime:{state_globals["external"]["ExperimentStartTime"]}'
    state_globals['external'][
        'sync_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.sync'
    sync_output_path = f'C:/ProgramData/AIBS_MPE/sync/output/'  # TODO put this in zookeeper
    os.makedirs(sync_output_path, exist_ok=True)
    # state_globals['external']['sync_temp_file'] = os.path.join(sync_output_path, f'{state_globals["external"]["session_name"]}_temp')


def start_sync(state_globals):
    try:
        proxy = state_globals['component_proxies']['Sync']
        try:
            proxy.start()
            # state_globals['external']['sync_temp_file'] = f'{config["sync_output_path"]}/{state_globals["external"]["session_name"]}_temp.h5'#TODO put in config
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            message = f'Remote call to start sync returned an error: {e}'
            print(message)
            alert_text(message, state_globals)
            state_globals['external']['status_message'] = f'Sync start failure:{e}'
            state_globals['external']['component_status']["Sync"] = False
    except Exception as e:
        print(f'Sync proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Sync proxy failure:{e}'
        state_globals['external']['component_status']["Sync"] = False


def stop_sync(state_globals):
    try:
        proxy = state_globals['component_proxies']['Sync']
        try:
            proxy.stop()

            sync_wrapup_time = get_from_config(['sync_wrapup_time'], default=5)

            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            state_globals['external']['status_message'] = f'Sync start failure:{e}'
            state_globals['external']['component_status']["Sync"] = False

    except Exception as e:
        print(f'Sync proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Sync proxy failure:{e}'
        state_globals['external']['component_status']["Sync"] = False
    # put in a delay between the sync starting and the videomon starting


def start_videomon(state_globals, video_prefix=''):
    try:
        # proxy = state_globals['component_proxies']['VideoMon']
        try:
            state_globals['external'][
                'behavior_file_path'] = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}.behavior.avi'
            state_globals['external'][
                'behavior_local_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.behavior.avi'
            state_globals['external'][
                'behavior_local_file_name'] = f'{state_globals["external"]["session_name"]}.behavior.avi'

            state_globals['external'][
                'eyetracking_file_path'] = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}.eye.avi'
            state_globals['external'][
                'eyetracking_local_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.eye.avi'
            state_globals['external'][
                'eyetracking_local_file_name'] = f'{state_globals["external"]["session_name"]}.eye.avi'
            state_globals['external'][
                'videomon_file_path'] = f'C:/ProgramData/AIBS_MPE/videomon/data/{state_globals["external"]["session_name"]}'  # TODO put in config

            # mvr_writer.define_hosts([cam['label'] for cam in config['cameras']])
            #* define_hosts now set at start of exp
            MaxRecTime_min = 365 * 60
            log = f'MID, {state_globals["external"]["mouse_id"]}, BID, {os.getenv("aibs_comp_id")}, Action, Begin Recording, MaxRecTime_min, {MaxRecTime_min}'
            logging.info(log, extra={'weblog': True})
            print('START RECORD:', video_prefix, state_globals['external']['session_name'])
            mvr_writer.start_record(file_name_prefix=video_prefix,
                                    sub_folder=state_globals['external']['session_name'],
                                    record_time=MaxRecTime_min)
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            message = f'Remote call to start MVR returned an error: {e}'
            print(message)
            alert_text(message, state_globals)
            state_globals['external']['status_message'] = f'Videomon start failure:{e}'
            state_globals['external']['component_status']["VideoMon"] = False
    except Exception as e:
        print(f'VideoMon proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Videomon proxy failure:{e}'
        state_globals['external']['component_status']["VideoMon"] = False

    # start the ephys process
    # recreate the proxy


def get_video_locations(state_globals):
    paths = {}
    for label in mvr_writer.exp_cam_labels:
        source = get_video_location(state_globals, label)
        paths[label] = source
    return paths


def get_video_location(state_globals, label):
    full_path = os.path.join(Rig.Mon.path,mvr_writer.output_dir,f"{state_globals['external']['session_name']}/*{label}*.mp4")
    print(f'Globbing for video at {full_path}')
    try:
        source = glob.glob(full_path)[0]
        print(f'Found video at {source}')
    except IndexError as E:
        source = full_path
    return source


def print_error(state_globals, e):
    template = "An exception of type {0} occurred. Arguments:{1!r}"
    message = template.format(type(e).__name__, e.args)
    print('\n\n' + '#' * 50)
    print(message)
    traceback.print_tb(e.__traceback__)
    print(message)
    print('\n\n' + '#' * 50)


def stop_videomon(state_globals):
    print('Attemption to stop videomon')
    # global_processes['camera_copy_processes'] = {}
    try:
        # proxy = state_globals['component_proxies']['VideoMon']
        try:
            host = Rig.Mon.path
            src_file_prefix = f"{host}\\c$\\programdata\\aibs_mpe\\mvr\\data\\{state_globals['external']['session_name']}"
            # src_file_prefix = os.path.join(host, vid_out_dir, state_globals['external']['session_name'])
            dst_file_prefix = state_globals["external"]["mapped_lims_location"]
            # os.path.join(state_globals["external"]["mapped_lims_location"], state_globals["external"]['session_name'])
            log = f'MID, {state_globals["external"]["mouse_id"]}, BID, {os.getenv("aibs_comp_id")}, Action, Stop Recording'
            logging.info(log, extra={'weblog': True})
            mvr_writer.stop_record()
            time.sleep(1)
            if not (os.path.exists(src_file_prefix)):
                message = 'Could not find videos to move, check location and permissions'
                alert_text(message, state_globals)
            state_globals['external']['video_filenames'] = os.listdir(src_file_prefix)
            command_string = "robocopy " + src_file_prefix + " " + dst_file_prefix + r" /e /xo"
            global_processes['camera_copy_processes'] = subprocess.Popen(command_string)
            # proxy.copy_arbitrary_file(
            #    (state_globals["external"]["videomon_file_path"] + "-0.avi"),
            #    state_globals["external"]["behavior_file_path"],
            # )
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            message = f'Remote call to stop MVR returned an error: {e}'
            print(message)
            alert_text(message, state_globals)
            state_globals['external']['status_message'] = f'Videomon start failure:{e}'
            state_globals['external']['component_status']["VideoMon"] = False
    except Exception as e:
        print(f'VideoMon proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Videomon proxy failure:{e}'
        state_globals['external']['component_status']["VideoMon"] = False


def rename_video_files(state_globals):
    for camera in config['cameras']:
        label = camera["label"]
        try:
            if not ('video_filenames' in state_globals['external']):
                message = f"No video filenames were saved, globbing for them instead"
                print(message)
                full_path = os.path.join(state_globals["external"]["mapped_lims_location"], '*' + label + '*')
                print(f'Globbing for files at {full_path}')
                assocatied_files = glob.glob(full_path)
                print(f'Files found for {label}: {assocatied_files}')
                assert (len(assocatied_files) <= 2)
                for old_filepath in assocatied_files:
                    if not ('pkl' in filename):
                        extension = os.path.splitext(old_filepath)[1]
                        new_filepath = os.path.join(state_globals["external"]["mapped_lims_location"],
                                                    state_globals["external"]['session_name'] + '.' + label + extension)
                        os.rename(old_filepath, new_filepath)
            else:
                assocatied_files = state_globals['external']['video_filenames']
                for file_name in assocatied_files:
                    if label.lower() in file_name.lower():
                        extension = os.path.splitext(file_name)[1]
                        old_filepath = os.path.join(state_globals["external"]["mapped_lims_location"], file_name)
                        new_filepath = os.path.join(state_globals["external"]["mapped_lims_location"],
                                                    state_globals["external"]['session_name'] + '.' + label + extension)
                        if os.path.exists(new_filepath):
                            print(f"Not renaming {label}, it already exists")
                        else:
                            os.rename(old_filepath, new_filepath)
        except Exception as E:
            message = f"There was an error renaming {label} video, maybe they don't exist"
            alert_text(message, state_globals)
            print_error(state_globals, E)


# start the stim process
# recreate the proxy
def start_stim(state_globals):
    try:
        camstim_proxy = state_globals['component_proxies']['Stim']
        try:
            status = retrieve_stim_status(camstim_proxy, state_globals)
            print(f'starting stim:{state_globals["external"]["stimulus_selected"]}')
            try:
                camstim_proxy.start_script_from_path(state_globals["external"]["stimulus_selected"])
            except Exception as E:
                message = 'Unable to start the stimulus. Please start manually and override, or fix camstim and retry'
                overrideable_error_state(state_globals, retry_state='initiate_stimulus',
                                         override_state='experiment_running_timer', message=message)
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            message = f'Remote call to start stim returned an error: {e}'
            print(message)
            alert_text(message, state_globals)
            state_globals['external']['status_message'] = f'Stim start failure:{e}'
            state_globals['external']['component_status']["Stim"] = False
    except Exception as e:
        print(f'Stim proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Stim proxy failure:{e}'
        state_globals['external']['component_status']["Stim"] = False


def start_common_session_monitoring(state_globals, video_prefix=''):
    get_start_experiment_params(state_globals)
    time.sleep(3)
    start_sync(state_globals)
    time.sleep(1)
    start_videomon(state_globals, video_prefix)
    time.sleep(
        2)  # a little extra buffer to make doubly sure we don't get any dropped MVR frames before the stim starts


def start_common_experiment_monitoring(state_globals, video_prefix=''):
    start_common_session_monitoring(state_globals, video_prefix)
    start_ecephys_recording(state_globals)
    # failed = check_data_stream_size(state_globals)
    # return failed


def stop_common_session_monitoring(state_globals):
    print('Stopping common session monitoring')
    get_stop_experiment_params(state_globals)
    time.sleep(2)  # A little extra buffer to make sure we recieve all the frames before MVR stops
    stop_videomon(state_globals)
    time.sleep(3)
    io = state_globals["resources"]["io"]
    sync_proxy = state_globals["component_proxies"]["Sync"]
    stop_sync(state_globals)


def stop_common_experiment_monitoring(state_globals):
    print('Stopping common experiment monitoring')
    stop_ecephys_recording(state_globals)
    stop_common_session_monitoring(state_globals)


def get_stop_experiment_params(state_globals):
    print('Getting stop experiment params')
    state_globals["external"]["ExperimentCompleteTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state_globals["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state_globals["external"]["ExperimentCompleteTime"]}'


def camstim_running(state_globals):
    running = False
    try:
        camstim_proxy = state_globals['component_proxies']['Stim']
        running = camstim_proxy.status['running']
    except Exception as E:
        message = 'Failed to check if camstim is running a stimulus'
        alert_text(message, state_globals)
    return running


def initiate_behavior_stimulus_input(state_globals):
    message = None
    if camstim_running(state_globals):
        message = ("A stim appears to be running."
                   "\nPlease stop session on mouse director and restart camstim using RSC before initiating a new one,"
                   "\nor proceed to experiment_running_timer to use the currently running stim")
        overrideable_error_state(state_globals, 'initiate_behavior_stimulus', override_state='experiment_running_timer',
                                 message=message)
        return
    else:
        initiate_behavior(state_globals)
    # state_globals['external']['clear_sticky'] = True
    # state_globals['external']['next_state'] = 'experiment_running_timer'
    # state_globals["external"]["transition_result"] = True
    # state_globals["external"]["status_message"] = "success"
    save_platform_json(state_globals, manifest=False)
    if not (camstim_running(state_globals)):
        message = "The stimulus doesn't seem to have started"
        overrideable_error_state(state_globals, 'initiate_behavior_stimulus', override_state='experiment_running_timer',
                                 message=message)
    save_platform_json(state_globals, manifest=False)
    return message


def initiate_behavior(state_globals):

    
    mouse_id = state_globals["external"]["mouse_id"]
    user_id = state_globals["external"]["user_id"]
    camstim_proxy = state_globals['component_proxies']['Stim']
    print('Starting behavior session')
    try:
        if isinstance(experiment, Passive):
            script = f"{config['scripts_path']}/{state_globals['external']['passive_script']}.py"
            camstim_proxy.start_script(script)
        else: 
            camstim_proxy.start_session(mouse_id, user_id)
    except Exception as E:
        print('here2')
        message = 'Unable to start the stimulus. Please start manually and override, or fix camstim and retry'
        overrideable_error_state(state_globals, retry_state='initiate_behavior_stimulus',
                                 override_state='experiment_running_timer', message=message)

    time.sleep(5)
    # try:
    print('attempting to retrieve_stim_status')
    retrieve_stim_status(camstim_proxy, state_globals)
    state_globals['external']['next_state'] = 'experiment_running_timer'
    # state_globals['external'].pop(choice_field, None)
    # except Exception as E:
    #    logging.debug('failed to retireve stim status from camstim', exc_info=True)
    #    state_globals['external']['next_state'] = 'camstim_ping_error'


def verify_script_name(state_globals, stimulus_name):
    try:
        if not (stimulus_name is None):
            missing_keywords = []
            keywords = []
            session_day = state_globals['external']['entered_experiment_day']
            if not ('hab' in state_globals['external']['full_session_type'].lower()):
                try:
                    keywords.extend(config['MTrainData']['Experiment_Keywords'])
                except Exception:
                    alert_text('Failed to check Experiment_Keywords in script name - not in config', state_globals)
            try:
                keywords.extend(config['MTrainData']['Stage_Keywords'])
            except Exception:
                alert_text('Failed to check Stage_Keywords in script name - not in config', state_globals)
            try:
                keywords.extend(config['MTrainData']['Experiment_Sessions'][session_day]['Keywords'])
            except Exception:
                if 'hab' in state_globals['external']['session_type']:
                    try:
                        mtrain_string = None
                        try:
                            default=None
                            key_list = ['MTrainData', 'Habituation_Sessions', str(dt.today().weekday()+1), 'Keywords']
                            mtrain_string = get_from_config(key_list, default)
                        except Exception as E:
                            pass
                        if mtrain_string == None:
                            keywords.extend(config['MTrainData']['Habituation_Sessions']['Habituation']['Keywords'])
                    except Exception as E:
                        alert_text(f'Failed to check Habituation keywords in script name - not in config',
                                   state_globals)
                else:
                    alert_text(f'Failed to check {session_day} keywords in script name - not in config', state_globals)
            for keyword in keywords:
                if not (keyword in stimulus_name):
                    missing_keywords.append(keyword)
            if missing_keywords:
                message = f"Some keywords are missing from the script name: {', '.join(missing_keywords)}"
                alert_text(message, state_globals)
        else:
            alert_text('No script name found: Unable to confirm correct mtrain stage', state_globals)
    except Exception as E:
        message = 'A failure occurred while attempting to check the script name'
        alert_text(message, state_globals)
        logging.debug(message, exc_info=True)


def get_script_name(stim_status):
    script_path = stim_status['script_path']
    script_file = os.path.split(script_path)[1]
    stimulus_name_list = script_file.split('_')
    stimulus_name_list.pop()
    stimulus_name = ('_').join(stimulus_name_list)
    return stimulus_name, script_path


def get_stim_status(camstim_proxy, state_globals):
    foraging_id = None
    stimulus_name = None
    script_path = None
    try:
        stim_status = camstim_proxy.status
        if not camstim_running(state_globals):
            message = "The stimulus doesn't seem to be running. \nPlease fix camstim and retry. \n   If you start manually you shoulds still revert \n   so that the WSE grabs the new foraging ID"
            overrideable_error_state(state_globals, retry_state='initiate_behavior_stimulus',
                                     override_state='experiment_running_timer', message=message)
        foraging_id = stim_status['session_uuid']
        stimulus_name, script_path = get_script_name(stim_status)
    except Exception as E:
        message = 'Unable to retrieve the stim status. \nPlease fix camstim and retry. \n   If you start manually you shoulds still reinitiate \n   so that the WSE grabs the new foraging ID'
        overrideable_error_state(state_globals, retry_state='initiate_behavior_stimulus',
                                 override_state='experiment_running_timer', message=message)
    return foraging_id, stimulus_name, script_path


def retrieve_stim_status(camstim_proxy, state_globals):
    try:
        foraging_id, stimulus_name, script_path = get_stim_status(camstim_proxy, state_globals)
        # state_globals['external']['foraging_id'] = foraging_id
        state_globals['external']['foraging_id_list'].append(foraging_id)
        state_globals['external']['stimulus_name'] = stimulus_name
        state_globals['external']['script_name'] = script_path
        print('foraging ID:' + state_globals['external']['foraging_id'])
        print('stimulus_name:' + state_globals['external']['stimulus_name'])
        print('script_name:' + state_globals['external']['script_name'])
        verify_script_name(state_globals, stimulus_name)
        # if not(bool(state_globals['external']['foraging_id'])):
        # alert_text('The foraging ID is empty', state_globals)
    except Exception as E:
        message = 'Unable to retrieve the stim status. \nPlease fix camstim and retry. \n   If you start manually you shoulds still reinitiate \n   so that the WSE grabs the new foraging ID'
        overrideable_error_state(state_globals, retry_state='initiate_behavior_stimulus',
                                 override_state='experiment_running_timer', message=message)


def camstim_ping_error_input(state_globals):
    print('>> camstim_ping_error_input <<')
    handle_2_choice_button('camstim_ping_retry', 'initiate_behavior_stimulus', 'experiment_running_timer',
                           state_globals)


def alert_text(message, state_globals, alert=True, transition=True, log_message=True, log_level=logging.DEBUG):
    if log_message:
        logging.log(log_level, message)
    if 'alert' in state_globals['external'] and state_globals['external']['alert']:
        message = append_alert(message, state_globals)
    state_globals['external']['alert'] = alert
    state_globals["external"]["transition_result"] = transition
    state_globals["external"]["msg_text"] = message


def append_alert(message: str, state_globals: dict):
    current_message = ''
    if 'msg_text' in state_globals['external']:
        current_message = state_globals["external"]["msg_text"]
        if not ('There were multiple messages:\n' in current_message):
            current_message = f'There were multiple messages:\n --- {current_message}\n\n'
    message = f'{current_message} --- {message}\n\n'
    return message


def get_new_files_list(path, num_files, extension='.pkl'):
    """
    returns a list of the number most recent files.  For use with WSE2.0
    """
    search_path = f'{path}/*{extension}'
    print(f' Searching {path} for {extension}')
    try:
        sorted_list = sorted(glob.iglob(search_path), key=os.path.getctime)
        recent_list = sorted_list[-num_files:]
    except Exception as e:
        logging.info("Unable to return new files list")
        recent_list = []

    return recent_list


def check_pkls(state_globals, session_type):
    time.sleep(10)  # this is to give camstim time to copy them.
    # Better solution would be to use robocopy here and ping process as with videos
    pkl_list = get_pkl_list(session_type)
    failed = {}
    for pkl_extension in pkl_list:
        pkl_path = os.path.join(state_globals["external"]["mapped_lims_location"],
                                state_globals["external"]["session_name"] + pkl_extension)
        if not (os.path.exists(pkl_path)):
            key = f'{pkl_extension}_found'
            message = f'Did not find pkl at {pkl_path}'
            failed[key] = message
    return failed


def get_pkl_list(session_type):
    if session_type == 'pretest':
        session_type = 'behavior_experiment_day1'
    file_extensions_dict = create_file_extensions_dict()
    session_type_mapping = create_session_type_mapping()
    check_what = session_type_mapping[session_type]
    pkl_list = []
    for extension, params in file_extensions_dict.items():
        if (params.session_types in check_what) and ('pkl' in extension):
            pkl_list.append(extension)
    return pkl_list


def get_num_pkls(session_type):
    if 'pretest' in session_type:
        if 'pretest_params' in config:
            num_files = config['pretest_params']['num_files']
        else:
            num_files = 7
    else:
        session_type_mapping = {
            'behavior_experiment_day1': 7,
            'behavior_experiment_day2': 7,
            'behavior_habituation': 4,
            'behavior_experiment': 5
        }
        try:
            num_files = session_type_mapping[session_type]
        except KeyError as E:
            num_files = 1
    return num_files


def file_created_after_experiment_start(state_globals, fullpath, modified=False):
    created_after_experiment_start = False
    try:
        experiment_start_time = state_globals['external']["ExperimentStartTime_dt"]
        if modified:
            filetime = dt.fromtimestamp(
                os.path.getmtime(fullpath)
            )
        else:
            filetime = dt.fromtimestamp(
                os.path.getctime(fullpath)
            )
        logging.info('Experiment start time:' + experiment_start_time.strftime('%Y%m%d_%H_%M_%S'))
        logging.info('The file was created at:' + filetime.strftime('%Y%m%d_%H_%M_%S'))
        created_after_experiment_start = filetime > experiment_start_time
    except Exception as E:
        print_error(state_globals, E)
        message = 'A failure occurred confirming the file was created during the expriment'
        alert_text(message, state_globals)
    return created_after_experiment_start


def get_foraging_id(pkl_path):
    print(pkl_path)
    filename = os.path.split(pkl_path)[1]
    try:
        print(filename)
        foraging_id = filename.split('-')[1]
    except Exception as E:
        logging.debug('error splitting foraging filename', exc_info=True)
        foraging_id = None
    return foraging_id


def overwrite_foraging_id(behavior_pkl_path, session_type, state_globals):
    foraging_id = get_foraging_id(behavior_pkl_path)
    print(foraging_id)
    if (foraging_id is None):
        message = 'Foraging ID not retrieved from pkl file. You will have to enter manually'
        alert_text(message, state_globals)
    elif not ('pretest' in session_type):
        state_globals['external']['foraging_id'] = foraging_id
        if not (foraging_id in state_globals['external']['foraging_id_list']):
            state_globals['external']['foraging_id_list'].append(foraging_id)


def get_pkl_path(pkl_keyword, state_globals):
    if '-' in pkl_keyword:
        try:
            data_location = state_globals['external']['local_lims_location']
            file_name = pkl_keyword + '*'
            computer = r'\\' + os.environ['COMPUTERNAME']
            filepath = os.path.join(computer, data_location, file_name)
            pkl_path = glob.glob(filepath)[0]
        except Exception as E:
            pkl_keyword = pkl_keyword[:-1]
            suffix = '.' + pkl_keyword + '.pkl'
            pkl_path = get_file_path(suffix, state_globals)
    else:
        suffix = '.' + pkl_keyword + '.pkl'
        pkl_path = get_file_path(suffix, state_globals)
    return pkl_path


def get_file_path(suffix, state_globals):
    data_location = state_globals['external']['local_lims_location']
    session_name = state_globals['external']["session_name"]
    file_name = session_name + suffix
    computer = r'\\' + os.environ['COMPUTERNAME']
    filepath = os.path.join(computer, data_location, file_name)

    return filepath


def copy_stim_pkls(state_globals, session_type):
    print('Copying stim pkls')

    num_files = get_num_pkls(session_type)

    pkl_list = get_pkl_list(session_type)
    #! TODO pkl_list is len(1) for behav exp
    #! getn_new_files_list should not use pkl_List for ref, replace with 'renamer' method
    print(f'Pkls to copy: {" ,".join(pkl_list)}')
    try:
        camstim_proxy = state_globals["component_proxies"]["Stim"]

        try:
            host = r'\\' + config['components']['Stim']['host']
            # stim_output_path = os.path.join(host, "output")  # TODO put in config
            stim_output_path = str(pathlib.Path(camstim_proxy.session_output_path).parent)
            file_list = get_new_files_list(stim_output_path, num_files)
            print(f">>>> file_list:{file_list}")
            warnings = {}
            for file in file_list:
                copy_file_as = ''
                try:
                    filename = os.path.split(file)[1]
                    full_path = os.path.join(stim_output_path, filename)

                    if not (file_created_after_experiment_start(state_globals, full_path)):
                        Err_string2 = f'The canidate pkl file {file} was created before the start of the experiment'
                        logging.debug(Err_string2)
                    else:
                        source = full_path
                        destination = os.path.join(state_globals["external"]["mapped_lims_location"], filename)
                        try:
                            print(f'Asking camstim to copy {source} to {destination}')
                            camstim_proxy.copy_arbitrary_file(source, destination) #! is this working?
                            time.sleep(.5)
                        except:
                            print(f'Try manual copy {source} to {destination}')
                            shutil.copy2(source,destination)


                        for pkl_extension in pkl_list:
                            pkl_keyword = pkl_extension.split('.')[-2]
                            if pkl_keyword in file:
                                copy_file_as = pkl_keyword

                        # if ("opto" in file) and (
                        #    '.opto.pkl' in pkl_list):  # Todo could proboably make this more flexible too...
                        #    copy_file_as = "opto"
                        # elif ('replay' in file) and ('.replay.pkl' in pkl_list):
                        #    copy_file_as = "replay"
                        # elif ('mapping' in file) and ('.mapping.pkl' in pkl_list):
                        #    copy_file_as = "mapping"
                        # elif ('behavior' in file) and ('.behavior.pkl' in pkl_list):
                        #    copy_file_as = "behavior"
                        # elif ('pretest' in session_type.lower()) and (num_files == 1):
                        #    copy_file_as = "behavior"
                        # elif (
                        #    'hab' in session_type.lower()):  # TODO remove this when Chris's final stim is working? (should include behavior in filename therefore its redundant, might get smaller pkl instead of slightly larger custom one)
                        #    copy_file_as = "behavior"
                        #    destination = os.path.join(state_globals["external"]["mapped_lims_location"],
                        #                               state_globals["external"][
                        #                                   "session_name"] + '.' + copy_file_as + '.pkl')
                        #    if os.path.exists(destination):
                        #        copy_file_as = False
                except Exception as E:
                    name = os.path.split(file)[1]
                    key = name + '_copied sucessfully'
                    message = f"Did not copy this pkl file {file} because of {E}!"
                    warnings[key] = message
                    print_error(state_globals, E)

                try:
                    if copy_file_as:
                        source = full_path
                        destination = get_pkl_path(copy_file_as, state_globals)
                        if os.path.exists(destination):
                            key = copy_file_as + '_unambiguous'
                            message = f'It looks like the {copy_file_as} pkl has already been copied\nIf this is the first time you tried to copy pkls, there must be two possibilities.\n You will have to make sure the correct one is renamed'
                            warnings[key] = message
                        else:
                            if session_type != 'pretest' and not (state_globals["external"]["mouse_id"] in file):
                                alert_text(
                                    f'The mouse ID was not found in the pkl {copy_file_as} filename. It was renamed to go to lims anyway.',
                                    state_globals)
                            print(f'Asking camstim to copy {source} to {destination}')
                            camstim_proxy.copy_arbitrary_file(source, destination)
                            # command_string = "robocopy "+ source +" "+destination +r" /e /xo"
                            # p = subprocess.check_output(command_string)
                except Exception as E:
                    name = os.path.split(file)[1]
                    key = name + '_renamed sucessfully'
                    message = f"Did not rename this pkl file {file} because of {E}!"
                    warnings[key] = message
                    print_error(state_globals, E)

                try:
                    if ('behavior' in copy_file_as):  # and not('pretest' in session_type):
                        time.sleep(2)
                        overwrite_foraging_id(source, session_type, state_globals)
                except Exception as E:
                    name = os.path.split(file)[1]
                    key = f'foraging id_retrieved from {name}'
                    message = f"Did not retrive foraging ID from file {file} because of {E}!"
                    warnings[key] = message
                    print_error(state_globals, E)

            if warnings:
                alert_from_error_dict(state_globals, warnings)

                # logging.exception(E)

        except Exception as e:
            message = f"Stim copy file failure:{e}"
            alert_text(message, state_globals)
            # print(f"Stim copy failure:{e}!")
            # state_globals["external"]["component_status"]["Stim"] = False
            # logging.exception(e)
    except Exception as e:
        print(f"Stim proxy failure:{e}!")
        state_globals["external"]["status_message"] = f"Stim proxy failure:{e}"

        state_globals["external"]["component_status"]["Stim"] = False


def establish_proxies(state_globals):
    state_globals['component_proxies'] = {}
    for key, value in config['components'].items():
        if 'port' in value and 'Notes' not in key:
            print(
                f'Creating Proxy for device:{key} at host:{value["host"]} port:{value["port"]} device name:{value["desc"]}')
            port = str(value["port"])
            device = value['desc']

            # Create the Proxy for the dummy components
            fullport = f'{value["host"]}:{port}'
            proxy = Proxy(fullport, serialization='json')

            # save the proxies for use later
            state_globals['component_proxies'][key] = proxy



def check_components(state_globals):
    print('Checking components')
    compStatusArray = {}
    # pdb.set_trace()
    for key, value in config['components'].items():

        if 'port' in value and 'Processing' not in key and 'Notes' not in key:
            compStatusArray[key] = False
            # Ping the remote computers to make sure they are alive...in dummy mode, will just be ping localhost, but we want to have the functionality here
            ping_result = os.system('ping %s -n 1' % (value["host"],))
            if ping_result:
                print(f'Host {value["host"]} Not Found')
                # LAN WAKE UP CALL GOES HERE
            else:
                print(f'Host {value["host"]} Found')

            # put this in the state globals so we can re-establish the proxy later in the workflow.  Can save the proxies
            # themselve since they are object and can't be serialized.
            proxy = state_globals['component_proxies'][key]
            try:
                print(f'{key} uptime: {proxy.uptime}')
                try:
                    platform_info = proxy.platform_info
                    version = platform_info.get('version', None)
                    if version < value['version']:
                        message = f'Component:{key} reporting outdated version.  Reporting {version}, needs {value["version"]}\n'
                        alert_text(message, state_globals)
                    else:
                        print(f'>>>> Component:{key} correct version {version} <<<<\n')
                except Exception as e:
                    print(f'Version check error:{e}\n')
                compStatusArray[key] = True
            except Exception as E:  # zmq.error.Again:
                logging.debug(f'Cannot communicate with {key}.')
                if 'Processing Agent' in key:
                    message = 'Processing agent did not respond. The processing agent may fail to respond if it is still busy processing data from the previous experiment.'
                    message = get_from_config(['processing_agent_unresponsive_str'], message)
                    if not ('msg_text' in state_globals['external']) or not (
                        message in state_globals['external']['msg_text']):
                        alert_text(message, state_globals)
        elif 'Processing' in key:
            print(f'skipping connection to {key}')
        else:  # the open ephys interface goes through the workflow router program, so need to set this up differently
            if not ('hab' in state_globals['external']['session_type']):
                compStatusArray[key] = False
                # state_globals['resources']['io'].add_message_bundle(ephys_messages)
                # state_globals['resources']['io'].register_for_message('system_info', handle_message)
                # state_globals['resources']['io'].register_for_message('system_status', handle_message)
                # state_globals['resources']['io'].register_for_message('set_data_file_path', handle_message)
                # state_globals['resources']['io'].register_for_message('acquisition', handle_message)
                # state_globals['resources']['io'].register_for_message('recording', handle_message)
                
                # and now request the system info
                try: 
                    message = request_open_ephys_status(state_globals) # = ephys_messages.request_system_info()
                    # state_globals['resources']['io'].write(message)
                    compStatusArray[key] = True
                except:
                    message = 'Open Ephys Interface did not respond. Is it running?'

    state_globals["external"]["drive_memory_low"] = False
    if disk_usage(state_globals["external"]["local_lims_head"]).free < 80 * (10 ** 9):
        state_globals["external"]["drive_memory_low"] = True
    state_globals['external']['component_status'].update(compStatusArray)


def confirm_components(state_globals):
    print('confirming components')
    check_components(state_globals)
    failed = []
    for name, status in state_globals['external']['component_status'].items():
        if not (status):
            failed.append(name)
    if state_globals["external"]["drive_memory_low"]:
        failed.append('z drive_memory_low')
    return failed


def run_pretest_script(state_globals, camstim, pretest_DOC_path):
    print(f'Attempting to run pretest stim from path {pretest_DOC_path}')
    try:
        camstim.start_script_from_path(pretest_DOC_path)
    except Exception as E:
        message = f'Unable to start the stimulus from pretest path. {pretest_DOC_path}'
        overrideable_error_state(state_globals, retry_state='configure_hardware_openephys', override_state='pretest',
                                 message=message)
        raise


def run_pretest_override_params(state_globals, camstim, params_path):
    if isinstance(experiment, DynamicRouting):
        #! TODO: this is a hack to get the pretest to work with camstim 2 override params
        # ben and corbett july 2022
        params_path = R"C:\Users\svc_neuropix\Documents\GitHub\NP_pipeline_validation\pretest_stim_params\dynamic_routing_pretest_stim_params.json"
    
    print(f'Attempting to run pretest stim with override params {params_path}')
    state_globals['external']['pretest_stimulus_name'] = ''
    override_params = False
    try:
        with open(params_path, 'r') as f:
            override_params = json.load(f)
    except Exception as E:
        message = f'Unable to load override params. Make sure they are present and shared at {params_path}'
        overrideable_error_state(state_globals, retry_state='configure_hardware_openephys', override_state='pretest',
                                 message=message)
    if override_params:
        try:
            # mouse_id = state_globals["external"]["mouse_id"]
            mouse_id = override_params["mouse_id"]
            user_id = state_globals["external"]["user_id"]
            print('Starting behavior session')
            camstim.start_session(mouse_id, user_id, override_params=override_params)
            try:
                time.sleep(3)
                stim_status = camstim.status
                stimulus_name, script_path = get_script_name(stim_status)
                state_globals['external']['pretest_stimulus_name'] = stimulus_name
            except Exception as E:
                message = f'Unable to retrieve the pretest script name: {E}'
                alert_text(message, state_globals)

        except Exception as E:
            message = 'Unable to start the session. Please start manually and override, or fix camstim and retry'
            overrideable_error_state(state_globals, retry_state='configure_hardware_openephys',
                                     override_state='pretest', message=message)
        try:
            if not (camstim_running(state_globals)):
                message = "The stimulus doesn't seem to be running. \nPlease fix camstim and retry."
                overrideable_error_state(state_globals, retry_state='configure_hardware_openephys',
                                         override_state='pretest', message=message)
        except Exception as E:
            message = "Unable to check if the sim is running. \nPlease fix camstim and retry."
            overrideable_error_state(state_globals, retry_state='configure_hardware_openephys',
                                     override_state='pretest', message=message)


def pretest_path(state_globals):
    params_path = False
    try:
        params_dir = config['pretest']['path']
        params_path = os.path.join(params_dir,
                                   state_globals['external']['full_session_type'] + '_pretest_stim_params.json')
        params_path = glob.glob(params_path)[0]
    except Exception as E:
        if params_path:
            message = f'Failed to find file at path {params_path}'
            alert_text(message, state_globals)
        params_dir = r"C:\Users\svc_neuropix\Documents\GitHub\NP_pipeline_validation\pretest_stim_params"
        params_path = os.path.join(params_dir,
                                   state_globals['external']['full_session_type'] + '_pretest_stim_params.json')
    return params_path


def start_pretest_stim(state_globals):
    computername = r'\\' + os.environ['COMPUTERNAME']
    # print(computername)
    camstim = state_globals["component_proxies"]["Stim"]
    ran_stim = False
    try:
        if ('pretest' in config) and (config['pretest']['mode'] == 'manual'):
            time.sleep(10)
            alert_text(
                'Pretest mode is manual - the WSE waited 10 seconds, hopefully you started pretest in that time.',
                state_globals)
            ran_stim = True
        if ('pretest' in config) and (config['pretest']['mode'] == 'script'):
            pretest_DOC_path = config['pretest']['path']
            run_pretest_script(state_globals, camstim, pretest_DOC_path)
            ran_stim = True
    except Exception as E:
        pass
    if not (ran_stim):
        params_path = pretest_path(state_globals)
        run_pretest_override_params(state_globals, camstim, params_path)

    # else:
    #    params_path = pretest_path(state_globals)
    #    run_pretest_override_params(state_globals, camstim, params_path)
    # pretest_DOC_path = os.path.join(computername, r"NP_pipeline_pretest\example_DOC_short.py")  # TODO Put in config
    # run_pretest_script(state_globals, camstim, pretest_DOC_path)


def run_np_validation(state_globals, env_name, functions_path, params_path, file_paths_path, validation_output_path):
    try:
        command = (' ').join(
            [config['np_validation_python_path'], functions_path, params_path, file_paths_path, validation_output_path])
        subprocess.check_output(command, shell=True)
    except Exception as E:
        message = f'Failed to initiate validation. See the prompt for more details'
        traceback.print_tb(E.__traceback__)
        alert_text(message, state_globals)


def get_validation_output_path(state_globals, validation_type):
    validation_output_path = os.path.join(
        state_globals["external"]["mapped_lims_location"],
        validation_type + '_validation_results.json'
    )
    return validation_output_path


def get_validation_params_path(state_globals, validation_type):
    validation_params_path = False
    try:
        validation_params_path = os.path.join(config['validation']['params_directory'],
                                              validation_type + '_params.json')
        validation_params_path = glob.glob(validation_params_path)[0]
    except Exception as E:
        dir_path = r"C:\Users\svc_neuropix\Documents\GitHub\NP_pipeline_validation\validation_params"
        default = os.path.join(dir_path, validation_type + '_params.json')
        if validation_params_path:
            message = f'Failed to find validtaion params file at path from config: {validation_params_path}\n\n Using default {default} instead'
            # alert_text(message, state_globals)
            print(message)
        validation_params_path = default
    return validation_params_path


def get_file_paths_path(state_globals, validation_type):
    return os.path.join(state_globals['external']['local_lims_location'], validation_type + '_file_paths.json')


def run_validation(state_globals, validation_type):
    # run QC script
    try:
        env_name = config['validation']['environment']
    except Exception as E:
        env_name = 'np_pipeline_validation'  # TODO put in config

    functions_path = False
    try:
        functions_path = config['validation']['script']
        functions_path = glob.glob(functions_path)[0]
    except Exception as E:
        default = r"C:\Users\svc_neuropix\Documents\GitHub\NP_pipeline_validation\run_validation_functions.py"
        if functions_path:
            message = f'Failed to find validtaion functions file at path from config: {functions_path}\n\n Using default {default} instead'
            # alert_text(message, state_globals)
            print(message)
        functions_path = default

    params_path = get_validation_params_path(state_globals, validation_type)
    file_paths_path = get_file_paths_path(state_globals, validation_type)
    make_validation_path_json(state_globals, file_paths_path)
    validation_output_path = get_validation_output_path(state_globals, validation_type)
    print(validation_output_path)

    run_np_validation(state_globals, env_name, functions_path, params_path, file_paths_path, validation_output_path)

    # change session_name back


def get_validation_results(state_globals, validation_type):
    failed = {}
    try:
        validation_output_path = get_validation_output_path(state_globals, validation_type)
        with open(validation_output_path, 'r') as f:
            validation_results = json.load(f)
        print(validation_results)
        for result_name, params in validation_results.items():
            print('result: ', result_name)
            if params['success'] == 0:  # Todo put this key in config?
                failed[result_name] = params['result string']
            else:
                print('test sucessful')
    except Exception as E:
        failed['validation_checked'] = f'Error checking validation results. See the prompt for more details'
        traceback.print_tb(E.__traceback__)
    return failed


def make_validation_path_json(state_globals, write_path):
    add_new_files(state_globals)
    add_probe_dirs(state_globals)
    file_paths = {
        'file_paths': state_globals['file_paths_dict']
    }

    with open(write_path, 'w') as out:
        json.dump(file_paths, out, indent=2)

    return write_path


def init_files(state_globals):
    if not ('file_paths_dict' in state_globals):
        state_globals['file_paths_dict'] = {}
    if not ('files_dict' in state_globals):
        state_globals['files_dict'] = {}


def add_new_files(state_globals, manifest=False):
    files_dict, file_paths_dict = get_new_files(state_globals, manifest)
    init_files(state_globals)
    state_globals['files_dict'].update(files_dict)
    state_globals['file_paths_dict'].update(file_paths_dict)


def get_new_files(state_globals, manifest=False):
    file_extensions_dict = create_file_extensions_dict()
    files_dict = {}
    file_paths_dict = {}
    try:
        data_location = state_globals['external']['local_lims_location']
        session_name = state_globals['external']["session_name"]
        for suffix, params in file_extensions_dict.items():
            name = params.lims_key
            try:
                file_name = session_name + suffix
                computer = r'\\' + os.environ['COMPUTERNAME']
                filepath = os.path.join(computer, data_location, file_name)
                # print('###################### Filepath' + filepath)
                fileExists = os.path.isfile(filepath)
                if fileExists:
                    file_paths_dict
                    #if name is 'behavior_stimulus':
                        ### would read the pkl here but don't want to add pkl as dependnecy...
                        #with open()
                        #foraging_id =
                        #state_globals['external']['foraging_id'] = foraging_id
                        #if not(foraging_id in state_globals['external']['foraging_id_list']):
                        #    state_globals['external']['foraging_id_list'].append(foraging_id)
                    if name is None:
                        name = 'platform_json'
                    else:
                        files_dict[name] = {'filename': file_name}
                    file_paths_dict[name] = filepath
                    if manifest:
                        manifest.add_to_manifest(filepath, remove_source = False)
                        print(f'{name} location found here: {filepath}')
                else:
                    # error = True
                    # print(f'{name} was not found')#
                    if manifest:
                        print(f'{name} was not found')
                        print(f'{filepath}')
            except Exception as E:
                logging.debug(f'error attempting to add new file: {name}')
    except Exception as E:
        # logging.exception('error attempting to add new files')
        pass
        # don't do anything here because it gums things up - designedto fail in the early stages when there is no lims location yet
    return files_dict, file_paths_dict


def add_probe_dirs(state_globals, manifest=False):
    files_dict, file_paths_dict = get_probe_dirs(state_globals, manifest)
    init_files(state_globals)
    state_globals['files_dict'].update(files_dict)
    state_globals['file_paths_dict'].update(file_paths_dict)


def get_probe_dirs(state_globals, manifest=False):
    slots_in_manifest = []
    files_dict = {}
    file_paths_dict = {}
    for probe in state_globals["external"]["probe_list"]:
        try:
            if not (state_globals["external"]["PXI"]):
                probe_dir = f'{state_globals["openephys_drives"][probe]}/{state_globals["external"]["session_name"]}_probe{probe}'
                if os.path.isdir(probe_dir):
                    lims_key = f"ephys_raw_data_probe_{probe}"
                    if [f"probe_{probe}_surface"] in state_globals["external"] and state_globals["external"][
                        f"probe_{probe}_surface"]:
                        files_dict[lims_key] = {
                            "directory_name": f'{state_globals["external"]["session_name"]}_probe{probe}'
                        }
                        file_paths_dict[lims_key] = probe_dir
                        if manifest:
                            print(f'adding to manifest: {probe_dir}')
                            manifest.add_to_manifest(probe_dir, remove_source = False)
                    else:
                        if manifest:
                            print(f"Probe {probe} not at surface...not adding to the platform json or manifest")
                else:
                    if manifest:
                        print(f"Probe directory not found at {probe_dir}\n")
            else:
                a_probe = list(state_globals["external"]["probe_list"].keys())[0]
                computer, x = os.path.split(state_globals["openephys_drives"][a_probe])
                computer = r"\\" + computer.split(r"/")[2]
                # print("computer:" + computer + ", tail:" + x)
                slot = state_globals["external"]["probe_list"][probe]
                dirname = (
                    state_globals["external"]["session_name"] + "_" + state_globals["external"]["probes_in_slot"][slot]
                )
                drive = state_globals["external"]["PXI"][slot]
                probe_dir, computer = get_probeDir(state_globals, slot, drive)  # os.path.join(computer, drive, dirname)
                if os.path.isdir(probe_dir):
                    name = f"ephys_raw_data_probe_{probe}"
                    dirname = os.path.split(probe_dir)[1]
                    files_dict[name] = {
                        "directory_name": dirname
                    }
                    file_paths_dict[name] = probe_dir
                    if not (slot in slots_in_manifest) and manifest:
                        print(f'adding to manifest: {probe_dir}')
                        manifest.add_to_manifest(probe_dir, remove_source = False)
                        slots_in_manifest.append(slot)
                else:
                    # error = True
                    if manifest:
                        print(f"Probe directory not found at {probe_dir}\n")
        except Exception as E:
            if manifest:
                logging.debug(f'error attempting to add data dir to platform json: {probe}', exc_info=True)
    return files_dict, file_paths_dict


def get_sync_location(state_globals):
    latest = ''
    try:
        # the following is a tempo stop gap to find the latest sync file
        hostname = config['components']['Sync']['host']
        path = f'\\\\{hostname}\\c$\\ProgramData\\AIBS_MPE\\sync\\data'
        latest = None
        latest_time = 0
        for x in os.listdir(path):
            file = f'{path}\\{x}'
            ctime = os.path.getmtime(file)
            if ctime > latest_time:
                latest_time = ctime
                latest = file

        print('latest file is', latest)
    except Exception as E:
        print_error(state_globals, E)
        message = 'Unable to get sync file location'
        alert_text(message, state_globals)
    return latest


def move_files(state_globals):
    try:
        # the following is a tempo stop gap to find the latest sync file

        latest = get_sync_location(state_globals)

        proxy = state_globals['component_proxies']['Sync']
        try:
            # proxy.stop()

            # time.sleep(1)
            dirname, filename = os.path.split(latest)
            extension = os.path.splitext(filename)[1]
            timestamp = dt.now().strftime('%Y%m%d%H%M%S')
            session_ID = state_globals['external']['session_name']
            new_path = os.path.join(dirname, f'{session_ID}_{timestamp}{extension}')
            sync_finished = False
            # MARKER
            message = wfltk_msgs.state_busy(message="Waiting for sync copy to complete.")
            state_globals["resources"]["io"].write(message)
            sync_proxy = state_globals["component_proxies"]["Sync"]
            time.sleep(3)  # wait for the stimulus to start ... (gross)
            logging.info('Attempting to get SYNC state')

            while True:
                try:
                    state = sync_proxy.get_state()
                    logging.info(f'SYNC STATE: {state}')
                    time.sleep(1)
                    if state[0] == "READY":
                        time.sleep(1)
                        logging.info('SYNC is READY, copying files.')
                        latest = get_sync_location(state_globals)
                        logging.info(f'Latest {latest}')
                        if file_created_after_experiment_start(state_globals, latest, modified=True):
                            copy_to = os.path.join(state_globals["external"]["local_lims_location"],
                                                                   f'{state_globals["external"]["session_name"]}.sync')

                            logging.info(f'Copying {latest} to {copy_to}')
                            proxy.copy_arbitrary_file(latest, copy_to)
                            state_globals['external']['status_message'] = 'success'
                        logging.info(f'renaming {latest} to {new_path}')
                        os.rename(latest, new_path)
                        break
                except zmq.error.Again:
                    alert_text('Error communicating with sync.  Sync files might not be copied', state_globals)
                    logging.error('Error communicating with sync.  Sync files might not be copied')
                    break
                except Exception as e:
                    alert_text('Error handling sync file copy.', state_globals)
                    logging.error(f'Error handling sync file copy: {e}')
                finally:
                    logging.info('Done checking SYNC state')
            #import pdb; pdb.set_trace()
            message = wfltk_msgs.state_ready(message="Sync file copy complete.")
            logging.info('SYNC file copy complete')
            state_globals["resources"]["io"].write(message)

        except Exception as e:
            logging.info(f'Sync end failure:{e}!')
            state_globals['external']['status_message'] = f'Sync copy failure:{e}'
            state_globals['external']['component_status']["Sync"] = False
    except Exception as e:
        logging.info(f'Sync proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Sync proxy failure:{e}'
        state_globals['external']['component_status']["Sync"] = False

    # copy the eyetracking file
    # try:
    #    proxy = state_globals['component_proxies']['VideoMon']
    #    try:
    #        proxy.copy_arbitrary_file((state_globals['external']['videomon_file_path'] + '-1.avi'),
    #                                  state_globals['external']['eyetracking_file_path'])
    #        # proxy.stop_display()
    #        state_globals['external']['status_message'] = 'success'
    #    except Exception as e:
    #        print(f'VideoMon eyetracking file copy failure:{e}!')
    #        state_globals['external']['status_message'] = f'Videomon stop failure:{e}'
    #        state_globals['external']['component_status']["VideoMon"] = False
    # except Exception as e:
    #    print(f'VideoMon proxy failure:{e}!')
    #    state_globals['external']['status_message'] = f'Videomon proxy failure:{e}'
    #    state_globals['external']['component_status']["VideoMon"] = False

    # send the path to the Notes App for saving the JSON file

    state_globals['external']['summary_comment'] = 'None'

    newstep_file_path = os.path.join(state_globals['external']['local_lims_location'],
                                     f'{state_globals["external"]["session_name"]}.motor-locs.csv')

    try:
        proxy = state_globals['component_proxies']['Notes']
        try:
            # result = proxy.copyMLog(str(state_globals["external"]["mapped_lims_location"]),
            #                        str(state_globals["external"]["session_name"]))
            print('Now copying mlog')
            copymlog(state_globals)
        except Exception as e:
            print(f'Notes copy log failure:{e}!')
    except Exception as e:
        print(f'Notes proxy failure:{e}!')


def get_mlog_location(state_globals):
    location = False
    try:
        host = r'\\' + config['MVR']['host']
        location = os.path.join(host, R'C\ProgramData\AIBS_MPE\wfltk\logs')
        location = glob.glob(location)[0]
    except Exception as E:
        default = os.path.join(host, R'C\MPM_data\log.csv')
        if location:
            message = f'Failed to find mlog file at path from config: {location}\n\n Using default {default} instead'
            # alert_text(message, state_globals)
            print(message)
        location = default
        print(f'mlog location {location}')
    return location


def rename_mlog(state_globals):
    try:
        old_fullpath = get_mlog_location(state_globals)
        destination = os.path.split(old_fullpath)[0]
        extension = os.path.splitext(old_fullpath)[1]
        timestamp = dt.now().strftime('%Y%m%d%H%M%S')
        new_fullpath = os.path.join(destination, timestamp + extension)
        shutil.copy2(old_fullpath, new_fullpath)
    except Exception as E:
        print_error(state_globals, E)
        message = 'A failure occured while trying to rename the newscale log, Check location and permissions'
        alert_text(message, state_globals)


def copymlog(state_globals):
    print('entered copymlog')
    try:
        original_fullpath = get_mlog_location(state_globals)
        source, filename = os.path.split(original_fullpath)
        destination = state_globals["external"]["mapped_lims_location"]
        command_string = f"robocopy {source} {destination} {filename} /e /xo"
        session_type = state_globals['external']['session_type']
        process_name = 'mlog_copy_process' + session_type
        print(f'copying from filename {filename} source {source} to destination {destination}')
        if not (process_name in global_processes):
            try:
                p = subprocess.check_call(command_string)
                global_processes[process_name] = p
            except Exception as E:
                print(E)
                logging.debug(E)
            old_fullpath = os.path.join(destination, filename)
            new_fullpath = os.path.join(destination, state_globals["external"]["session_name"] + ".motor-locs.csv")
            print(f'renaming file from {old_fullpath} to destination {new_fullpath}')
            if not (os.path.exists(new_fullpath)):
                os.rename(old_fullpath, new_fullpath)
    except Exception as E:
        print_error(state_globals, E)
        message = 'A failure occured while trying to copy and rename the newscale log, Check location and permissions'
        alert_text(message, state_globals)


def save_notes(state_globals):
    try:
        proxy = state_globals['component_proxies']['Notes']

        try:
            result = proxy.saveFile(str(state_globals["external"]["mapped_lims_location"]),
                                    str(state_globals["external"]["session_name"]))
            state_globals["external"]["local_surgery_notes_location"] = os.path.join(
                state_globals["external"]["local_lims_location"], result)
            state_globals["external"]["surgery_notes_name"] = result
            state_globals['external']['status_message'] = 'success'
            state_globals['external']['component_status']["Notes"] = True
        except Exception as e:
            print(f'Notes save failure:{e}!')
            state_globals['external']['status_message'] = f'Notes saveFile Failure:{e}'
            state_globals['external']['component_status']["Notes"] = False
    except Exception as e:
        print(f'Notes proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Notes Proxy Failure:{e}'
        try:
            state_globals['external']['component_status']["Notes"] = False
        except:
            pass


def overrideable_error_state(state_globals, retry_state, override_state=None, message=None):
    if override_state is None:
        print('override_state' + override_state)
        override_state = state_globals['external']['next_state']
        print('override_state' + override_state)
    if not (state_globals['external']['next_state'] == 'overrideable_error_state'):
        state_globals['external']['retry_state'] = retry_state
        state_globals['external']['override_state'] = override_state
        state_globals['external']['next_state'] = 'overrideable_error_state'
    if message:
        alert_text(message, state_globals)


def overrideable_error_state_input(state_globals):
    retry = state_globals['external']['retry_state']
    override = state_globals['external']['override_state']
    handle_2_choice_button('retry', retry, override, state_globals)


def overrideable_error_state_exit(state_globals):
    # state_globals["external"].pop("msg_text")
    pass


def check_experiment_foraging_id(state_globals):
    return {'foraging_id': 'Could not confirm that the foraging ID matches the one for the behavior pkl'}


def check_experiment_stimulus_pkls(state_globals):
    return {}


def alert_from_error_dict(state_globals, failure_dict, primary_key=None, suppress_alert=False):
    total_failure_count = len(failure_dict)
    eye_grabbing_prompt_message = '\n' + '_' * 50 + '\n{}: {}\n' + '_' * 50

    total_failure_count = len(failure_dict)
    additional_failures = total_failure_count

    def message_from_failure_dict(name, description):
        message = f'"{name}" was not: {description}'
        if '_' in name:
            thing, test_description = name.rsplit('_', 1)
            message = f'"{thing}" was not {test_description}: {description}\n\n'
        return message

    first_message = ''
    if not (primary_key == False) or ((total_failure_count - 1) == 0):
        additional_failures = total_failure_count - 1
        if additional_failures > 0:
            additional_failures = str(additional_failures) + ' other'
        try:
            description = failure_dict.pop(primary_key)
            first_message = message_from_failure_dict(primary_key, description)
        except KeyError as E:
            random_key = list(failure_dict.keys())[0]
            description = failure_dict.pop(random_key)
            first_message = message_from_failure_dict(random_key, description)
    # else:
    #    random_key = list(failure_dict.keys())[0]
    #    description = failure_dict.pop(random_key)
    #    first_message = message_from_failure_dict(random_key, description)

    names_string = ", ".join(failure_dict)
    print('_' * 50 + '\nadditional_failures: ' + str(additional_failures))
    if ((total_failure_count - 1) > 0):
        failure_string = failure_dict_to_string(failure_dict)
        other_str = ''
        if not (primary_key == False):
            other_str = 'other '
        detailed_message = '{}There were {} failures total. The {}checks that failed are: \n {} \n\n'.format(
            first_message, additional_failures, other_str, failure_string)
    else:
        detailed_message = f'{first_message}. \n\nThere were no other failures'
    if not (suppress_alert):
        alert_text(detailed_message, state_globals, log_message=False)
    for name, message in failure_dict.items():
        full_string = eye_grabbing_prompt_message.format(name, message)
        logging.debug(f'a validation check failed: {name}')
        print(full_string)
    return detailed_message


def group_matching_failures(failure_dict):
    grouped_dict = {}
    for key in failure_dict:
        try:
            thing, test_description = key.rsplit('_', 1)
            if not (test_description in grouped_dict):
                grouped_dict[test_description] = []
            grouped_dict[test_description].append(thing)
        except ValueError as E:
            if not ('sucessful' in grouped_dict):
                grouped_dict['sucessful'] = []
            grouped_dict['sucessful'].append(key)
    return grouped_dict


def failure_dict_to_string(failure_dict):
    grouped_dict = group_matching_failures(failure_dict)
    failure_string_list = []
    for description, failed_list in grouped_dict.items():
        message = f'--- not {description}: {", ".join(failed_list)}'
        failure_string_list.append(message)
    failure_string = '\n\n'.join(failure_string_list)
    return failure_string


def get_from_config(key_list, default=''):
    if type(key_list) == str:
        key_list = [key_list]
    else:
        key_list = list(key_list)
    try:
        field = config
        for key in key_list:
            field = field[key]
            # print(field)
    except Exception as E:
        join_str = '"]["'
        message = f'Failed to find ["{join_str.join(key_list)}"] in  config. Using default {default} instead'
        print(message)
        # alert_text(message, state_globals)
        field = default
    return field


def settle_timer_enter(state_globals, wait_time=300):
    stop_time = dt.now()
    time_elapsed = (stop_time - state_globals["resources"]["final_depth_timer_start"]).total_seconds()
    total_seconds = max(0, wait_time - time_elapsed)
    state_globals['external']['settle_time_remaining_num'] = total_seconds
    state_globals['external']['settle_time_remaining'] = convert_seconds_to_time_string(total_seconds)
    state_globals["external"]["final_depth_timer_seconds"] = convert_seconds_to_time_string(wait_time)
    print('the total seconds remaining =', total_seconds)


def convert_seconds_to_time_string(seconds):
    try:
        seconds = int(seconds)
        m = floor(seconds / 60)
        s = seconds % 60
        string = f'{m} minutes and {s} seconds'
    except Exception as E:
        string = seconds
        logging.debug('Failed to convert seconds to string with minutes and seconds', exc_info=True)
    return string


def save_platform_json(state_globals, manifest=False):
    state_globals["external"]["test_lims_incoming_location"] = "C:/test_lims_incoming"
    state_globals["external"]["test_lims_trigger_location"] = "C:/ProgramData/AIBS_MPE/neuropixels/lims_trigger"
    state_globals["external"]["platform_json_save_time"] = dt.now().strftime('%Y%m%d%H%M%S')
    if manifest:
        state_globals["external"]["manifest_creation_time"] = dt.now().strftime('%Y%m%d%H%M%S')

    platform = get_platform_fields(state_globals, manifest)

    # create files dict for everything to be added to the platform json

    lims_session = False
    if manifest:
        # do the LIMS TK stuff here...this is for pushing to the real lims directories
        lims_session = limstk.Session("neuropixel", "neuropixels", id=state_globals["external"]["ecephys_session_id"])
        lims_session.path_data["trigger_dir"] = state_globals["external"]["trigger_dir"]
        lims_session.path_data["location"] = state_globals["external"]["lims_location"]
        print(
            f'Doing Real LIMS upload to location:{state_globals["external"]["lims_location"]} '
            f'trigger_dir:{state_globals["external"]["trigger_dir"]}'
        )
        lims_session.trigger_data["sessionid"] = state_globals["external"]["ecephys_session_id"]
        lims_session.trigger_data["location"] = lims_session.path_data["location"]

    error = False

    # add sync file information
    # check that the file exists

    # do this for real now

    add_new_files(state_globals, manifest=lims_session)
    add_probe_dirs(state_globals, manifest=lims_session)

    platform["files"] = state_globals['files_dict']
    # Add the files_dict to the plaftorm json

    # And now dump everything into the platform json file
    if 'local_lims_location' in state_globals["external"]:
        platform_file_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            f'{state_globals["external"]["session_name"]}_platformD1.json'
        )

        # make the platform json human readable
        # pprint(platform)
        try:
            readable_platform = json.dumps(platform, sort_keys=True, indent=4)
            write_file = open(platform_file_path, "w")
            write_file.write(readable_platform)
            write_file.close()
        except Exception as E:
            message = 'Unable to write platform json. Is the data directory acessible (shared if necessary) with the necessary permissions?'
            alert_text(message, state_globals)

    # and now add this to the trigger file

    # create the sync report file
    if manifest:
        lims_session.add_to_manifest(platform_file_path, remove_source = False)
        # create_sync_report(state_globals, lims_session)

        trigger_file_name = f'{state_globals["external"]["session_name"]}'
        print(f">>>> trigger_file_name:{trigger_file_name}")

        time.sleep(1)
        try:
            lims_session.write_manifest(trigger_filename=trigger_file_name)
        except Exception as e:
            print(f"lims session error:{e}")


def create_sync_report(state_globals, lims_session):
    try:
        sync_report_name = f'{state_globals["external"]["session_name"]}_report.pdf'
        sync_report_path = os.path.join(state_globals["external"]["local_lims_location"], sync_report_name)
        kill_open_report()
        # TODO this doesn't work
    except Exception as E:
        print("Failed to kill the sync report")
    try:
        sync_report = NeuropixelsReport(state_globals["external"]["local_lims_location"])
        sync_report.to_pdf(sync_report_path)
    except Exception as E:
        pass
    try:
        if os.path.isfile(sync_report_path):
            lims_session.add_to_manifest(sync_report_path, remove_source = False)
            print(f"sync_report_path:{sync_report_path}")
        else:
            error = True
            print(f"sync_report file not found at location:{sync_report_path}\n")
    except Exception as E:
        pass


def get_platform_fields(state_globals, manifest=False):
    # create the platform JSON file

    # print("Getting platform fields")

    platform = {}

    platform_fields_mapping = {
        'foraging_id': '',
        'foraging_id_list': '',
        'stimulus_name': '',
        'script_name': '',
        'mouse_weight_pre_float': '',
        'mouse_weight_post': '',
        'rig_id': '',
        'operatorID': 'user_id',
        'HeadFrameEntryTime': '',
        'HeadFrameExitTime': '',
        'CartridgeLowerTime': '',
        'ProbeInsertionStartTime': '',
        'ProbeInsertionCompleteTime': '',
        'ExperimentStartTime': '',
        'ExperimentCompleteTime': '',
        'probe_A_DiI_depth': '',
        'probe_B_DiI_depth': '',
        'probe_C_DiI_depth': '',
        'probe_D_DiI_depth': '',
        'probe_E_DiI_depth': '',
        'probe_F_DiI_depth': '',
        'ExperimentNotes': '',
        'DiINotes': '',
        'InsertionNotes': '',
        'water_supplement': '',
        'workflow_start_time': '',
        'platform_json_save_time': '',
        'manifest_creation_time': '',
        'workflow_complete_time': '',
        'mouse_weight_pre': '',
        "water_calibration_heights": '',
        "water_calibration_volumes": '',
        "wheel_height": '',
    }

    def platform_failed_message(name, manifest, state_globals=None):
        message = f'Failed to include {name} in the platform json...'
        if manifest and (state_globals and not ('hab' in state_globals['external']['session_type'].lower())):
            logging.debug(message)
            # alert_text(message)
            print(message)

    for platform_key, state_globals_external_key in platform_fields_mapping.items():
        try:
            if state_globals_external_key == '':
                state_globals_external_key = platform_key
            value = state_globals['external'][state_globals_external_key]
            # print(f'Field found... {state_globals_external_key} = {value}')
            if not (value is None):
                # if '/' in value:
                #    print(f'Whoops, we found a slash... {state_globals_external_key} = {value}')
                platform[platform_key] = value
        except Exception as E:
            platform_failed_message(platform_key, manifest, state_globals)

    try:
        platform['workflow'] = state_globals['resources']['workflow']['import']
    except Exception as E:
        platform_failed_message('workflow', manifest, state_globals)

    try:
        platform["wfl_version"] = state_globals["resources"]["workflow"]["wfl_version"]
    except Exception as E:
        platform_failed_message('wfl_version', manifest, state_globals)
    # add the hardware configuration]
    try:
        platform["HardwareConfiguration"] = state_globals["hardware_config"]
    except Exception as E:
        platform_failed_message('HardwareConfiguration', manifest, state_globals)

    return platform


def water_mouse_enter(state_globals):
    mouse_weight = float(state_globals['external']['mouse_weight_post'])
    get_water_supplement(state_globals, mouse_weight)


def water_mouse_input(state_globals):
    print('npxc.water_mouse_enter')
    state_globals['external']['next_state'] = 'check_files1'
    print(state_globals['external']['next_state'])
    handle_2_choice_button('overwrite_fid', 'foraging_ID_error', 'check_files1', state_globals)

    if not (state_globals['external']['foraging_id']):
        state_globals['external']['next_state'] = 'foraging_ID_error'
        print(state_globals['external']['next_state'])


def water_mouse_exit(state_globals):
    if not ('session_finalized' in state_globals['external']) or not (state_globals['external']['session_finalized']):
        try:
            mouse_director_proxy.set_user_id(state_globals["external"]["user_id"])
            mouse_director_proxy.set_mouse_id(state_globals["external"]["mouse_id"])
            # TODO @Ross I've decided to leave these together. If the mouse ID is incorrect then we shouldn't finalize automatically
            # What we should really do is grab all the values since thats the only way to know if they are set properly -
            # assert(mouse_director_proxy.get_user_id() == state_globals["external"]["user_id"]
            # assert(mouse_director_proxy.get_mouse_id() == state_globals["external"]["mouse_id"]
            # assert(mouse_director_proxy.get_mouse_weight() == state_globals["external"]["mouse_weight_post"]
            mouse_director_proxy.finalize_session('')
            state_globals['external']['session_finalized'] = True

        except Exception as E:
            message = 'Unable to finalize session. Please check the user ID, mouse ID and mosue weight and then finalize the session manually'
            overrideable_error_state(state_globals, 'water_mouse', override_state='check_files1', message=message)
    else:
        message = 'The WSE already finalized the session, so it did not attempt to finalize again.'
        alert_text(message, state_globals)
    move_files(state_globals)
    save_notes(state_globals)
    save_platform_json(state_globals, manifest=False)


def foraging_ID_error_input(state_globals):
    if not (state_globals['external']['foraging_id']):
        state_globals['external']['next_state'] = 'foraging_ID_error'
        handle_2_choice_button('retry', 'foraging_ID_error', 'check_files1', state_globals)
    else:
        state_globals['external']['next_state'] = 'check_files1'


def load_mouse_enter(state_globals):
    if 'first' in state_globals['external']['full_session_type']:
        message = ("Since this is the first session you will have to "
                   "determine the wheel height for this mouse. "
                   "Set something that looks comfortable now, but plan to adjust during the expeirment")
        alert_text(message, state_globals)
    elif not ('wheel_height' in state_globals['external']):
        message = ("No wheel height found you will have to "
                   "determine the wheel height for this mouse. "
                   "Set something that looks comfortable now, but plan to adjust during the expeirment")
        alert_text(message, state_globals)

    if '1_day_before' in state_globals['external']['full_session_type']:
        message = ("Since you are tapping the coverslip with the probes today, "
                   "it is particularly important to make sure that the coverslip is cleaned")
        alert_text(message, state_globals)


    if 'day2' in state_globals['external']['full_session_type'].lower():
        alert_text('Remove extra agarose and ensure adequate silicon oil.', state_globals)

def load_mouse_behavior(state_globals):
    try:
        print('Attempting to send mouse ID to mouse director')
        mouse_director_proxy.set_mouse_id(state_globals["external"]["mouse_id"])
    except Exception as E:
        alert_string = f'Failed to send mouse ID to mouse director'
        alert_text(alert_string, state_globals)

    try:
        state_globals['external']['mouse_weight_pre_float'] = float(state_globals['external']['mouse_weight_pre'])
        print(f"Converted mouse_weight_pre sring to float - {state_globals['external']['mouse_weight_pre_float']}")
    except Exception as E:
        message = 'The mouse weight must be a number. Please enter a number'
        return message
    else:
        if 'previous_mouse_weight_pre' in state_globals['external']:
            try:
                old_weight = float(state_globals['external']['previous_mouse_weight_pre'])
                new_weight = float(state_globals['external']['mouse_weight_pre_float'])
                mouse_weight_bound = get_from_config(['mouse_weight_bound'], default=.5)
                if abs(old_weight - new_weight) > mouse_weight_bound:
                    message = (f"It looks like the current weight {new_weight} is more than {mouse_weight_bound}"
                        f" from the weight last session of {old_weight}. Are you sure this is the right mouse?")

                    overrideable_error_state(state_globals, 'load_mouse', override_state='lower_probe_cartridge',
                                             message=message)
            except Exception as E:
                message = 'Unable to verify mouse weight is reasonable'
                alert_text(message, state_globals)
        else:
            try:
                new_weight = float(state_globals['external']['previous_mouse_weight_pre'])
                lower = float(get_from_config(['mouse_weight_upper_bound'], default=15))
                upper = float(get_from_config(['mouse_weight_lower_bound'], default=35))
                if not ((new_weight > lower) and (new_weight < upper)):
                    message = (
                        f"It looks like the current weight {new_weight} is more than {upper} or less than {lower}"
                        f" Are you sure it was typed correctly?")

                    overrideable_error_state(state_globals, 'load_mouse', override_state='lower_probe_cartridge',
                                             message=message)
            except Exception as E:
                message = 'Unable to verify mouse weight is reasonable'
                alert_text(message, state_globals)


def pretest_wrapup(state_globals):
    # wrap thing sup in the same order as the experiment without validating pkls
    session_type = 'pretest'
    copy_stim_pkls(state_globals, session_type)

    wait_time = get_from_config(['pretest_stream_check_wait_time'], default=12)
    failed = check_data_stream_size(state_globals, wait_time=wait_time)
    # if (dt.now() - state_globals["external"]["pretest_start_time"]).seconds > 120:
    #    failed = check_data_stream_size(state_globals, wait_time=15)
    #    if 'behavior_growing' in failed:
    #        message = 'The videos may have stopped recording. Please check that they exceed 2 minutes'
    #        alert_text(message, state_globals, log_level=logging.INFO)
    # else:
    #    message = 'Pretest ended before 2 minutes. Unable to confirm that video was still growing after 2 minutes'
    #    alert_text(message, state_globals)

    stop_common_experiment_monitoring(state_globals)
    videomon_copy_wrapup(state_globals)

    move_files(state_globals)

    run_validation(state_globals, 'pretest')
    failed.update(get_validation_results(state_globals, 'pretest'))

    # failed.update(state_globals['external']['failure_messages'])
    if failed:
        state_globals['external']['next_state'] = 'pretest_error'
        alert_from_error_dict(state_globals, failed, primary_key=False)


streams_message = ("It looks like one of the streams may have stopped.\n\n"
                "FIRST verify this manually. Look at the gui for evidence that its recording.\n"
                "If it appears to be recording, then look for the file and see if it doesn't exist or if it actually isn't growing.\n"
                "IF this is the case, you will need to start it manually and restart the stim, or restart the entire session.\n"
                     "Reinitiating from the WSE causes issues so this is disabled."
            )

def monitor_experiment_input(state_globals):
    """
    Input test function for state monitor_experiment
    """
    # monitor the stim process
    print("monitor_experiment input")

    # state_globals['external']['next_state'] = 'end_experiment'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    state_globals['external']['next_state'] = 'end_experiment'
    advancing = False
    if 'monitor_experiment' in state_globals['external'] and state_globals['external']['monitor_experiment']:
        state_globals['external']['next_state'] = 'monitor_experiment'
        failed = False
        try:
            print('Attempting to monitor experiment')
            print(state_globals['external']['exp_monitor_time'])
            exp_monitor_time = float(state_globals['external']['exp_monitor_time'])
            exp_monitor_time = exp_monitor_time * 60
            print(exp_monitor_time)
        except Exception as E:
            message = 'The experiment monitor time must be a number. Please enter a number'
            logging.debug(message, exc_info=True)
            return message
        else:
            failed = monitor_experiment(state_globals, wait_time=exp_monitor_time)
            if failed:
                print('monitor experiment failed!')
                print(failed)
                for key in failed:
                    if 'camstim' in key:
                        #npxc.alert_from_error_dict(state_globals, error_dict)
                        state_globals['external']['next_state'] = 'end_experiment'
                        advancing = True

                #message = streams_message
                #alert_text(message, state_globals)
                fail_message_1 = alert_from_error_dict(state_globals, failed, primary_key=False)

    else:
        advancing = True
        if camstim_running(state_globals):
            time.sleep(20)
            if camstim_running(state_globals):
                message = 'Camstim seems to be running a stimulus. are you sure the stim is done?'
                overrideable_error_state(state_globals, 'experiment_running_timer', override_state='end_experiment',
                                              message=message)
    if advancing:
        print('attempting to copy pkls')
        session_type = state_globals['external']['full_session_type']
        if 'hab' in session_type:
            session_type = state_globals['external']['session_type']
        copy_stim_pkls(state_globals, session_type)
        failed = False
        try:
            failed = check_pkls(state_globals, session_type)
        except Exception as E:
            message = 'Failed to check if all pkls were produced'
            alert_text(message, state_globals)
            traceback.print_tb(E.__traceback__)
        if failed:
            message = alert_from_error_dict(state_globals, failed)
            overrideable_error_state(state_globals, 'experiment_running_timer', override_state='end_experiment')
    # foraging_failed = check_experiment_foraging_id(state_globals)
    # failure_dict = check_experiment_stimulus_pkls(state_globals)
    # failure_dict.update(foraging_failed)
    # message = alert_from_error_dict(state_globals, failure_dict, primary_key='foraging_id', suppress_alert=True)
    # state_globals['external']['next_state'] = 'end_experiment'
    # if foraging_failed:
    #    overrideable_error_state(state_globals, 'experiment_running_timer', message=message)
    #    state_globals['external']['next_state'] = 'foraging_ID_error'
    # elif pkls_failed:
    #    overrideable_error_state(state_globals, 'experiment_running_timer', message=message)
    save_platform_json(state_globals, manifest=False)


def probes_need_cleaning(state_globals):
    probes_need_cleaning = False
    exp_yesterday = 'probes_need_cleaning' in state_globals['external'] and state_globals['external'][
        'probes_need_cleaning']
    exp_later_today = dt.today().weekday() == 2
    if exp_yesterday:
        message = ("It looks like there was an experiment yesterday. "
                   "Please make sure the probes have been dipped in MilliQ water")
        alert_text(message, state_globals)
        probes_need_cleaning = True
    if exp_later_today:
        message = ("It looks like there may be an experiment later today. "
            "Please make sure to dip the probes in MilliQ water")
        alert_text(message, state_globals)
        probes_need_cleaning = True
    return probes_need_cleaning
