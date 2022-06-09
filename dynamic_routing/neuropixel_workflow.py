# -*- coding: latin-1 -*-
import datetime
import inspect
import json
import logging
import os
import socket
import threading
import time
import traceback
import webbrowser
from datetime import datetime as dt
from importlib import reload
from pprint import pformat

import limstk
import requests
import zmq
from wfltk import middleware_messages_pb2 as wfltk_msgs
from zro import Proxy

from neuropixel import npxcommon as npxc

# -------------- experiment-specific objects --------------

config: dict = mpeconfig.source_configuration("dynamic_routing")
experiment = DynamicRouting()

# ---------------- Network Service Objects ----------------

io: mpetk.aibsmw.ioio.io.ZMQHandler

mvr: MVRConnector
camstim_agent: zro.Proxy
sync: zro.Proxy
mouse_director: zro.Proxy

# ------------------- UTILITY FUNCTIONS -------------------

def fail_state(message: str, state: dict):
    """
    Set the current transition to failed and fill out the dictionary with the appropriate information
    :param message: the fail reason
    :param state: the state dictionary
    :return:
    """

    if 'fail_state_override' in state['external'] and state['external']['fail_state_override']:
        state['external'].pop('fail_state_override', None)
    else:
        current_frame = inspect.currentframe()
        calling_frame = inspect.getouterframes(current_frame, 2)[1][3]
        state_name = calling_frame[: calling_frame.rfind("_")]
        logging.info(f"{state_name} failed to advance: {message}")
        state['external']['alert'] = True
        state["external"]["transition_result"] = False
        state["external"]["next_state"] = state_name
        message = npxc.append_alert(message, state)
        state["external"]["msg_text"] = message


def skip_states(state, states_skipped, fields_skipped=()):
    for field in fields_skipped:
        state['external'][field] = True
    for state_name in states_skipped:
        for transition in ['enter', 'input', 'exit']:
            func_name = state_name + '_' + transition
            default_func_name = 'default_' + transition
            if func_name in globals():
                method_to_call = globals()[func_name]
                method_to_call(state)
            else:
                method_to_call = globals()[default_func_name]
                method_to_call(state, state_name)


def state_transition(state_transition_function):
    def wrapper(state, *args):
        try:
            #reload(npxc)
            transition_type = state_transition_function.__name__.split('_')[-1]
            if ((transition_type == 'input') or (transition_type == 'revert')) and ('msg_text' in state["external"]):
                state["external"].pop("msg_text")
            if args:
                state_transition_function(state, args)
            else:
                state_transition_function(state)
            npxc.save_platform_json(state, manifest=False)
        except Exception as e:
            npxc.print_error(state, e)
            message = f'An exception occurred in state transition {state_transition_function.__name__}'
            logging.debug(message)
            npxc.alert_text(message, state)
        return None

    return wrapper


def component_check(state: dict) -> list:
    failed = []
    for name, proxy in state['component_proxies'].items():
        try:
            logging.info(f'{name} uptime: {proxy.uptime}')
            state["external"]["component_status"][name] = True
        except Exception:
            state["external"]["component_status"][name] = False
            logging.debug(f'Cannot communicate with {name}.')
            failed.append(name)
    return failed


# function to interlace the left/right images into one image and save to a path
def interlace(left, right, stereo):
    npxc.interlace(left, right, stereo)


def handle_message(message_id, message, timestamp, io):
    npxc.handle_message(message_id, message, timestamp, io)

def connect_to_services(state):
    """    The expectation is these services are on (i.e., you have started them with RSC).  If the connection fails you can
    send a message to the user with the fail_state() function above.
    """
    global io
    io = state['resources']['io']
    
    component_errors = []
    
    io.write(ephys.start_ecephys_acquisition())
    #  Here we either need to test open ephys by trying to record or we get the status message.  awaiting testing.
    global mvr
    try:
        if not mvr._mvr_connected:
            mvr = MVRConnector(args=config['MVR'])
            logging.info("Failed to connect to mvr")
            component_errors.append(f"Failed to connect to MVR on {config['MVR']}")
    except Exception:
        component_errors.append(f"Failed to connect to MVR.")

    global camstim_agent
    try:
        service = config['camstim_agent']
        camstim_agent = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'], serialization='json')
        logging.info(f'Camstim Agent Uptime: {camstim_agent.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Camstim Agent.")

    global sync
    try:
        service = config['sync']
        sync = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'])
        logging.info(f'Sync Uptime: {sync.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Sync.")

    
    global mouse_director
    try:
        service = config['mouse_director']
        mouse_director = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'])
        logging.info(f'MouseDirector Uptime: {mouse_director.uptime}')
    except Exception:
        # component_errors.append(f"Failed to connect to MouseDirector.")
        # TODO MDir currently not working - switch this line back on when fixes
        logging.info(" ** skipping connection to MouseDirector **")
        
    
    #  At this point, You could send the user to an informative state to repair the remote services.
    if component_errors:
        fail_state('\n'.join(component_errors), state)

# ------------------- State Transitions -------------------
@state_transition
def default_enter(state, label):
    npxc.default_enter(state, label)


@state_transition
def default_input(state, label):
    npxc.default_input(state, label)


@state_transition
def default_exit(state, label):
    npxc.default_exit(state, label)


@state_transition
def initialize_enter(state):
    state['external']['session_type'] = 'behavior_experiment'
    state['external']['msg_text'] = 'No message defined.'
    Processing_Agents = npxc.get_processing_agents(state)
    for agent, params in Processing_Agents.items():
        key = 'Neuropixels Processing Agent '+agent
        if not(key in npxc.config['components']):
            params['desc'] = key
            npxc.config['components'][key] = params

    key = 'Neuropixels Processing Assistant'
    if not(key in npxc.config['components']):
            computer = npxc.config['components']['OpenEphys']['host']
            port = '1212'
            npxc.config['components'][key] = {'desc': key,
                                  'host': computer,
                                  'port': port,
                                  'version': '0.0.1'}

    npxc.initialize_enter(state)


@state_transition
def initialize_input(state):
    """
    Input test function for state initialize
    """
    npxc.initialize_input(state)

    connect_to_services(state)
    """
    global mouse_director
    md_host = npxc.config['components']['MouseDirector']['host']
    md_port = npxc.config['components']['MouseDirector']['port']
    mouse_director = Proxy(f'{md_host}:{md_port}')

    global camstim_agent
    host = npxc.config["components"]["Stim"]["host"]
    port = npxc.config["components"]["Stim"]["port"]
    state['mtrain_text'] = pformat(npxc.config['MTrainData'])
    camstim_agent = Proxy(f"{host}:{port}", serialization="json")

    global io
    io = state["resources"]["io"]

    result = limstk.user_details(state["external"]["user_id"])


    if 'components_skip' in state['external'] and state['external']['components_skip']:
        failed = False
    else:
        failed = npxc.confirm_components(state)
    """

    beh_mon_str_def = 'Factors that interfere with behavior completely like feces on the lickspout or the chin/paws excessively bumping the spout should be amended'
    state['external']['behavior_monitoring_string'] = npxc.get_from_config(['behavior_monitoring_string_exp'], default=beh_mon_str_def)
    state['external']['dii_description'] = npxc.get_from_config(['dii_description'], default='CM-DiI 100%')

    defaults = ['day1', 'day2']
    key_list = ['MTrainData', 'Experiment_Sessions']
    experiment_sessions = npxc.get_from_config(key_list, defaults)
    #experiment_sessions = []
    #try:
    #    experiment_sessions = npxc.config['MTrainData']['Experiment_Sessions']
    #except Exception as E:
    #    message = f'Failed to find experiment sessions in  config. Using default instead'
    #    print(message)
    #    #npxc.alert_text(message, state)
    #    experiment_sessions = ['day1', 'day2']

    state["external"]["session_type_option_string"] = ', '.join(experiment_sessions)
    state["external"]["session_types_options"] = experiment_sessions

    state["external"]["next_state"] = "scan_mouse_id"
    if failed:
        alert_string = f'The following proxies are not available: {", ".join(failed)}'
        npxc.overrideable_error_state(state, 'initialize', 'scan_mouse_id', message=alert_string)
    try:
        if result != -1:
            state["external"]["lims_user_id"] = result[0]["id"]
            state["external"]["status_message"] = "success"
            state["external"]["local_log"] = f'User {state["external"]["user_id"]} found in LIMS'
        else:
            print('Failed user ID test')
            fail_state(f'No LIMS ID for User:{state["external"]["user_id"]} found in LIMS', state)
    except (KeyError, IndexError):
        fail_state(f'No LIMS ID for User:{state["external"]["user_id"]} found in LIMS', state)

    npxc.probes_need_cleaning(state)

@state_transition
def components_error_input(state):
    npxc.components_error_input(state, 'scan_mouse_id')


@state_transition
def prepare_for_pretest_enter(state):
    npxc.prepare_for_pretest_input(state)


@state_transition
def prepare_for_pretest_input(state):
    pass


@state_transition
def check_data_drives_input(state):
    for slot in state["external"]["PXI"]:
        state["external"]["PXI"][slot] = state["external"][f"slot_{slot}_drive"]




@state_transition
def start_pretest_input(state):
    state['external']['session_name'] = dt.now().strftime("%Y%m%d%H%M%S") + '_pretest'
    state["external"]["local_lims_location"] = os.path.join(state["external"]["local_lims_head"],
                                                                    state['external']['session_name'])
    os.makedirs(state["external"]["local_lims_location"], exist_ok=True)
    state["external"]["mapped_lims_location"] = state["external"]["local_lims_location"]
    state["external"]["pretest_start_time"] = dt.now()
    npxc.set_open_ephys_name(state)
    logging.info('starting monitoring with video prefix = pretest')
    npxc.start_common_experiment_monitoring(state, video_prefix='pretest')
    npxc.start_pretest_stim(state)

    foraging_id, stimulus_name, script_path = npxc.get_stim_status(camstim_agent, state)
    npxc.verify_script_name(state, stimulus_name)

    failed = npxc.establish_data_stream_size(state)
    #state['external']['failure_messages'] = failed or {}
    if failed:
        fail_message_1 = npxc.alert_from_error_dict(state, failed, primary_key=False)
        logging.debug(fail_message_1)


@state_transition
def pretest_input(state):
    if not('water_calibration_heights' in state["external"]):
        state["external"]["water_calibration_heights"] = []

    if not('water_calibration_volumes' in state["external"]):
        state["external"]["water_calibration_volumes"] = []

    height_match = False
    mass_match = False
    if len(state["external"]["water_calibration_heights"]):
        height_match = state["external"]["water_height"] == state["external"]["water_calibration_heights"][-1]
        mass_match = state["external"]["water_mass"] == state["external"]["water_calibration_volumes"][-1]

    if not(height_match) or not(mass_match):
        try:
            water_height = float(state["external"]["water_height"])
            water_mass = float(state["external"]["water_mass"])

            state["external"]["water_calibration_heights"].append(water_height)
            state["external"]["water_calibration_volumes"].append(water_mass)
            key_list = ['expected_water_mass']
            expected_water_mass = npxc.get_from_config(key_list, default=0.050)
            key_list = ['acceptable_water_mass_diff']
            acceptable_water_mass_diff = npxc.get_from_config(key_list, default=0.005)
            if abs(water_mass -expected_water_mass) > acceptable_water_mass_diff and not(water_mass==0):
                message = f'The reported water mass of {water_mass} is more than {acceptable_water_mass_diff} from the expected mass of {expected_water_mass}. Please calibrate the solonoid or adjust the level in the reservior syringe.'
                npxc.alert_text(message, state)
        except Exception as E:
            message = 'The values entered must be numbers. Please enter a number'
            fail_state(message, state)
            return

    camstim_running = npxc.camstim_running(state)
    if camstim_running:
        message = 'Camstim seems to be running a stimulus. are you sure the stim is done?'
        npxc.alert_text(message, state)
        state['external']['next_state'] = 'pretest_stim_finished_error'
        return

    npxc.pretest_wrapup(state)


@state_transition
def pretest_exit(state):
    pass


@state_transition
def pretest_stim_finished_error_input(state):
    npxc.handle_2_choice_button('pretest_stim_wait', 'pretest', 'configure_hardware_videomon', state)
    if state['external']['next_state'] == 'configure_hardware_videomon':
        npxc.pretest_wrapup(state)

@state_transition
def pretest_error_input(state):
    npxc.pretest_error_input(state)


@state_transition
def scan_mouse_id_input(state):
    """
    Input test function for state initialize
    """
    comp_id = os.environ.get('aibs_comp_id', socket.gethostname())
    mouse_id = state["external"]["mouse_id"]
    user_id = state["external"]["user_id"]

    logging.info(f'MID, {mouse_id}, UID, {user_id}, BID, {comp_id}, Received', extra={'weblog':True})

    state["external"]["local_log"] = f'Mouse ID :{mouse_id}'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    state["external"]["clear_sticky"] = True

    #npxc.start_ecephys_acquisition(state)

    try:
        result = limstk.donor_info(mouse_id)
        if "dummy_mode" in state["external"] and (state["external"]["dummy_mode"] == True):
            trigger_dir = npxc.config['dummy_trigger_dir']
        else:
            if "project" in result[0]["specimens"][0]:
                trigger_dir = result[0]["specimens"][0]["project"]["trigger_dir"]
            else:
                trigger_dir = state["external"]["trigger_dir"]

        local_trigger_dir = npxc.config['local_trigger_dir']
        state["external"]["dummy_mode"] = False

    except limstk.LIMSUnavailableError:
        message = f'Could not retrieve donor_info for {mouse_id} from LIMS.'
        fail_state(message, state)
        return

    npxc.assess_previous_sessions(state)
    session_dict = state['external']['exp_sessions']
    session_count = npxc.count_sessions(session_dict, state['external']['mouse_id'])
    guess_exp_day = 'Day1'
    if session_count:
        guess_exp_day = 'Day2'
    state['external']['entered_experiment_day'] = guess_exp_day

    state["external"]["local_trigger_dir"] = local_trigger_dir
    state["external"]["lims_specimen_id"] = result[0]["specimens"][0]["id"]
    state["external"]["specimen.Name"] = result[0]["name"]
    state["external"]["lims_project_id"] = result[0]["specimens"][0]["project_id"]
    state["external"]["Project.Trigger_dir"] = trigger_dir

    if "project" in result[0]["specimens"][0]:
        state["external"]["Project.Code"] = result[0]["specimens"][0]["project"].get("code", "No Project Code")
    else:
        state["external"]["Project.Code"] = "No Project Code"

    state["external"]["Project.Code1"] = state["external"]["Project.Code"]
    state["external"]["Project.Code2"] = state["external"]["Project.Code"]
    state["external"]["Project.Code3"] = state["external"]["Project.Code"]
    state["external"]["Project.Code4"] = state["external"]["Project.Code"]

    # generate a session name
    session_name_timestamp = dt.now().strftime("%Y%m%d")
    session_name_string = f'{state["external"]["mouse_id"]}_{session_name_timestamp}'
    logging.info(f'Session name: {session_name_string}')
    state["external"]["session_name"] = session_name_string
    state["external"]["sessionNameTimestamp"] = session_name_timestamp
    state['external']['Auto.Date.String'] = dt.now().strftime("%Y%m%d")
    state['external']['Auto.Date.String1'] = state['external']['Auto.Date.String']
    state['external']['next_state'] = 'LIMS_request'

    if 'use_auto' in state['external'] and state['external']['use_auto']:
        states_skipped = ['LIMS_request', 'date_string_check']
        fields_skipped = ['auto_generated_date_string', 'Project.Code.lims']
        skip_states(state, states_skipped, fields_skipped)
        state['external']['next_state'] = 'check_experiment_day'


@state_transition
def LIMS_request_enter(state):
    """
    Entry function for state initialize
    """
    state['external']['Manual.Project.Code'] = state['external']['Project.Code']



@state_transition
def LIMS_request_input(state):
    """
    Input test function for state initialize
    """
    if 'Project.Code.new' in state['external'] and state['external']['Project.Code.new']:
        state['external']['Project.Code'] = state['external']['Manual.Project.Code']
    try:
        result = limstk.projects_validation(state["external"]["Project.Code"])
        if result:
            state["external"]["status_message"] = "success"
            state["external"][
                "local_log"
            ] = f'Project.Code:{state["external"]["Project.Code"]} validated'
        else:
            state["external"][
                "local_log"
            ] = f'Project.Code:{state["external"]["Project.Code"]} NOT validated'

            state["external"]["status_message"] = f"Failure:Project.Code Validation Error"

        state["external"]["transition_result"] = True
    except limstk.LIMSUnavailableError:
        fail_state(f'Error validating project code {state["external"]["Project.Code.LIMS"]} in LIMS', state)


@state_transition
def LIMS_request_revert(state):
    ...

def date_string_check_enter(state):
    state['external']['Manual.Date.String'] = state['external']['Auto.Date.String']


@state_transition
def date_string_check_input(state):
    """
    Input test function for state initialize
    """
    if state["external"]["dummy_mode"]:
        result = limstk.isi_experiment_test(state["external"]["mouse_id"])
    else:
        result = limstk.isi_experiment_prod(state["external"]["mouse_id"])

    if not result:
        fail_state(f'Could not find an ISI experiment for mouse id:{state["external"]["mouse_id"]} failure', state)
        state["external"]["next_state"] = "lims_abort_confirm"
        return
    else:
        state["external"]["next_state"] = "check_experiment_day"

    isi_experiments_list = []
    target_list = []
    isi_id_list = []

    for item in result[0]["isi_experiments"]:
        isi_name = item["name"]
        state_local = item["workflow_state"]
        target_list = [item["targets"]]
        if len(target_list) >= 1:
            target_str = "Target Data Available"
        else:
            target_str = "No Target Data Found"

        isi_id = item["id"]
        experiment_str = f"{isi_name}   state:{state_local}   targets:{target_str}"
        isi_experiments_list.append(experiment_str)
        target_list.append(target_list)
        isi_id_list.append(isi_id)

    state["external"]["ISI_experiments"] = isi_experiments_list
    state["external"]["ISI_targets"] = target_list
    state["external"]["ISI_ids"] = isi_id_list
    target_path = f'/{result[0]["target_map_image_path"]}'
    overlay_path = f'/{result[0]["isi_image_overlay_path"]}'
    state["external"]["isi_image_path"] = target_path
    state["external"]["isi_overlay_path"] = overlay_path
    state["external"][
        "local_log"] = f'LIMS pull for isi experiments associated with mouse id:{state["external"]["mouse_id"]} success!'
    """
    Input test function for state initialize
    """
    # lots of LIMS interactions to be added here when service is available
    # find which experiment was selected:
    selected_index = 0
    # TODO - Have it actually select the ISI map that we put our coordinates on?
    # for x in range(len(state["external"]["ISI_experiments"])):
    #    if state["external"]["ISI_experiments"][x] == state["external"]["isi_experiment_selected"]:
    #        selected_index = x
    #        break

    project_name = state["external"].get("Project.Code", None)
    project_id = state["external"]["lims_project_id"]
    if project_name:
        try:
            url = "http://lims2/projects.json?code=" + project_name  # TODO: Configure this
            lims_result = requests.get(url)
            project_id = json.loads(lims_result.text)[0]["id"]
        except (KeyError, IndexError):
            fail_state(f"Error getting project id for project {project_name}", state)
            return

    state["external"]["selected_isi_id"] = state["external"]["ISI_ids"][selected_index]

    # create a unique name for the creating the ecephys session
    if 'auto_generated_date_string' in state['external'] and state['external']['auto_generated_date_string']:
        session_name_timestamp = state['external']['Auto.Date.String']
    else:
        session_name_timestamp = state['external']['Manual.Date.String']
    session_name_string = f'{state["external"]["mouse_id"]}_{session_name_timestamp}'
    state["external"]["session_name"] = session_name_string
    state["external"]["sessionNameTimestamp"] = session_name_timestamp

    timestamp = dt.now().strftime("%Y%m%d%H%M%S")
    name = f'{timestamp}_{state["external"]["lims_user_id"]}'
    request_json = {
        "specimen_id": state["external"]["lims_specimen_id"],
        "project_id": project_id,
        "isi_experiment_id": state["external"]["selected_isi_id"],
        "name": name,
        "operator_id": state["external"]["lims_user_id"],
    }

    url = "http://lims2/observatory/ecephys_session/create"
    response = requests.post(url, json=request_json)
    decoded_dict = json.loads(response.content.decode("utf-8"))
    ecephys_session_id = str(decoded_dict["id"])
    state["external"]["ecephys_session_id"] = ecephys_session_id
    state["external"]["session_name"] = f'{ecephys_session_id}_{state["external"]["session_name"]}'
    state['external']['non_pretest_session_name'] = state["external"]["session_name"]

    # create direction for session ID on local drive
    session_name_directory = os.path.join(
        state["external"]["local_lims_head"], state["external"]["session_name"]
    )
    state["external"]["session_name_directory"] = session_name_directory
    local_lims_location = session_name_directory
    os.makedirs(local_lims_location, exist_ok=True)

    try:
        notes_proxy = state["component_proxies"]["Notes"]
        notes_proxy.setID(str(state["external"]["mouse_id"]), str(state["external"]["session_name"]))
        notes_proxy.setNoSurgery(True)
        state["external"]["status_message"] = "success"
        state["external"]["component_status"]["Notes"] = True
    except KeyError:
        fail_state('SurgeryNotes proxy is not defined.', state)
        state["external"]["component_status"]["Notes"] = False
        return
    except Exception:
        fail_state('Error setting mouse and session name in SurgeryNotes', state)
        state["external"]["component_status"]["Notes"] = False
        return

    mapped_lims_location = f"{npxc.config['mapped_lims_location']}/{state['external']['session_name']}"
    state["external"]["mapped_lims_location"] = mapped_lims_location
    state["external"]["local_lims_location"] = local_lims_location
    state["external"]["non_pretest_mapped_lims_location"] = state["external"]["local_lims_location"]
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    

@state_transition
def date_string_check_revert(state):
    ...


@state_transition
def lims_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "lims_abort_experiment" in state["external"] and state["external"]["lims_abort_experiment"]:
        state["external"]["next_state"] = "create_manifest_and_platform_json"
    elif "lims_abort_cancel" in state["external"] and state["external"]["lims_abort_cancel"]:
        state["external"]["next_state"] = "pull_ISI_data"
    else:
        fail_state('No valid inputs', state)


@state_transition
def lims_abort_confirm_revert(state):
    ...


@state_transition
def probe_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "probe_abort_experiment" in state["external"] and state["external"]["probe_abort_experiment"]:
        state["external"]["next_state"] = "create_manifest_and_platform_json"
    elif "probe_abort_cancel" in state["external"] and state["external"]["probe_abort_cancel"]:
        state["external"]["next_state"] = "scan_mouse_id"
    else:
        fail_state('No valid inputs', state)


@state_transition
def probe_abort_confirm_revert(state):
    ...


@state_transition
def pull_ISI_data_input(state):
    pass


@state_transition
def ecephys_id_check_input(state):
    """
    Input test function for state ecephys_id_check
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def check_experiment_day_enter(state):
    url = f'http://mtrain:5000'
    webbrowser.open(url)
    state["external"]["clear_sticky"] = True
    npxc.start_ecephys_acquisition(state)


@state_transition
def check_experiment_day_input(state):
    entered_experiment_day = state['external']['entered_experiment_day']
    session_types_options = state['external']['session_types_options']
    if not (entered_experiment_day in session_types_options):
        message = 'Session Type Not Valid: please type exactly as listed'
        fail_state(message, state)
    else:
        state['external']['full_session_type'] = state['external'][
                                                             'session_type'] + '_' + entered_experiment_day.lower()

        if 'day1' in state['external']['full_session_type'] :
            state['external']['dii_description'] = npxc.get_from_config(['dii_description_day1'], default='CM-DiI 100%')
            message = 'Is the ISO off?'
            npxc.alert_text(message, state)
        if 'day2' in state['external']['full_session_type'] :
            state['external']['dii_description'] = npxc.get_from_config(['dii_description_day2'], default='CM-DiI 100%')


@state_transition
def configure_hardware_camstim_enter(state):
    """
    Entry function for state configure_hardware_camstim
    """
    pass


@state_transition
def configure_hardware_camstim_input(state):
    """
    Input test function for state workflow_complete
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def configure_hardware_videomon_enter(state):
    """
    Entry function for state configure_hardware_camstim
    """
    try:
        state['external']['session_name'] = state['external']['non_pretest_session_name']
        state["external"]["local_lims_location"] = state["external"]["non_pretest_mapped_lims_location"]
        state["external"]["mapped_lims_location"] = state["external"][
            "non_pretest_mapped_lims_location"]
    except KeyError as E:
        npxc.alert_text('Failed to reset session name after pretest', state)
    pass


@state_transition
def configure_hardware_openephys_enter(state):
    """
    Input test function for state configure_hardware_openephys

    """
    print('>>>configure hardware openephys<<<')
    # npxc.set_open_ephys_name(state)
    # configure sync here so that its ready for pretest, but doesn't need to be done multiple times
    npxc.start_ecephys_acquisition(state)
    try:
        sync_proxy = state["component_proxies"]["Sync"]
        sync_proxy.init()
        # sync_proxy.load_config("C:/ProgramData/AIBS_MPE/sync/last.yml")  # TODO: We should put this in zookeeper
        state["external"]["status_message"] = "success"
        state["external"]["component_status"]["Sync"] = True
    except KeyError:
        fail_state('Sync proxy undefined', state)
        state["external"]["component_status"]["Sync"] = False
    except Exception as e:
        fail_state(f"Sync load config failure:{e}", state)
        state["external"]["component_status"]["Sync"] = False


@state_transition
def configure_hardware_openephys_exit(state):
    """
    Exit function for state configure_hardware_openephys
    """
    # set the data file path for open ephys
    # npxc.set_open_ephys_name(state)
    pass


@state_transition
def configure_hardware_openephys_input(state):
    """
    Input test function for state configure_hardware_openephys
    """
    npxc.handle_2_choice_button('pretest_run', 'start_pretest', 'configure_hardware_videomon', state)
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def configure_hardware_rig_input(state):
    """
    Input function for configure_hardware_rig
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def calibrate_probe_offset_input(state):
    """
    Input function for calibrate_probe_offset
    """
    # state['external']['next_state'] = 'align_probes_start'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def align_probes_start_input(state):
    """
    Input test function for state align_probes_start
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def align_probes_start_revert(state):
    ...


@state_transition
def align_probes_complete_input(state):
    """
    Input test function for state align_probes_complete
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    probes_not_aligned = state['external'].get('probes_not_aligned', False)
    if probes_not_aligned:
        state["external"]["next_state"] = "probes_not_aligned"
    else:
        state["external"]["next_state"] = "diI_application"


@state_transition
def probes_not_aligned_input(state):
    """
    Input test function for state probes_not_aligned
    """
    # state['external']['next_state'] = 'probe_abort_confirm'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def check_stimulus_enter(state):
    url = f'http://mtrain:5000/set_state/{state["external"]["mouse_id"]}'
    webbrowser.open(url)
    session_day = state['external']['entered_experiment_day']
    key_list = ['MTrainData', 'Experiment_Sessions', session_day, 'Full_String']
    state['external']['mtrain_string'] = npxc.get_from_config(key_list, default='')

    state["external"]["clear_sticky"] = True




@state_transition
def diI_application_enter(state):
    pass


@state_transition
def diI_application_input(state):
    """
    Input test function for state diI_application
    """
    state["external"]["DiINotes"] = {"StartTime": dt.now().strftime("%Y%m%d%H%M%S")}
    state["external"][
        "local_log"
    ] = f'diI Application Start Time:{state["external"]["DiINotes"]["StartTime"]}'
    npxc.save_platform_json(state, manifest=False)


@state_transition
def diI_probe_depth_confirmation_enter(state):
    """
    Entry function for state diI_probe_depth_confirmation
    """


@state_transition
def diI_probe_depth_confirmation_input(state):
    """
    Input test function for state diI_probe_depth_confirmation
    """
    ...


@state_transition
def diI_photoDoc_setup_input(state):
    """
    Input test function for state diI_photoDoc_setup
    """
    if state["external"]["dummy_mode"]:
        pre_experiment_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image1-left.png"),
        )
        pre_experiment_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image1-right.png"),
        )
        pre_experiment_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image1-left.png"),
        )
    else:
        pre_experiment_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image1-left.png'
        pre_experiment_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image1-right.png'
        pre_experiment_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image1-left.png'
    pre_experiment_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image1-left.png"),
    )
    pre_experiment_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image1-right.png"),
    )
    try:
        proxy = state["component_proxies"]["Cam3d"]

        print(">>>>>>> pre-experiment_image")
        print(f"pre_experiment_left_path:{pre_experiment_left_path}")
        print(f"pre_experiment_right_path:{pre_experiment_right_path}")
        print(f"pre_experiment_local_path:{pre_experiment_local_path}")
        print("<<<<<<<")

        state["external"][
            "surface_1_left_name"
        ] = f'{state["external"]["session_name"]}_surface-image1-left.png'
        state["external"][
            "surface_1_right_name"
        ] = f'{state["external"]["session_name"]}_surface-image1-right.png'

        try:
            proxy.save_left_image(pre_experiment_left_path)
            proxy.save_right_image(pre_experiment_right_path)

            state["external"]["status_message"] = "success"
            state["external"]["local_log"] = f"Surface_1_Path:{pre_experiment_local_path}"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(pre_experiment_left_local_path)
    right_image_result = os.path.isfile(pre_experiment_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (
        left_image_result and right_image_result):  # if one of the images not take successfully, force the warning box
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state["external"]["local_log"] = f"pre_experiment_path:{pre_experiment_local_path}"
    state["external"][
        "surface_1_file_location"
    ] = pre_experiment_local_path  # this is what gets displayed in the GUI
    if not(os.path.exists(pre_experiment_local_path)):
        time.sleep(5)
        if not(os.path.exists(pre_experiment_local_path)):
            message = 'You may need to click the blue botton and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_1_left_local_file_location"] = pre_experiment_left_local_path
    state["external"]["surface_1_right_local_file_location"] = pre_experiment_right_local_path

    # state['external']['next_state'] = 'diI_photoDocumentation'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"




@state_transition
def diI_photoDocumentation_input(state):
    """
    Input test function for state diI_photoDocumentation
    """
    if "retake_image_1" in state["external"] and state["external"]["retake_image_1"]:
        print("go back to diI_photoDoc_setup")
        state["external"]["next_state"] = "diI_photoDoc_setup"
        state["external"].pop("retake_pre_experiment_image", None)
    else:
        state["external"]["next_state"] = "diI_info_and_remove"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def diI_info_and_remove_enter(state):
    """
    Entry function for state diI_info_and_remove
    """


@state_transition
def diI_info_and_remove_input(state):
    """
    Exit function for state diI_info_and_remove
    """
    # create a diI application timestamp (YYYYMMDDHHMMSS)

    state["external"]["DiINotes"]["EndTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state["external"][
        "local_log"
    ] = f'diI Application End Time:{state["external"]["DiINotes"]["EndTime"]}'
    print(f'diI Application End Time:{state["external"]["DiINotes"]["EndTime"]}')

    # state['external']['DiINotes']['DiI_Concentration'] = state['external']['dye_concentration']
    state["external"]["DiINotes"]["times_dipped"] = state["external"]["fresh"]
    state["external"]["DiINotes"]["dii_description"] = state["external"]["dii_description"]

    # state['external']['DiINotes']['TimesDipped'] = state['external']['times_dipped']

    # state['external']['next_state'] = 'load_mouse'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)

@state_transition
def load_mouse_enter(state):
    npxc.load_mouse_enter(state)

@state_transition
def load_mouse_input(state):
    """
    Input test function for state load_mouse_headframe
    """
    # create a mouse in headframe timestamp (YYYYMMDDHHMMSS)
    # state['external']['mouse_in_headframe_holder']
    state["external"]["HeadFrameEntryTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state["external"]["local_log"] = f'HeadFrameEntryTime:{state["external"]["HeadFrameEntryTime"]}'
    # state['external']['next_state'] = 'lower_probe_cartridge'
    message = npxc.load_mouse_behavior(state)
    if message:
        fail_state(message, state)
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def load_mouse_revert(state):
    print("doing load mouse revert stuff")


@state_transition
def lower_probe_cartridge_enter(state):
    state["external"]["clear_sticky"] = True


@state_transition
def ground_connected_check_input(state):
    """
    Input test function for state ground_connected_check
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "ground_not_connected" in state["external"] and state["external"]["ground_not_connected"]:
        state["external"]["next_state"] = "ground_abort_confirm"
    elif "ground_connected" in state["external"] and state["external"]["ground_connected"]:
        state["external"]["next_state"] = "eyetracking_dichroic"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False


@state_transition
def ground_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    print(">> ground abort_confirm_input <<")

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "ground_abort_experiment" in state["external"] and state["external"]["ground_abort_experiment"]:
        print("&& ground abort_experiment")
        state["external"]["next_state"] = "ground_abort_shutdown"
    elif "ground_abort_cancel" in state["external"] and state["external"]["ground_abort_cancel"]:
        print("&& ground abort_cancel")
        state["external"]["next_state"] = "eyetracking_dichroic"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False


@state_transition
def ground_abort_confirm_revert(state):
    print("doing ground_abort_confirm_revert stuff")


@state_transition
def ground_abort_shutdown_input(state):
    """
    Input test function for state initialize
    """
    print(">> ground_abort_shutdown_input <<")

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    state["external"]["next_state"] = "remove_mouse_and_move_files2"


@state_transition
def ground_abort_shutdown_revert(state):
    print("ground_abort_shutdown_confirm_revert stuff")


@state_transition
def eyetracking_dichroic_input(state):
    """
    Input test function for state eyetracking_dichroic
    """
    # state['external']['next_state'] = 'eye_visible_check'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def eye_visible_check_input(state):
    """
    Input test function for state eye_visible_check
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "eye_not_visible" in state["external"] and state["external"]["eye_not_visible"]:
        state["external"]["next_state"] = "eye_visible_abort_confirm"
    elif "eye_visible" in state["external"] and state["external"]["eye_visible"]:
        state["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False


@state_transition
def eye_visible_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    print(">> eye_visible_abort_confirm_input <<")

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if (
        "eye_visible_abort_experiment" in state["external"]
        and state["external"]["eye_visible_abort_experiment"]
    ):
        state["external"]["next_state"] = "remove_mouse_and_move_files2"
    elif (
        "eye_visible_abort_cancel" in state["external"]
        and state["external"]["eye_visible_abort_cancel"]
    ):
        state["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False


@state_transition
def eye_visible_abort_confirm_revert(state):
    print("doing ground_abort_confirm_revert stuff")


@state_transition
def lower_probe_cartridge_input(state):
    """
    Input test function for state lower_probe_cartridge
    """
    # create a probe_cartridge_lower timestamp (YYYYMMDDHHMMSS)
    if state["external"]["probe_cartridge_lower"]:
        state["external"]["CartridgeLowerTime"] = dt.now().strftime("%Y%m%d%H%M%S")
        state["external"][
            "local_log"
        ] = f'CartridgeLowerTime: {state["external"]["CartridgeLowerTime"]}'
    # state['external']['next_state'] = 'brain_surface_focus'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)
    try:
        proxy = state["component_proxies"]["Notes"]
        try:
            npxc.rename_mlog(state)
        except Exception as e:
            print(f"Notes rename log failure:{e}!")
    except Exception as e:
        print(f"Notes proxy failure:{e}!")


@state_transition
def brain_surface_focus_enter(state):
    state["external"]["clear_sticky"] = True


@state_transition
def brain_surface_focus_input(state):
    """
    Input test function for state brain_surface_focus
    """
    if state["external"]["dummy_mode"]:
        surface_2_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image2-left.png"),
        )
        surface_2_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image2-right.png"),
        )
        surface_2_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image2-left.png"),
        )
    else:
        surface_2_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image2-left.png'
        surface_2_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image2-right.png'
        surface_2_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image2-left.png'
    surface_2_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image2-left.png"),
    )
    surface_2_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image2-right.png"),
    )
    try:
        proxy = state["component_proxies"]["Cam3d"]

        print(">>>>>>> brain_surface_image")
        print(f"surface_2_left_path:{surface_2_left_path}")
        print(f"surface_2_right_path:{surface_2_right_path}")
        print(f"surface_2_local_path:{surface_2_local_path}")
        print("<<<<<<<")

        state["external"][
            "surface_2_left_name"
        ] = f'{state["external"]["session_name"]}_surface-image2-left.png'
        state["external"][
            "surface_2_right_name"
        ] = f'{state["external"]["session_name"]}_surface-image2-right.png'

        try:
            proxy.save_left_image(surface_2_left_path)
            proxy.save_right_image(surface_2_right_path)

            state["external"]["status_message"] = "success"
            state["external"]["local_log"] = f"Surface_2_Path:{surface_2_local_path}"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_2_left_local_path)
    right_image_result = os.path.isfile(surface_2_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (
        left_image_result and right_image_result):  # if one of the images not take successfully, force the warning box
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state["external"]["local_log"] = f"surface_2_path:{surface_2_local_path}"
    state["external"][
        "surface_2_file_location"
    ] = surface_2_local_path  # this is what gets displayed in the GUI
    if not(os.path.exists(surface_2_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_2_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_2_left_local_file_location"] = surface_2_left_local_path
    state["external"]["surface_2_right_local_file_location"] = surface_2_right_local_path

    # state['external']['next_state'] = 'insert_probes_start'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def insert_probes_start_enter(state):
    """
    Entry function for state insert_probes_start_enter
    """
    print("&& input probes start enter &&")
    npxc.set_open_ephys_name(state)
    # start the open ephys acquisitino
    npxc.start_ecephys_acquisition(state)


@state_transition
def insert_probes_start_input(state):
    """
    Input test function for state insert_probes_start
    """
    # create a insert_probes timestamp (YYYYMMDDHHMMSS)
    print("&& insert probes input &&")

    if "retake_image_2" in state["external"] and state["external"]["retake_image_2"]:
        print("go back to brain surface focus")
        state["external"]["next_state"] = "brain_surface_focus"
        state["external"].pop("retake_image_2", None)
    else:
        state["external"]["next_state"] = "confirm_ISI_match"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def probe_brain_surface_enter(state):
    """
    Entry function for state probe_brain_surface
    """
    state["external"]["clear_sticky"] = True


@state_transition
def probe_brain_surface_input(state):
    """
    Input test function for state probe_brain_surface
    """

    # check to see if the none is checked

    probes_at_surface = 0
    probes_at_surface_list = []

    if "probe_A_surface" in state["external"] and state["external"]["probe_A_surface"]:
        state["external"]["probe_A_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("A")
    else:
        state["external"]["probe_A_surface_failure"] = True

    if "probe_B_surface" in state["external"] and state["external"]["probe_B_surface"]:
        state["external"]["probe_B_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("B")
    else:
        state["external"]["probe_B_surface_failure"] = True

    if "probe_C_surface" in state["external"] and state["external"]["probe_C_surface"]:
        state["external"]["probe_C_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("C")
    else:
        state["external"]["probe_C_surface_failure"] = True

    if "probe_D_surface" in state["external"] and state["external"]["probe_D_surface"]:
        state["external"]["probe_D_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("D")
    else:
        state["external"]["probe_D_surface_failure"] = True

    if "probe_E_surface" in state["external"] and state["external"]["probe_E_surface"]:
        state["external"]["probe_E_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("E")
    else:
        state["external"]["probe_E_surface_failure"] = True

    if "probe_F_surface" in state["external"] and state["external"]["probe_F_surface"]:
        state["external"]["probe_F_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("F")
    else:
        state["external"]["probe_F_surface_failure"] = True

    if probes_at_surface >= 3:
        print(">>> More than 3 probes at surface")
        state["external"]["local_log"] = "More than 3 probes at surface"
        state["external"]["next_state"] = "photodoc_setup3"
    else:
        print(">>> Less than 3 probes at surface")
        state["external"]["local_log"] = "Less than 3 probes at surface"
        state["external"]["next_state"] = "brain_surface_abort_confirm"

    state["external"]["probe_a_agar_insertions"] = state["external"][
        "probe_b_agar_insertions"
    ] = state["external"]["probe_c_agar_insertions"] = state["external"][
        "probe_d_agar_insertions"
    ] = state[
        "external"
    ][
        "probe_e_agar_insertions"
    ] = state[
        "external"
    ][
        "probe_f_agar_insertions"
    ] = 1

    # Set the default values here so they don't reset if you need to revert
    for probe in state["external"]["probe_list"]:
        lower_probe = probe.lower()
        state["external"][f"probe_{lower_probe}_location_changed"] = None
        state["external"][f"probe_{lower_probe}_bending_severity"] = 0
        state["external"][f"probe_{lower_probe}_agar_insertions"] = 1
        state["external"][f"probe_{lower_probe}_insert_failure"] = 0
        state["external"][f"probe_{lower_probe}_bending_elsewhere_severity"] = 0

    state["external"]["probes_aligned_string"] = ", ".join(probes_at_surface_list)
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def brain_surface_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    print(">> brain_surface_abort_confirm_input <<")

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if (
        "brain_surface_abort_experiment" in state["external"]
        and state["external"]["brain_surface_abort_experiment"]
    ):
        state["external"]["next_state"] = "end_experiment_photodocumentation"
    elif (
        "brain_surface_abort_cancel" in state["external"]
        and state["external"]["brain_surface_abort_cancel"]
    ):
        state["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False

@state_transition
def photodoc_setup3_enter(state):
    message = f"- Adjust the zoom to {state['external']['high_zoom_level']}"
    npxc.alert_text(message, state)

@state_transition
def photodoc_setup3_input(state):
    """

    """
    if state["external"]["dummy_mode"]:
        surface_3_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image3-left.png"),
        )
        surface_3_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image3-right.png"),
        )
        surface_3_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image3-left.png"),
        )
    else:
        surface_3_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image3-left.png'
        surface_3_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image3-right.png'
        surface_3_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image3-left.png'
    surface_3_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image3-left.png"),
    )
    surface_3_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image3-right.png"),
    )
    try:
        proxy = state["component_proxies"]["Cam3d"]

        print(">>>>>>> pre_insertion_surface")
        print(f"surface_3_left_path:{surface_3_left_path}")
        print(f"surface_3_right_path:{surface_3_right_path}")
        print(f"surface_3_local_path:{surface_3_local_path}")
        print("<<<<<<<")

        state["external"][
            "surface_3_left_name"
        ] = f'{state["external"]["session_name"]}_surface-image3-left.png'
        state["external"][
            "surface_3_right_name"
        ] = f'{state["external"]["session_name"]}_surface-image3-right.png'

        try:
            print(f"saving left image to {surface_3_left_path}")
            proxy.save_left_image(surface_3_left_path)
            print(f"saving left image to {surface_3_right_path}")
            proxy.save_right_image(surface_3_right_path)

            state["external"]["status_message"] = "success"
            state["external"]["local_log"] = f"Surface_3_Path:{surface_3_local_path}"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_3_left_local_path)
    right_image_result = os.path.isfile(surface_3_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (left_image_result and right_image_result):
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3dViewer, and restart if necessary.  " "Retake Image",
        }

    state["external"]["local_log"] = f"surface_3_path:{surface_3_local_path}"
    state["external"]["surface_3_file_location"] = surface_3_local_path  # this is what gets displayed in GUI
    if not(os.path.exists(surface_3_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_3_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_3_left_local_file_location"] = surface_3_left_local_path
    state["external"]["surface_3_right_local_file_location"] = surface_3_right_local_path

    # state['external']['next_state'] = 'photodoc_confirm3'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def photodoc_confirm3_input(state):
    """
    Input test function for state lower_probes_automatically
    """
    # create a insert_probes timestamp (YYYYMMDDHHMMSS)

    if "retake_image_3" in state["external"] and state["external"]["retake_image_3"]:
        state["external"]["next_state"] = "photodoc_setup3"
        state["external"].pop("retake_image_3", None)
    else:
        state["external"]["next_state"] = "lower_probes_automatically"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)

@state_transition
def lower_probes_automatically_enter(state):
    message = f"- Adjust the zoom to {state['external']['low_zoom_level']}"
    npxc.alert_text(message, state)

@state_transition
def lower_probes_automatically_input(state):
    """
    Input test function for state eyetracking_dichroic
    """
    probe_insertion_start_time = dt.now().strftime("%Y%m%d%H%M%S")
    state["external"]["ProbeInsertionStartTime"] = probe_insertion_start_time
    state["external"][
        "local_log"
    ] = f'ProbeInsertionStartTime:{state["external"]["ProbeInsertionStartTime"]}'

    InsertionNotes = {}
    if not ("InsertionNotes" in state["external"]):
        state["external"]["InsertionNotes"] = {}

    state["external"]["next_state"] = "probe_a_notes"
    npxc.handle_2_choice_button('skip_insertion_notes', 'probes_final_depth', 'probe_a_notes', state)
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def probe_a_notes_input(state):
    """
    Input test function for state probe_a_notes
    """

    probe_a_notes = {}

    if "probe_A_surface_timestamp" in state["external"]:
        probe_a_notes["InsertionTimes"] = state["external"]["probe_A_surface_timestamp"]

    probe_a_notes["ProbeLocationChanged"] = state["external"]["probe_a_location_changed"]
    probe_a_notes["ProbeBendingOnSurface"] = state["external"]["probe_a_bending_severity"]
    probe_a_notes["NumAgarInsertions"] = state["external"]["probe_a_agar_insertions"]
    probe_a_notes["FailedToInsert"] = state["external"]["probe_a_insert_failure"]
    probe_a_notes["ProbeBendingElsewhere"] = state["external"]["probe_a_bending_elsewhere_severity"]

    state["external"]["InsertionNotes"]["ProbeA"] = probe_a_notes

    state["external"]["next_state"] = "probe_b_notes"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def probe_b_notes_input(state):
    """
    Input test function for state probe_b_notes
    """
    probe_b_notes = {}

    if "probe_B_surface_timestamp" in state["external"]:
        probe_b_notes["InsertionTimes"] = state["external"]["probe_B_surface_timestamp"]

    probe_b_notes["ProbeLocationChanged"] = state["external"]["probe_b_location_changed"]
    probe_b_notes["ProbeBendingOnSurface"] = state["external"]["probe_b_bending_severity"]
    probe_b_notes["NumAgarInsertions"] = state["external"]["probe_b_agar_insertions"]
    probe_b_notes["FailedToInsert"] = state["external"]["probe_b_insert_failure"]
    probe_b_notes["ProbeBendingElsewhere"] = state["external"]["probe_b_bending_elsewhere_severity"]

    state["external"]["InsertionNotes"]["ProbeB"] = probe_b_notes

    state["external"]["next_state"] = "probe_c_notes"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def probe_c_notes_input(state):
    """
    Input test function for state probe_c_notes
    """
    probe_c_notes = {}

    if "probe_C_surface_timestamp" in state["external"]:
        probe_c_notes["InsertionTimes"] = state["external"]["probe_C_surface_timestamp"]

    probe_c_notes["ProbeLocationChanged"] = state["external"]["probe_c_location_changed"]
    probe_c_notes["ProbeBendingOnSurface"] = state["external"]["probe_c_bending_severity"]
    probe_c_notes["NumAgarInsertions"] = state["external"]["probe_c_agar_insertions"]
    probe_c_notes["FailedToInsert"] = state["external"]["probe_c_insert_failure"]
    probe_c_notes["ProbeBendingElsewhere"] = state["external"]["probe_c_bending_elsewhere_severity"]

    state["external"]["InsertionNotes"]["ProbeC"] = probe_c_notes

    state["external"]["next_state"] = "probe_d_notes"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def probe_d_notes_input(state):
    """
    Input test function for state probe_d_notes
    """
    probe_d_notes = {}

    if "probe_D_surface_timestamp" in state["external"]:
        probe_d_notes["InsertionTimes"] = state["external"]["probe_D_surface_timestamp"]

    probe_d_notes["ProbeLocationChanged"] = state["external"]["probe_d_location_changed"]
    probe_d_notes["ProbeBendingOnSurface"] = state["external"]["probe_d_bending_severity"]
    probe_d_notes["NumAgarInsertions"] = state["external"]["probe_d_agar_insertions"]
    probe_d_notes["FailedToInsert"] = state["external"]["probe_d_insert_failure"]
    probe_d_notes["ProbeBendingElsewhere"] = state["external"]["probe_d_bending_elsewhere_severity"]

    state["external"]["InsertionNotes"]["ProbeD"] = probe_d_notes

    state["external"]["next_state"] = "probe_e_notes"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def probe_e_notes_input(state):
    """
    Input test function for state probe_e_notes
    """
    probe_e_notes = {}

    if "probe_E_surface_timestamp" in state["external"]:
        probe_e_notes["InsertionTimes"] = state["external"]["probe_E_surface_timestamp"]

    probe_e_notes["ProbeLocationChanged"] = state["external"]["probe_e_location_changed"]
    probe_e_notes["ProbeBendingOnSurface"] = state["external"]["probe_e_bending_severity"]
    probe_e_notes["NumAgarInsertions"] = state["external"]["probe_e_agar_insertions"]
    probe_e_notes["FailedToInsert"] = state["external"]["probe_e_insert_failure"]
    probe_e_notes["ProbeBendingElsewhere"] = state["external"]["probe_e_bending_elsewhere_severity"]

    state["external"]["InsertionNotes"]["ProbeE"] = probe_e_notes

    state["external"]["next_state"] = "probe_f_notes"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def probe_f_notes_input(state):
    """
    Input test function for state probe_f_notes
    """
    probe_f_notes = {}

    if "probe_F_surface_timestamp" in state["external"]:
        probe_f_notes["InsertionTimes"] = state["external"]["probe_F_surface_timestamp"]

    probe_f_notes["ProbeLocationChanged"] = state["external"]["probe_f_location_changed"]
    probe_f_notes["ProbeBendingOnSurface"] = state["external"]["probe_f_bending_severity"]
    probe_f_notes["NumAgarInsertions"] = state["external"]["probe_f_agar_insertions"]
    probe_f_notes["FailedToInsert"] = state["external"]["probe_f_insert_failure"]
    probe_f_notes["ProbeBendingElsewhere"] = state["external"]["probe_f_bending_elsewhere_severity"]

    state['external']['InsertionNotes']['ProbeF'] = probe_f_notes

    state["external"]["next_state"] = "probes_final_depth"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def probes_final_depth_enter(state):
    """
    Entry function for state insertion_photodocumentation
    """
    message = 'No probes had bumping limitations'
    alert = False
    probe_restrctions = {}
    probe_restrctions_string = '\n'
    probe_restrctions_template = 'Probe {}: {}\n'
    for probe in state['external']['probe_list']:
        key = 'probe_' + probe + '_DiI_depth'
        max_depth = state["external"][key]
        if not (max_depth == '6000'):
            alert = True
            probe_restrctions[probe] = max_depth
            probe_restrctions_string = probe_restrctions_string + probe_restrctions_template.format(probe, max_depth)

    if alert:
        message = 'The following probes have restrictions: ' + probe_restrctions_string
        npxc.alert_text(message, state)


    default = 'Retract the probes 30 um to reduce drift? (corbett, sev do we want to do this? its unverified still)'
    key_list = ['depth_retract_bool_string']
    depth_retract_string = npxc.get_from_config(key_list, default)

    default = False
    key_list = ['depth_retract_bool']
    depth_retract_bool = npxc.get_from_config(key_list, default)
    if depth_retract_bool:
        npxc.alert_text(depth_retract_string, state)

@state_transition
def probes_final_depth_exit(state):
    """
    Exit function for state insertion_photodocumentation
    """
    pass


@state_transition
def probes_final_depth_input(state):
    """
    Input test function for state probes_final_depth
    """
    # create a probes_final_depth timestamp (YYYYMMDDHHMMSS)
    print("in probes_final_depth")
    npxc.set_open_ephys_name(state)
    state["external"]["ProbeInsertionCompleteTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    print(f'ProbeInsertionCompleteTime:{state["external"]["ProbeInsertionCompleteTime"]}')
    state["external"][
        "local_log"
    ] = f'ProbeInsertionCompleteTime:{state["external"]["ProbeInsertionCompleteTime"]}'
    if "retake_image_3" in state["external"] and state["external"]["retake_image_3"]:
        print(">>> retake image!")
        # state['external']['next_state'] = 'check_data_dirs'
        state["external"].pop("retake_image_3", None)
    else:
        print(">>> probes_final_depth!")
        # state['external']['next_state'] = 'photodoc_setup4'

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    state["resources"]["final_depth_timer_start"] = datetime.datetime.now()
    npxc.save_platform_json(state, manifest=False)


@state_transition
def photodoc_setup4_input(state):
    """

    """
    if state["external"]["dummy_mode"]:
        surface_4_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image4-left.png"),
        )
        surface_4_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image4-right.png"),
        )
        surface_4_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image4-left.png"),
        )
    else:
        surface_4_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image4-left.png'
        surface_4_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image4-right.png'
        surface_4_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image4-left.png'

    surface_4_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image4-left.png"),
    )
    surface_4_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image4-right.png"),
    )
    print(">>>>>>> post_insertion")
    print(f"surface_4_left_path:{surface_4_left_path}")
    print(f"surface_4_right_path:{surface_4_right_path}")
    print(f"surface_4_local_path:{surface_4_local_path}")
    print("<<<<<<<")

    state["external"][
        "surface_4_left_name"
    ] = f'{state["external"]["session_name"]}_surface-image4-left.png'
    state["external"][
        "surface_4_right_name"
    ] = f'{state["external"]["session_name"]}_surface-image4-right.png'

    try:
        proxy = state["component_proxies"]["Cam3d"]
        try:
            proxy.save_left_image(surface_4_left_path)  # will be replaced by the real call to cam3d
            proxy.save_right_image(surface_4_right_path)  # will be replaced by the real call to cam3d
            state["external"]["status_message"] = "success"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_4_left_local_path)
    right_image_result = os.path.isfile(surface_4_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (
        left_image_result and right_image_result):  # if one of the images not take successfully, force the warning box
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state["external"]["local_log"] = f"surface_3_path:{surface_4_local_path}"
    state["external"]["surface_4_file_location"] = surface_4_local_path  # for displaying in the GUI
    if not(os.path.exists(surface_4_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_4_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_4_left_local_file_location"] = surface_4_left_local_path
    state["external"]["surface_4_right_local_file_location"] = surface_4_right_local_path

    # state['external']['next_state'] = 'insertion_photodocumentation'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def insertion_photodocumentation_input(state):
    """
    Input test function for state insertion_photodocumentation
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "bleeding_evident" in state["external"] and state["external"]["bleeding_evident"]:
        state["external"]["next_state"] = "pre_experiment_bleeding_severity"
    elif "no_bleeding_evident" in state["external"] and state["external"]["no_bleeding_evident"]:
        state["external"]["next_state"] = "check_data_dirs"
    elif "retake_image_4" in state["external"] and state["external"]["retake_image_4"]:
        state["external"]["next_state"] = "photodoc_setup4"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False
    npxc.save_platform_json(state, manifest=False)


@state_transition
def pre_experiment_bleeding_severity_enter(state):
    """
    Entry function for bleeding_severity
    """
    state["external"]["pre_probe_a_bleeding_severity"] = state["external"][
        "pre_probe_b_bleeding_severity"
    ] = state["external"]["pre_probe_c_bleeding_severity"] = state["external"][
        "pre_probe_d_bleeding_severity"
    ] = state[
        "external"
    ][
        "pre_probe_e_bleeding_severity"
    ] = state[
        "external"
    ][
        "pre_probe_f_bleeding_severity"
    ] = 0


@state_transition
def pre_experiment_bleeding_severity_input(state):
    """
    Input function for bleeding severity
    """

    if "pre_probe_a_bleeding" in state["external"] and state["external"]["pre_probe_a_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeA"
        ] = 5  # state['external']['pre_probe_a_bleeding_severity']

    if "pre_probe_b_bleeding" in state["external"] and state["external"]["pre_probe_b_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeB"
        ] = 5  # state['external']['pre_probe_b_bleeding_severity']

    if "pre_probe_c_bleeding" in state["external"] and state["external"]["pre_probe_c_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeC"
        ] = 5  # state['external']['pre_probe_c_bleeding_severity']

    if "pre_probe_d_bleeding" in state["external"] and state["external"]["pre_probe_d_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeD"
        ] = 5  # state['external']['pre_probe_d_bleeding_severity']

    if "pre_probe_e_bleeding" in state["external"] and state["external"]["pre_probe_e_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeE"
        ] = 5  # state['external']['pre_probe_e_bleeding_severity']

    if "pre_probe_f_bleeding" in state["external"] and state["external"]["pre_probe_f_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeF"
        ] = 5  # state['external']['pre_probe_f_bleeding_severity']

    # state['external']['next_state'] = 'bleeding_abort_confirm'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def bleeding_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    print(">> bleeding abort_confirm_input <<")

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if (
        "bleeding_abort_experiment" in state["external"]
        and state["external"]["bleeding_abort_experiment"]
    ):
        print("&& bleeding abort_experiment")
        state["external"]["next_state"] = "end_experiment_photodocumentation"
    elif "bleeding_abort_cancel" in state["external"] and state["external"]["bleeding_abort_cancel"]:
        print("&& bleeding abort_cancel")
        state["external"]["next_state"] = "check_data_dirs"
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False


@state_transition
def bleeding_abort_confirm_revert(state):
    print("doing bleeding_abort_confirm_revert stuff")


def get_exp_wait_time(state):
    defaults = 300
    key_list = ['final_depth_timer_s']
    wait_time = npxc.get_from_config(key_list, defaults)
    return wait_time

@state_transition
def pre_stimulus_wait_enter(state):
    wait_time = get_exp_wait_time(state)
    npxc.settle_timer_enter(state, wait_time)


@state_transition
def pre_stimulus_wait_input(state):
    """
    Input test function for state pre_stimulus_wait
    """
    # before transiting to the next state, get the listing for camstim files on the stim computer
    # recreate the proxy
    stim_files = []

    state['external']['next_state'] = 'prime_lickspout'

    wait_time = get_exp_wait_time(state)
    npxc.settle_timer_enter(state, wait_time)
    if state["external"].get("override_settle_timer", False):
        return  # TODO: make the checkbox optional

    if float(state['external']['settle_time_remaining_num'])>0:  # total_seconds < npxc.config['final_depth_timer_s']:
        message = 'The settle time has not elapsed! Please wait until the state timer matches the remaining time'
        fail_state(message, state)
        return

    # state['external']['next_state'] = 'check_data_dirs'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def move_lickspout_to_mouse_offset_enter(state):
    try:
        print('Attempting to send mouse ID to mouse director')
        mouse_director.set_mouse_id(state["external"]["mouse_id"])
    except Exception as E:
        alert_string = f'Failed to send mouse ID to mouse director'
        npxc.alert_text(alert_string, state)
    try:
        print('Attempting to send userID to mouse director')
        mouse_director.set_user_id(state["external"]["user_id"])
    except Exception as E:
        alert_string = f'Failed to send userID to mouse director'
        npxc.alert_text(alert_string, state)  # Todo put this back


@state_transition
def move_lickspout_to_mouse_offset_input(state):
    ...

    failed = npxc.confirm_components(state)
    if failed:
        # state["external"]["next_state"] = "components_error"
        alert_string = f'The following proxies are not available: {", ".join(failed)}'
        # npxc.alert_text(alert_string, state)#Todo put this back  - I think it was being wierd.
        npxc.overrideable_error_state(state, 'move_lickspout_to_mouse_offset', 'initiate_behavior_experiment',
                                      message=alert_string)


@state_transition
def move_lickspout_to_mouse_offset_exit(state):
    npxc.get_start_experiment_params(state)


@state_transition
def probe_quiescence_enter(state):
    rest_time = npxc.config['final_depth_timer_s']
    stop_time = datetime.datetime.now()
    total_seconds = (stop_time - state["resources"]["final_depth_timer"]).total_seconds()
    wfltk_msgs.state_busy(message=f"Waiting for 5 minute delay time.  Resuming in {300 - total_seconds}")
    if total_seconds < rest_time:
        time.sleep(total_seconds)
        io.write(wfltk_msgs.state_ready(message="ready"))


@state_transition
def probe_quiescence_input(state):
    ...


@state_transition
def check_data_dirs_enter(state):
    print(">> check_data_dirs_enter <<")
    npxc.set_open_ephys_name(state)


@state_transition
def check_data_dirs_input(state):
    #npxc.set_open_ephys_name(state)
    npxc.start_ecephys_recording(state)
    time.sleep(3)
    npxc.stop_ecephys_recording(state)
    time.sleep(5)
    try:
        failed = npxc.check_data_drives(state)
    except Exception:
        message = f'There must be a bug in the function that moves the settings file and chakcs the data dirs'
        logging.debug("Data drive failure", exc_info=True)
        npxc.alert_text(message, state)
    if failed:
        state["external"]["next_state"] = "data_dir_error"
        npxc.alert_from_error_dict(state, failed, primary_key=False)
    else:
        state["external"]["next_state"] = "pre_stimulus_wait"
    state["external"]["status_message"] = "success"
    state["external"]["transition_result"] = True


@state_transition
def data_dir_error_input(state):
    print(">> data_dir_error_input <<")

    if "check_dirs_retry" in state["external"] and state["external"]["check_dirs_retry"]:
        print("go back to check data dirs")
        state["external"]["next_state"] = "check_data_dirs"
        state["external"].pop("check_dirs_retry", None)
    else:
        state["external"]["next_state"] = "pre_stimulus_wait"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def select_stimulus_input(state):
    """
    Input test function for state select_stimulus
    """
    print(f'stim file selected:{state["external"]["stimulus_selected"]}')
    state["external"]["local_log"] = f'stim file selected:{state["external"]["stimulus_selected"]}'

    # state['external']['next_state'] = 'initiate_experiment'
    state["external"]["status_message"] = "success"
    state["external"]["transition_result"] = True
    npxc.set_open_ephys_name(state)
    npxc.save_platform_json(state, manifest=False)

    # failed = component_check(state)
    # if failed:
    #    fail_state(f'The following proxies are not available: {", ".join(failed)}', state)
    #    return


###################



@state_transition
def initiate_behavior_experiment_input(state):
    """
    Input test function for state initiate_experiment
    """

    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    npxc.start_common_experiment_monitoring(state)
    wait_time = npxc.get_from_config(['experiment_stream_check_wait_time'], default=90)
    failed = npxc.check_data_stream_size(state, wait_time=wait_time)
    if failed:
        streams = [key.split('_')[0] for key in list(failed.keys())]
        message = f'STREAMS TO CHECK: {", ".join(streams).upper()}'
        npxc.alert_text(message, state)
        message = npxc.streams_message
        npxc.alert_text(message, state)
        fail_message_1 = npxc.alert_from_error_dict(state, failed, primary_key=None)
        # fail_message_1 = f'The following data streams are not recording: {", ".join(failed)}'
        # npxc.alert_text(fail_message_1,state)
        state['external']['next_state'] = 'streams_error_state'
        # fail_state(f'The following data streams are not recording: {", ".join(failed)}', state)
        # return
    else:
        state['external']['next_state'] = 'experiment_running_timer'
        initiate_behavior_stimulus_input(state)
    # state['external']['clear_sticky'] = True
    # state['external']['next_state'] = 'experiment_running_timer'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def initiate_behavior_stimulus_input(state):
    """
    Input test function for state initiate_experiment
    """
    message = npxc.initiate_behavior_stimulus_input(state)
    npxc.delete_dummy_recording(state)
    if message:
        return
    else:
        do_second_stream_check = npxc.get_from_config(['do_second_stream_check'], default=False)
        if do_second_stream_check:
            initial_wait = npxc.get_from_config(['wait_time_before_checking_streams'], default=0)
            time.sleep(initial_wait)
            npxc.establish_data_stream_size(state)
            wait_time = npxc.get_from_config(['experiment_2_stream_check_wait_time'], default=70)
            failed = npxc.check_data_stream_size(state, wait_time=wait_time)
            if failed:
                message = npxc.streams_message

                npxc.alert_text(message, state)
                fail_message_1 = npxc.alert_from_error_dict(state, failed, primary_key=False)


@state_transition
def overrideable_error_state_input(state):
    npxc.overrideable_error_state_input(state)


@state_transition
def overrideable_error_state_exit(state):
    npxc.overrideable_error_state_exit(state)



@state_transition
def initiate_experiment_input(state):
    """
    Input test function for state initiate_experiment
    """
    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    npxc.start_common_experiment_monitoring(state)
    npxc.start_stim(state)
    npxc.save_platform_json(state, manifest=False)
    # state['external']['clear_sticky'] = True
    # state['external']['next_state'] = 'experiment_running_timer'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


def get_exp_message_1(state):
    message_1 = ''
    if 'day1' in state['external']['full_session_type'].lower():
        defaults = ("Please copy the surgery images from to the data directory\n"
            "- Then rename them to XXXXXXXXXX_XXXXXX_XXXXXXXX.surgeryImage1.jpg and "
            "XXXXXXXXXX_XXXXXX_XXXXXXXX.surgeryImage2.jpg")
        key_list = ['session_params', '2_days_before_experiment', 'message_1']
        message_1 = npxc.get_from_config(key_list, defaults)

    if message_1:
        npxc.alert_text(message_1, state)
    return message_1

@state_transition
def experiment_running_timer_enter(state):
    """
    Entry function for state experiment_running_timer
    """
    print("in experiment running enter")


@state_transition
def monitor_experiment_input(state):
    npxc.monitor_experiment_input(state)



@state_transition
def end_experiment_enter(state):
    pass


@state_transition
def end_experiment_input(state):
    """
    Input test function for state end_experiment
    """

    # get an experiment end time
    state["external"]["ExperimentCompleteTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state["external"]["ExperimentCompleteTime"]}'

    # end the stim process
    # opto_stim_file_path = os.path.join(
    #    state["external"]["local_lims_location"], state["external"]["session_name"] + ".opto.pkl"
    # )
    # visual_stim_file_path = os.path.join(
    #    state["external"]["local_lims_location"], state["external"]["session_name"] + ".stim.pkl"
    # )

    # state["external"]["opto_stim_file_name"] = f'{state["external"]["session_name"]}.opto.pkl'
    # state["external"]["visual_stim_file_name"] = f'{state["external"]["session_name"]}.stim.pkl'

    # state["external"]["opto_stim_file_location"] = opto_stim_file_path
    # state["external"]["visual_stim_file_location"] = visual_stim_file_path

    # print(
    #    f'mapped_lims_location:{state["external"]["mapped_lims_location"]}, opto file name:{state["external"]["opto_stim_file_name"]}'
    # )

    # recreate the proxy

    # stop the open ephys process
    # npxc.stop_ecephys_recording(state)

    # stop the VideoMon process

    time.sleep(3)
    npxc.stop_common_experiment_monitoring(state)
    # recreate the proxy

    # state['external']['next_state'] = 'remove_probes_start'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"

    state["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state["external"]["ExperimentCompleteTime"]}'
    state["external"]["transition_result"] = True
    npxc.save_platform_json(state, manifest=False)


@state_transition
def end_experiment_photodocumentation_input(state):
    """
    Input test function for state end_experiment_photodocumentation
    """
    surface_5_left_path = 'undefined'
    if state["external"]["dummy_mode"]:
        surface_6_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image5-left.png"),
        )
        surface_5_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image5-right.png"),
        )
        surface_5_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image5-left.png"),
        )
    else:
        surface_5_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image5-left.png'
        surface_5_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image5-right.png'
        surface_5_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image5-left.png'

    surface_5_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image5-left.png"),
    )
    surface_5_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image5-right.png"),
    )
    print(">>>>>>> post_experiment")
    print(f"surface_5_left_path:{surface_5_left_path}")
    print(f"surface_5_right_path:{surface_5_right_path}")
    print(f"surface_5_local_path:{surface_5_local_path}")
    print("<<<<<<<")

    state["external"][
        "surface_5_left_name"
    ] = f'{state["external"]["session_name"]}_surface-image5-left.png'
    state["external"][
        "surface_5_right_name"
    ] = f'{state["external"]["session_name"]}_surface-image5-right.png'

    try:
        proxy = state["component_proxies"]["Cam3d"]
        try:
            print(f"taking surface 5 left:{surface_5_left_path}")
            result = proxy.save_left_image(surface_5_left_path)
            print(f"taking surface 5 right:{surface_5_right_path}")
            result = proxy.save_right_image(surface_5_right_path)
            state["external"]["status_message"] = "success"
        except Exception as e:
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_5_left_local_path)
    right_image_result = os.path.isfile(surface_5_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (
        left_image_result and right_image_result):  # if one of the images not take successfully, force the warning box
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state["external"]["local_log"] = f"surface_5_path:{surface_5_local_path}"
    state["external"]["surface_5_file_location"] = surface_5_local_path
    if not(os.path.exists(surface_5_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_5_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_5_left_local_file_location"] = surface_5_left_local_path
    state["external"]["surface_5_right_local_file_location"] = surface_5_right_local_path

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    npxc.save_platform_json(state, manifest=False)


@state_transition
def remove_probes_start_input(state):
    """
    Input test function for state remove_probes_start
    """
    print(">>>remove_probes_start_input<<<")
    if "retake_image_5" in state["external"] and state["external"]["retake_image_5"]:
        state["external"]["next_state"] = "end_experiment_photodocumentation"
        state["external"].pop("retake_image_5", None)
    else:
        state["external"]["next_state"] = "remove_probes_end"

    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def remove_probes_end_input(state):
    """
    Input test function for state remove_probes_end
    """
    # state['external']['next_state'] = 'post_removal_photodocumentation'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def post_removal_photodocumentation_input(state):
    """
    Input test function for state workflow_complete
    """
    if state["external"]["dummy_mode"]:
        surface_6_left_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image6-left.png"),
        )
        surface_6_right_path = os.path.join(
            state["external"]["mapped_lims_location"],
            (state["external"]["session_name"] + "_surface-image6-right.png"),
        )
        surface_6_local_path = os.path.join(
            state["external"]["local_lims_location"],
            (state["external"]["session_name"] + "_surface-image6-left.png"),
        )
    else:
        surface_6_left_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image6-left.png'
        surface_6_right_path = f'{state["external"]["mapped_lims_location"]}/{state["external"]["session_name"]}_surface-image6-right.png'
        surface_6_local_path = f'{state["external"]["local_lims_location"]}/{state["external"]["session_name"]}_surface-image6-left.png'

    surface_6_left_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image6-left.png"),
    )
    surface_6_right_local_path = os.path.join(
        state["external"]["local_lims_location"],
        (state["external"]["session_name"] + "_surface-image6-right.png"),
    )
    print(">>>>>>> post_removal")
    print(f"surface_6_left_path:{surface_6_left_path}")
    print(f"surface_6_right_path:{surface_6_right_path}")
    print(f"surface_6_local_path:{surface_6_local_path}")
    print("<<<<<<<")

    state["external"][
        "surface_6_left_name"
    ] = f'{state["external"]["session_name"]}_surface-image6-left.png'
    state["external"][
        "surface_6_right_name"
    ] = f'{state["external"]["session_name"]}_surface-image6-right.png'

    try:
        proxy = state["component_proxies"]["Cam3d"]
        try:
            result = proxy.save_left_image(surface_6_left_path)
            result = proxy.save_right_image(surface_6_right_path)
            state["external"]["status_message"] = "success"
        except Exception as e:
            state["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        state["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_6_left_local_path)
    right_image_result = os.path.isfile(surface_6_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (
        left_image_result and right_image_result):  # if one of the images not take successfully, force the warning box
        state["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state["external"]["local_log"] = f"surface_6_path:{surface_6_local_path}"
    state["external"]["surface_6_file_location"] = surface_6_local_path
    if not(os.path.exists(surface_6_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_6_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state)
    state["external"]["surface_6_left_local_file_location"] = surface_6_left_local_path
    state["external"]["surface_6_right_local_file_location"] = surface_6_right_local_path
    # state['external']['next_state'] = 'post_removal_image'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def post_removal_image_input(state):
    """
    Input test function for state workflow_complete
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"

    if "post_bleeding_evident" in state["external"] and state["external"]["post_bleeding_evident"]:
        state["external"]["next_state"] = "post_experiment_bleeding_severity"
    elif (
        "no_post_bleeding_evident" in state["external"]
        and state["external"]["no_post_bleeding_evident"]
    ):
        state["external"]["next_state"] = "remove_mouse_and_move_files2"
    elif "retake_image_6" in state["external"] and state["external"]["retake_image_6"]:
        state["external"]["next_state"] = "post_removal_photodocumentation"
        state["external"].pop("retake_image_6", None)
    else:
        state["external"]["status_message"] = f"No valid inputs"
        state["external"]["transition_result"] = False
    npxc.save_platform_json(state, manifest=False)


@state_transition
def post_removal_image_exit(state):
    npxc.reset_open_ephys(state)


@state_transition
def post_experiment_bleeding_severity_exit(state):
    npxc.reset_open_ephys(state)


@state_transition
def post_experiment_bleeding_severity_enter(state):
    """
    Entry function for bleeding_severity
    """
    state["external"]["post_probe_a_bleeding_severity"] = state["external"][
        "post_probe_b_bleeding_severity"
    ] = state["external"]["post_probe_c_bleeding_severity"] = state["external"][
        "post_probe_d_bleeding_severity"
    ] = state[
        "external"
    ][
        "post_probe_e_bleeding_severity"
    ] = state[
        "external"
    ][
        "post_probe_f_bleeding_severity"
    ] = 0


@state_transition
def post_experiment_bleeding_severity_input(state):
    """
    Input function for bleeding severity
    """
    if "post_probe_a_bleeding" in state["external"] and state["external"]["post_probe_a_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeA"
        ] = 5  # state['external']['post_probe_a_bleeding_severity']

    if "post_probe_b_bleeding" in state["external"] and state["external"]["post_probe_b_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeB"
        ] = 5  # state['external']['post_probe_b_bleeding_severity']

    if "post_probe_c_bleeding" in state["external"] and state["external"]["post_probe_c_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeC"
        ] = 5  # state['external']['post_probe_c_bleeding_severity']

    if "post_probe_d_bleeding" in state["external"] and state["external"]["post_probe_d_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeD"
        ] = 5  # state['external']['post_probe_d_bleeding_severity']

    if "post_probe_e_bleeding" in state["external"] and state["external"]["post_probe_e_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeE"
        ] = 5  # state['external']['post_probe_e_bleeding_severity']

    if "post_probe_f_bleeding" in state["external"] and state["external"]["post_probe_f_bleeding"]:
        state["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeF"
        ] = 5  # state['external']['post_probe_f_bleeding_severity']

    # state['external']['next_state'] = 'remove_mouse_and_move_files2'
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def remove_mouse_and_move_files2_input(state):
    message = npxc.remove_mouse_input(state)
    if message:
        fail_state(message, state)
    npxc.save_platform_json(state, manifest=False)
    if 'day2' in state['external']['full_session_type'].lower():
        message = 'Please mark the cap so LAS knows this mouse is safe to perfuse'
        message = npxc.get_from_config(['cap_message'], default=message)
        npxc.alert_text(message, state)

@state_transition
def water_mouse_enter(state):
    print('water_mouse_enter')
    npxc.water_mouse_enter(state)

@state_transition
def water_mouse_input(state):
    print('water_mouse_input')
    npxc.water_mouse_input(state)

@state_transition
def water_mouse_exit(state):
    print('water_mouse_exit')
    npxc.water_mouse_exit(state)


@state_transition
def check_files1_input(state):
    print('>> check_files1_input <<')
    session_type = state['external']['full_session_type']
    pkl_keyword = 'behavior-'
    behavior_pkl_path = npxc.get_pkl_path(pkl_keyword, state)
    npxc.overwrite_foraging_id(behavior_pkl_path, session_type, state)
    checkpoint = 1
    npxc.videomon_copy_wrapup(state)
    missing_files = {}
    try:
        missing_files = npxc.check_files_input(state, session_type, checkpoint)
        print(f'Missing files is {missing_files}')
    except Exception as E:
        message = (f'Error checking files: see the prompt for more details')
        traceback.print_tb(E.__traceback__)
        npxc.alert_text(message, state)

    try:
        data_missing = {}
        if not (state['external']['PXI']):
            for probe in state['external']['probe_list']:
                if state['external'][f'probe_{probe}_surface']:
                    probeDir = f'{state["openephys_drives"][probe]}/{state["external"]["session_name"]}_probe{probe}'
                    if not (os.path.isdir(probeDir)):
                        message = 'Cannot find data directory for ' + probe
                        data_missing[probe] = message
                        print(message)
        else:
            try:
                npxc.reset_open_ephys(state)
            except Exception as E:
                message = f'Failed to reset open ephys'
                key = f'open_ephys_reset'
                data_missing[key] = message
            print('attempting to rename probdirs')
            for slot, drive in state['external']['PXI'].items():
                print(f'for slot {slot}')
                a_probe = state['external']['reverse_mapping'][slot][
                    0]  # list(state['external']['probe_list'].keys())[0]
                probeDir, computer = npxc.get_probeDir(state, slot, drive)
                # print('computer:'+ computer + ', tail:'+x)
                new_dir, computer = npxc.get_final_probeDir(state, a_probe)
                print(f'dirnames are {probeDir} {new_dir}')
                if os.path.isdir(probeDir):
                    try:
                        print(f'Attempting to rename {probeDir} {new_dir}')
                        os.rename(probeDir, new_dir)
                    except Exception as E:
                        message = f'Cannot rename data directory for {slot}'
                        key = f'data_dir_{slot}_renamed'
                        data_missing[key] = message
                        print('')
                        print('')
                        print('______________________________________________________________________________')
                        print(message)
                        print(
                            'Please clear the sessionID field in open ephys, play acquisition and double click the record button to get open ephys to release the directory')
                        print('')
                        print('')
                if not (os.path.isdir(new_dir)):
                    key = f'data_dir_{slot}_found'
                    message = 'Cannot find data directory for ' + slot
                    data_missing[key] = message
                    print('')
                    print('')
                    print('______________________________________________________________________________')
                    print(message)
                    print('')
                    print('')
    except Exception as E:
        message = (f'Error checking data: see the prompt for more details')
        traceback.print_tb(E.__traceback__)
        npxc.alert_text(message, state)
    missing_files.update(data_missing)
    if missing_files:
        # state['external']['next_state'] = 'files_error1'
        npxc.alert_from_error_dict(state, missing_files)
    validation_type = state['external']['full_session_type'] + '_lims_ready'
    npxc.run_validation(state, validation_type)
    failed = npxc.get_validation_results(state, validation_type)
    state['external']['next_state'] = 'create_manifest_and_platform_json'
    if failed:
        foraging_failed = False
        for key, message in failed.items():
            if 'foraging' in key or 'foraging' in message:
                foraging_failed = True
        if foraging_failed:
            npxc.overrideable_error_state(state, 'check_files1', message=message)
            state['external']['next_state'] = 'foraging_ID_error'
        else:
            state['external']['next_state'] = 'files_error1'
            npxc.alert_from_error_dict(state, failed)
    state['external']['transition_result'] = True
    state['external']['status_message'] = 'success'


@state_transition
def files_error1_input(state):
    print(">> files_error_input <<")
    if "check_files_retry" in state["external"] and state["external"]["check_files_retry"]:
        state["external"].pop("check_files_retry", None)
        check_files1_input(state)
        #print("go back to initialize")
        #state["external"]["next_state"] = "check_files1"
    # elif "move_files_retry" in state["external"] and state["external"]["move_files_retry"]:
    #    print("go back to initialize")
    #    state["external"]["next_state"] = "remove_mouse_and_move_files2"
    #    state["external"].pop("move_files_retry", None)
    else:
        state["external"]["next_state"] = "create_manifest_and_platform_json"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def foraging_ID_error_input(state):
    #print('foraging ID:' + state['external']['foraging_id'])
    #print('stimulus_name:' + state['external']['stimulus_name'])
    #print('script_name:' + state['external']['script_name'])
    state['external']['retry_state'] = 'water_mouse'
    state['external']['override_state'] = 'check_files1'
    #npxc.overrideable_error_state_input(state)


@state_transition
def foraging_ID_error_exit(state):
    #npxc.overrideable_error_state_exit(state)
    pass


@state_transition
def create_manifest_and_platform_json_enter(state):
    """
    Input test function for state create_manifest_and_platform_json_and_sync_report
    """
    # LIMS Stuff will wait until I get the LIMS Session stuff sorted out with RH
    npxc.save_platform_json(state, manifest=True)
    print("going to workflow complete...")
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def cleanup_enter(state):
    """
    Entry function for state workflow_complete
    """
    print(f">> Cleanup enter!")

    # if aborting, make sure to stop capture and release cameras in Cam3d



@state_transition
def cleanup_input(state):
    """
    Input test function for state workflow_complete
    """
    print(f">> Cleanup Input <<")
    if'stop_streams' in state['external'] and state['external']['stop_streams']:
        npxc.stop_common_experiment_monitoring(state)

    npxc.save_platform_json(state, manifest=False)
    state["external"]["next_state"] = "workflow_complete"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def check_files2_input(state):
    print(">> check_files2_input <<")
    stop = npxc.check_files2(state)
    print(f"stop is {stop}")
    if stop:
        state["external"]["next_state"] = "files_error2"
    else:
        state["external"]["next_state"] = "initiate_data_processing"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def check_files2_revert(state):
    print(">> check_files2_revert <<")
    message = 'Reverting to this state will generate a second platform_json. You should delete the old one'
    npxc.alert_text(message, state)


@state_transition
def files_error2_input(state):
    print(">> files_error2_input <<")
    if "check_files2_retry" in state["external"] and state["external"]["check_files2_retry"]:
        print("go back to initialize")
        state["external"]["next_state"] = "check_files2"
        state["external"].pop("check_files2_retry", None)
    elif "create_files2_retry" in state["external"] and state["external"]["create_files2_retry"]:
        print("go back to initialize")
        state["external"]["next_state"] = "create_manifest_and_platform_json"
        state["external"].pop("create_files2_retry", None)
    else:
        state["external"]["next_state"] = "initiate_data_processing"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def initiate_data_processing_enter(state):
    """
    Entry function for state workflow_complete
    """
    print("enter workflow complete")
    logging.info("Neuropixels behavior workflow complete", extra={"weblog": True})


@state_transition
def initiate_data_processing_input(state):
    """
    Input test function for state workflow_complete
    """
    npxc.stop_ecephys_acquisition(state)
    print(">> initiate_data_processing_input <<")
    initiated = False
    try:
        initiated = npxc.initiate_data_processing(state)
    except Exception as E:
        message = f'Error initiating data processing: {E}'
        npxc.alert_text(message, state)
    if initiated:
        print("initiated all data processing sucessfully")
        state["external"]["next_state"] = "copy_files_to_network"
        state["external"].pop("data_retry", None)
    else:
        state["external"]["next_state"] = "data_error"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def data_error_input(state):
    print(">> data_error_input <<")
    if "data_retry" in state["external"] and state["external"]["data_retry"]:
        print("go back to initialize")
        state["external"]["next_state"] = "initiate_data_processing"
        state["external"].pop("data_retry", None)
    else:
        state["external"]["next_state"] = "copy_files_to_network"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def copy_files_to_network_input(state):
    """
    Input test function for state workflow_complete
    """
    print(">> copy_files_to_network_input <<")
    npxc.save_notes(state)
    npxc.backup_files(state)


@state_transition
def ready_to_check_network_input(state):
    """
    Input test function for state workflow_complete
    """
    # TODO This will be broken
    print(">> ready_to_check_network_input <<")
    npxc.save_notes(state)
    session_type = state['external']['full_session_type']
    try:
        if npxc.global_processes['network_backup_process'].poll() is None:
            message = 'Files are not finished copying to the network. Wait a bit and try again'
            fail_state(message, state)
    except Exception as E:
        npxc.alert_text('Failed to test if network backup is finished'. state)
    failed = {}
    failed = npxc.check_files_network(state, session_type, {1, 2})
    print(f"missing_files network is {failed}")
    validation_type = state['external']['full_session_type'] + '_local_qc'
    npxc.run_validation(state, validation_type)
    failed.update(npxc.get_validation_results(state, validation_type))
    state['external']['next_state'] = 'create_manifest_and_platform_json'
    if failed:
        npxc.alert_from_error_dict(state, failed)
        state["external"]["next_state"] = "network_backup_error"
    else:
        state["external"]["next_state"] = "workflow_complete"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def network_backup_error_input(state):
    print(">> files_error2_input <<")
    if "copy_retry" in state["external"] and state["external"]["copy_retry"]:
        print("go back to initialize")
        state["external"]["next_state"] = "copy_files_to_network"
        state["external"].pop("copy_retry", None)
    elif "data_retry" in state["external"] and state["external"]["data_retry"]:
        print("go back to initialize")
        state["external"]["next_state"] = "ready_to_check_network"
        state["external"].pop("data_retry", None)
    else:
        state["external"]["next_state"] = "workflow_complete"
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"


@state_transition
def workflow_complete_enter(state):
    """
    Entry function for state workflow_complete
    """
    print("enter workflow complete")
    logging.info("Neuropixels behavior workflow complete", extra={"weblog": True})
    state["external"]["workflow_complete_time"] = dt.now().strftime('%Y%m%d%H%M%S')


@state_transition
def workflow_complete_input(state):
    """
    Input test function for state workflow_complete
    """
    print("workflow complete input")

    state["external"]["next_state"] = None
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
