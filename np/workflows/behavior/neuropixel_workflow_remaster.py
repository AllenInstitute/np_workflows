# -*- coding: latin-1 -*-

import pdb
import sys

# pdb.set_trace()
# sys.path.append("...")
# sys.path.append("..")

try:
    # logging.warning("logging started")
    import datetime
    import inspect
    import json
    import logging
    import os
    import pathlib
    import socket
    import sys
    import threading
    import time
    import traceback
    import webbrowser
    from datetime import datetime as dt
    from importlib import reload
    from pprint import pformat

    import mpetk.aibsmw.routerio.router as router
    import requests
    import yaml
    import zmq
    from mpetk import limstk, mpeconfig, zro
    from mpetk.zro import Proxy
    from np.models.model import \
        DynamicRouting  # It can make sense to have a class to store experiment data.
    from np.services import mvr
    from np.services.ephys_api import \
        EphysHTTP as Ephys  # TODO unused - can move from npxcommon to workflow
    from np.services.mvr import MVRConnector
    from wfltk import middleware_messages_pb2 as messages  # name in new ver
    from wfltk import middleware_messages_pb2 as wfltk_msgs
    messages = wfltk_msgs

    from np.workflows import npxcommon as npxc
    
except Exception as e:
    # import errors aren't printed to console by default
    print(e)
    
# -------------- experiment-specific objects --------------
global config

config = mpeconfig.source_configuration('neuropixels', version='1.4.0')
#! #TODO line above is temporary, we want to consolidate config settings into one file 
config.update(mpeconfig.source_configuration("dynamic_routing"))


with open('np/config/neuropixels.yml') as f:
    yconfig = yaml.safe_load(f)
config.update(yconfig)
# pdb.set_trace()


experiment = DynamicRouting()
 
# ---------------- Network Service Objects ----------------

router: router.ZMQHandler
camstim_proxy: zro.Proxy = None
mouse_director_proxy: zro.Proxy = None
mvr_writer: mvr.MVRConnector
sync: zro.Proxy

# ------------------- UTILITY FUNCTIONS --------Flims-----------

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


def skip_states(state_globals, states_skipped, fields_skipped=()):
    for field in fields_skipped:
        state_globals['external'][field] = True
    for state_name in states_skipped:
        for transition in ['enter', 'input', 'exit']:
            func_name = state_name + '_' + transition
            default_func_name = 'default_' + transition
            if func_name in globals():
                method_to_call = globals()[func_name]
                method_to_call(state_globals)
            else:
                method_to_call = globals()[default_func_name]
                method_to_call(state_globals, state_name)


def state_transition(state_transition_function):
    def wrapper(state_globals, *args):
        try:
            # reload(npxc)
            transition_type = state_transition_function.__name__.split('_')[-1]
            if ((transition_type == 'input') or (transition_type == 'revert')) and ('msg_text' in state_globals["external"]):
                state_globals["external"].pop("msg_text")
            if args:
                state_transition_function(state_globals, args)
            else:
                state_transition_function(state_globals)
            npxc.save_platform_json(state_globals, manifest=False)
        except Exception as e:
            npxc.print_error(state_globals, e)
            message = f'An exception occurred in state transition {state_transition_function.__name__}'
            logging.debug(message)
            npxc.alert_text(message, state_globals)
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


# ------------------- State Transitions -------------------
@state_transition
def default_enter(state_globals, label):
    npxc.default_enter(state_globals, label)


@state_transition
def default_input(state_globals, label):
    npxc.default_input(state_globals, label)


@state_transition
def default_exit(state_globals, label):
    npxc.default_exit(state_globals, label)


@state_transition
def initialize_enter(state_globals):
    state_globals['external']['session_type'] = 'behavior_experiment'
    state_globals['external']['msg_text'] = 'username not found'
   
    """
    Processing_Agents = npxc.get_processing_agents(state_globals)
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
    """

    npxc.initialize_enter(state_globals)


@state_transition
def initialize_input(state_globals):
    """
    Input test function for state initialize
    """
    npxc.initialize_input(state_globals)

    #! this is done in npxc too- delete
    global mouse_director_proxy
    md_host = npxc.config['components']['MouseDirector']['host']
    md_port = npxc.config['components']['MouseDirector']['port']
    #! should md_host be localhost, not vidmon?
    mouse_director_proxy = Proxy(f'{md_host}:{md_port}')

    global camstim_proxy
    host = npxc.config["components"]["Stim"]["host"]
    port = npxc.config["components"]["Stim"]["port"]
    state_globals['mtrain_text'] = pformat(npxc.config['MTrainData'])
    camstim_proxy = Proxy(f"{host}:{port}", serialization="json")

    global router
    router = state_globals["resources"]["io"]

    result = limstk.user_details(state_globals["external"]["user_id"])


    if 'components_skip' in state_globals['external'] and state_globals['external']['components_skip']:
        failed = False
    else:
        failed = npxc.confirm_components(state_globals)


    beh_mon_str_def = 'Factors that interfere with behavior completely like feces on the lickspout or the chin/paws excessively bumping the spout should be amended'
    state_globals['external']['behavior_monitoring_string'] = npxc.get_from_config(['behavior_monitoring_string_exp'], default=beh_mon_str_def)
    state_globals['external']['dii_description'] = npxc.get_from_config(['dii_description'], default='CM-DiI 100%')

    defaults = ['day1', 'day2']
    key_list = ['MTrainData', 'Experiment_Sessions']
    experiment_sessions = npxc.get_from_config(key_list, defaults)
    #experiment_sessions = []
    #try:
    #    experiment_sessions = npxc.config['MTrainData']['Experiment_Sessions']
    #except Exception as E:
    #    message = f'Failed to find experiment sessions in  config. Using default instead'
    #    print(message)
    #    #npxc.alert_text(message, state_globals)
    #    experiment_sessions = ['day1', 'day2']

    state_globals["external"]["session_type_option_string"] = ', '.join(experiment_sessions)
    state_globals["external"]["session_types_options"] = experiment_sessions

    state_globals["external"]["next_state"] = "scan_mouse_id"
    if failed:
        alert_string = f'The following proxies are not available: {", ".join(failed)}'
        npxc.overrideable_error_state(state_globals, 'initialize', 'scan_mouse_id', message=alert_string)
    try:
        if result != -1:
            state_globals["external"]["lims_user_id"] = result[0]["id"]
            state_globals["external"]["status_message"] = "success"
            state_globals["external"]["local_log"] = f'User {state_globals["external"]["user_id"]} found in LIMS'
        else:
            state_globals["external"]["next_state"] = "initialize"
            fail_state(f'No LIMS ID for User:{state_globals["external"]["user_id"]} found in LIMS', state_globals)
    except (KeyError, IndexError):
        print('Failed user ID test')
        fail_state(f'No LIMS ID for User:{state_globals["external"]["user_id"]} found in LIMS', state_globals)
        state_globals["external"]["next_state"] = "initialize"    

    npxc.probes_need_cleaning(state_globals)

@state_transition
def components_error_input(state_globals):
    npxc.components_error_input(state_globals, 'scan_mouse_id')


@state_transition
def prepare_for_pretest_enter(state_globals):
    npxc.prepare_for_pretest_input(state_globals)


@state_transition
def prepare_for_pretest_input(state_globals):
    pass


@state_transition
def check_data_drives_input(state_globals):
    for slot in state_globals["external"]["PXI"]:
        state_globals["external"]["PXI"][slot] = state_globals["external"][f"slot_{slot}_drive"]




@state_transition
def start_pretest_input(state_globals):
    state_globals['external']['session_name'] = dt.now().strftime("%Y%m%d%H%M%S") + '_pretest'
    state_globals["external"]["local_lims_location"] = os.path.join(state_globals["external"]["local_lims_head"],
                                                                    state_globals['external']['session_name'])
    os.makedirs(state_globals["external"]["local_lims_location"], exist_ok=True)
    state_globals["external"]["mapped_lims_location"] = state_globals["external"]["local_lims_location"]
    state_globals["external"]["pretest_start_time"] = dt.now()
    npxc.set_open_ephys_name(state_globals)
    logging.info('starting monitoring with video prefix = pretest')
    npxc.start_common_experiment_monitoring(state_globals, video_prefix='pretest')
    npxc.start_pretest_stim(state_globals)

    foraging_id, stimulus_name, script_path = npxc.get_stim_status(camstim_proxy, state_globals)
    npxc.verify_script_name(state_globals, stimulus_name)

    failed = npxc.establish_data_stream_size(state_globals)
    #state_globals['external']['failure_messages'] = failed or {}
    if failed:
        fail_message_1 = npxc.alert_from_error_dict(state_globals, failed, primary_key=False)
        logging.debug(fail_message_1)


@state_transition
def pretest_input(state_globals):
    if not('water_calibration_heights' in state_globals["external"]):
        state_globals["external"]["water_calibration_heights"] = []

    if not('water_calibration_volumes' in state_globals["external"]):
        state_globals["external"]["water_calibration_volumes"] = []

    height_match = False
    mass_match = False
    if len(state_globals["external"]["water_calibration_heights"]):
        height_match = state_globals["external"]["water_height"] == state_globals["external"]["water_calibration_heights"][-1]
        mass_match = state_globals["external"]["water_mass"] == state_globals["external"]["water_calibration_volumes"][-1]

    if not(height_match) or not(mass_match):
        try:
            water_height = float(state_globals["external"]["water_height"])
            water_mass = float(state_globals["external"]["water_mass"])

            state_globals["external"]["water_calibration_heights"].append(water_height)
            state_globals["external"]["water_calibration_volumes"].append(water_mass)
            key_list = ['expected_water_mass']
            expected_water_mass = npxc.get_from_config(key_list, default=0.050)
            key_list = ['acceptable_water_mass_diff']
            acceptable_water_mass_diff = npxc.get_from_config(key_list, default=0.005)
            if abs(water_mass -expected_water_mass) > acceptable_water_mass_diff and not(water_mass==0):
                message = f'The reported water mass of {water_mass} is more than {acceptable_water_mass_diff} from the expected mass of {expected_water_mass}. Please calibrate the solonoid or adjust the level in the reservior syringe.'
                npxc.alert_text(message, state_globals)
        except Exception as E:
            message = 'The values entered must be numbers. Please enter a number'
            fail_state(message, state_globals)
            return

    camstim_running = npxc.camstim_running(state_globals)
    if camstim_running:
        message = 'Camstim seems to be running a stimulus. are you sure the stim is done?'
        npxc.alert_text(message, state_globals)
        state_globals['external']['next_state'] = 'pretest_stim_finished_error'
        return

    npxc.pretest_wrapup(state_globals)


@state_transition
def pretest_exit(state_globals):
    pass


@state_transition
def pretest_stim_finished_error_input(state_globals):
    npxc.handle_2_choice_button('pretest_stim_wait', 'pretest', 'configure_hardware_videomon', state_globals)
    if state_globals['external']['next_state'] == 'configure_hardware_videomon':
        npxc.pretest_wrapup(state_globals)

@state_transition
def pretest_error_input(state_globals):
    npxc.pretest_error_input(state_globals)


@state_transition
def scan_mouse_id_input(state_globals):
    """
    Input test function for state initialize
    """
    comp_id = os.environ.get('aibs_comp_id', socket.gethostname())
    mouse_id = state_globals["external"]["mouse_id"]
    user_id = state_globals["external"]["user_id"]

    logging.info(f'MID, {mouse_id}, UID, {user_id}, BID, {comp_id}, Received', extra={'weblog':True})

    state_globals["external"]["local_log"] = f'Mouse ID :{mouse_id}'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    state_globals["external"]["clear_sticky"] = True

    #npxc.start_ecephys_acquisition(state_globals)

    try:
        result = limstk.donor_info(mouse_id)
        if "dummy_mode" in state_globals["external"] and (state_globals["external"]["dummy_mode"] == True):
            trigger_dir = npxc.config['dummy_trigger_dir']
        else:
            if "project" in result[0]["specimens"][0]:
                trigger_dir = result[0]["specimens"][0]["project"]["trigger_dir"]
            else:
                trigger_dir = state_globals["external"]["trigger_dir"]

        local_trigger_dir = npxc.config['local_trigger_dir']
        state_globals["external"]["dummy_mode"] = False

    except limstk.LIMSUnavailableError:
        message = f'Could not retrieve donor_info for {mouse_id} from LIMS.'
        fail_state(message, state_globals)
        return

    npxc.assess_previous_sessions(state_globals)
    session_dict = state_globals['external']['exp_sessions']
    session_count = npxc.count_sessions(session_dict, state_globals['external']['mouse_id'])
    guess_exp_day = 'Day1'
    if session_count:
        guess_exp_day = 'Day2'
    state_globals['external']['entered_experiment_day'] = guess_exp_day

    state_globals["external"]["local_trigger_dir"] = local_trigger_dir
    state_globals["external"]["lims_specimen_id"] = result[0]["specimens"][0]["id"]
    state_globals["external"]["specimen.Name"] = result[0]["name"]
    state_globals["external"]["lims_project_id"] = result[0]["specimens"][0]["project_id"]
    state_globals["external"]["Project.Trigger_dir"] = trigger_dir

    if "project" in result[0]["specimens"][0]:
        state_globals["external"]["Project.Code"] = result[0]["specimens"][0]["project"].get("code", "No Project Code")
    else:
        state_globals["external"]["Project.Code"] = "No Project Code"

    state_globals["external"]["Project.Code1"] = state_globals["external"]["Project.Code"]
    state_globals["external"]["Project.Code2"] = state_globals["external"]["Project.Code"]
    state_globals["external"]["Project.Code3"] = state_globals["external"]["Project.Code"]
    state_globals["external"]["Project.Code4"] = state_globals["external"]["Project.Code"]

    # generate a session name
    session_name_timestamp = dt.now().strftime("%Y%m%d")
    session_name_string = f'{state_globals["external"]["mouse_id"]}_{session_name_timestamp}'
    logging.info(f'Session name: {session_name_string}')
    state_globals["external"]["session_name"] = session_name_string
    state_globals["external"]["sessionNameTimestamp"] = session_name_timestamp
    state_globals['external']['Auto.Date.String'] = dt.now().strftime("%Y%m%d")
    state_globals['external']['Auto.Date.String1'] = state_globals['external']['Auto.Date.String']
    state_globals['external']['next_state'] = 'LIMS_request'

    if 'use_auto' in state_globals['external'] and state_globals['external']['use_auto']:
        states_skipped = ['LIMS_request', 'date_string_check']
        fields_skipped = ['auto_generated_date_string', 'Project.Code.lims']
        skip_states(state_globals, states_skipped, fields_skipped)
        state_globals['external']['next_state'] = 'check_experiment_day'


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
def LIMS_request_revert(state_globals):
    ...

def date_string_check_enter(state_globals):
    state_globals['external']['Manual.Date.String'] = state_globals['external']['Auto.Date.String']


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

    # try:
    #     notes_proxy = state["component_proxies"]["Notes"]
    #     notes_proxy.setID(str(state["external"]["mouse_id"]), str(state["external"]["session_name"]))
    #     notes_proxy.setNoSurgery(True)
    #     state["external"]["status_message"] = "success"
    #     state["external"]["component_status"]["Notes"] = True
    # except KeyError:
    #     fail_state('SurgeryNotes proxy is not defined.', state)
    #     state["external"]["component_status"]["Notes"] = False
    # except Exception:
    #     fail_state('Error setting mouse and session name in SurgeryNotes', state)
    #     state["external"]["component_status"]["Notes"] = False


    mapped_lims_location = f"{npxc.config['mapped_lims_location']}/{state['external']['session_name']}"
    state["external"]["mapped_lims_location"] = mapped_lims_location
    state["external"]["local_lims_location"] = local_lims_location
    state["external"]["non_pretest_mapped_lims_location"] = state["external"]["local_lims_location"]
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    

@state_transition
def date_string_check_revert(state_globals):
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
def ecephys_id_check_enter(state_globals):
    """
    Input test function for state ecephys_id_check
    """
    state_globals['external']["oephys_dir"] = os.path.join(os.getcwd(), "np/images/oephys_dir.png")# R"C:\progra~1\AIBS_MPE\workflow_launcher\dynamic_routing\oephys_dir.png"

@state_transition
def ecephys_id_check_input(state_globals):
    pass
    # state_globals["external"]["transition_result"] = True
    # state_globals["external"]["status_message"] = "success"


@state_transition
def check_experiment_day_enter(state_globals):
    url = f'http://mtrain:5000'
    webbrowser.open(url)
    state_globals["external"]["clear_sticky"] = True
    npxc.start_ecephys_acquisition(state_globals)


@state_transition
def check_experiment_day_input(state_globals):
    entered_experiment_day = state_globals['external']['entered_experiment_day']
    session_types_options = state_globals['external']['session_types_options']
    if not (entered_experiment_day in session_types_options):
        message = 'Session Type Not Valid: please type exactly as listed'
        fail_state(message, state_globals)
    else:
        state_globals['external']['full_session_type'] = state_globals['external'][
                                                             'session_type'] + '_' + entered_experiment_day.lower()

        if 'day1' in state_globals['external']['full_session_type'] :
            state_globals['external']['dii_description'] = npxc.get_from_config(['dii_description_day1'], default='CM-DiI 100%')
            message = 'Is the ISO off?'
            npxc.alert_text(message, state_globals)
        if 'day2' in state_globals['external']['full_session_type'] :
            state_globals['external']['dii_description'] = npxc.get_from_config(['dii_description_day2'], default='CM-DiI 100%')


@state_transition
def configure_hardware_camstim_enter(state_globals):
    """
    Entry function for state configure_hardware_camstim
    """
    pass


@state_transition
def configure_hardware_camstim_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def configure_hardware_videomon_enter(state_globals):
    """
    Entry function for state configure_hardware_camstim
    """
    try:
        state_globals['external']['session_name'] = state_globals['external']['non_pretest_session_name']
        state_globals["external"]["local_lims_location"] = state_globals["external"]["non_pretest_mapped_lims_location"]
        state_globals["external"]["mapped_lims_location"] = state_globals["external"][
            "non_pretest_mapped_lims_location"]
    except KeyError as E:
        npxc.alert_text('Failed to reset session name after pretest', state_globals)
    pass


@state_transition
def configure_hardware_openephys_enter(state_globals):
    """
    Input test function for state configure_hardware_openephys

    """
    print('>>>configure hardware openephys<<<')
    # npxc.set_open_ephys_name(state_globals)
    # configure sync here so that its ready for pretest, but doesn't need to be done multiple times
    npxc.start_ecephys_acquisition(state_globals)
    try:
        sync_proxy = state_globals["component_proxies"]["Sync"]
        sync_proxy.init()
        # sync_proxy.load_config("C:/ProgramData/AIBS_MPE/sync/last.yml")  # TODO: We should put this in zookeeper
        state_globals["external"]["status_message"] = "success"
        state_globals["external"]["component_status"]["Sync"] = True
    except KeyError:
        fail_state('Sync proxy undefined', state_globals)
        state_globals["external"]["component_status"]["Sync"] = False
    except Exception as e:
        fail_state(f"Sync load config failure:{e}", state_globals)
        state_globals["external"]["component_status"]["Sync"] = False


@state_transition
def configure_hardware_openephys_exit(state_globals):
    """
    Exit function for state configure_hardware_openephys
    """
    # set the data file path for open ephys
    # npxc.set_open_ephys_name(state_globals)
    pass


@state_transition
def configure_hardware_openephys_input(state_globals):
    """
    Input test function for state configure_hardware_openephys
    """
    npxc.handle_2_choice_button('pretest_run', 'start_pretest', 'configure_hardware_videomon', state_globals)
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def configure_hardware_rig_input(state_globals):
    """
    Input function for configure_hardware_rig
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def calibrate_probe_offset_input(state_globals):
    """
    Input function for calibrate_probe_offset
    """
    # state_globals['external']['next_state'] = 'align_probes_start'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def align_probes_start_input(state_globals):
    """
    Input test function for state align_probes_start
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def align_probes_start_revert(state_globals):
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
def probes_not_aligned_input(state_globals):
    """
    Input test function for state probes_not_aligned
    """
    # state_globals['external']['next_state'] = 'probe_abort_confirm'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def check_stimulus_enter(state_globals):
    url = f'http://mtrain:5000/set_state/{state_globals["external"]["mouse_id"]}'
    webbrowser.open(url)
    session_day = state_globals['external']['entered_experiment_day']
    key_list = ['MTrainData', 'Experiment_Sessions', session_day, 'Full_String']
    state_globals['external']['mtrain_string'] = npxc.get_from_config(key_list, default='')

    state_globals["external"]["clear_sticky"] = True




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
def diI_probe_depth_confirmation_enter(state_globals):
    """
    Entry function for state diI_probe_depth_confirmation
    """


@state_transition
def diI_probe_depth_confirmation_input(state_globals):
    """
    Input test function for state diI_probe_depth_confirmation
    """
    ...


@state_transition
def diI_photoDoc_setup_input(state):
    """
    Input test function for state diI_photoDoc_setup
    """
    try:
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
                npxc.mvr_capture(state,pre_experiment_left_path)
                npxc.mvr_capture(state,pre_experiment_right_path)

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
        
    except:
        # state_globals['external']['next_state'] = 'diI_photoDocumentation'
        state["external"]["transition_result"] = True
        state["external"]["status_message"] = "success"




@state_transition
def diI_photoDocumentation_input(state_globals):
    """
    Input test function for state diI_photoDocumentation
    """
    if "retake_image_1" in state_globals["external"] and state_globals["external"]["retake_image_1"]:
        print("go back to diI_photoDoc_setup")
        state_globals["external"]["next_state"] = "diI_photoDoc_setup"
        state_globals["external"].pop("retake_pre_experiment_image", None)
    else:
        state_globals["external"]["next_state"] = "diI_info_and_remove"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def diI_info_and_remove_enter(state_globals):
    """
    Entry function for state diI_info_and_remove
    """


@state_transition
def diI_info_and_remove_input(state_globals):
    """
    Exit function for state diI_info_and_remove
    """
    # create a diI application timestamp (YYYYMMDDHHMMSS)

    state_globals["external"]["DiINotes"]["EndTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state_globals["external"][
        "local_log"
    ] = f'diI Application End Time:{state_globals["external"]["DiINotes"]["EndTime"]}'
    print(f'diI Application End Time:{state_globals["external"]["DiINotes"]["EndTime"]}')

    # state_globals['external']['DiINotes']['DiI_Concentration'] = state_globals['external']['dye_concentration']
    state_globals["external"]["DiINotes"]["times_dipped"] = state_globals["external"]["fresh"]
    state_globals["external"]["DiINotes"]["dii_description"] = state_globals["external"]["dii_description"]

    # state_globals['external']['DiINotes']['TimesDipped'] = state_globals['external']['times_dipped']

    # state_globals['external']['next_state'] = 'load_mouse'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)

@state_transition
def load_mouse_enter(state_globals):
    npxc.load_mouse_enter(state_globals)

@state_transition
def load_mouse_input(state_globals):
    """
    Input test function for state load_mouse_headframe
    """
    # create a mouse in headframe timestamp (YYYYMMDDHHMMSS)
    # state_globals['external']['mouse_in_headframe_holder']
    state_globals["external"]["HeadFrameEntryTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state_globals["external"]["local_log"] = f'HeadFrameEntryTime:{state_globals["external"]["HeadFrameEntryTime"]}'
    # state_globals['external']['next_state'] = 'lower_probe_cartridge'
    message = npxc.load_mouse_behavior(state_globals)
    if message:
        fail_state(message, state_globals)
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def load_mouse_revert(state_globals):
    print("doing load mouse revert stuff")


@state_transition
def lower_probe_cartridge_enter(state_globals):
    state_globals["external"]["clear_sticky"] = True


@state_transition
def ground_connected_check_input(state_globals):
    """
    Input test function for state ground_connected_check
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if "ground_not_connected" in state_globals["external"] and state_globals["external"]["ground_not_connected"]:
        state_globals["external"]["next_state"] = "ground_abort_confirm"
    elif "ground_connected" in state_globals["external"] and state_globals["external"]["ground_connected"]:
        state_globals["external"]["next_state"] = "eyetracking_dichroic"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False


@state_transition
def ground_abort_confirm_input(state_globals):
    """
    Input test function for state initialize
    """
    print(">> ground abort_confirm_input <<")

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if "ground_abort_experiment" in state_globals["external"] and state_globals["external"]["ground_abort_experiment"]:
        print("&& ground abort_experiment")
        state_globals["external"]["next_state"] = "ground_abort_shutdown"
    elif "ground_abort_cancel" in state_globals["external"] and state_globals["external"]["ground_abort_cancel"]:
        print("&& ground abort_cancel")
        state_globals["external"]["next_state"] = "eyetracking_dichroic"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False


@state_transition
def ground_abort_confirm_revert(state_globals):
    print("doing ground_abort_confirm_revert stuff")


@state_transition
def ground_abort_shutdown_input(state_globals):
    """
    Input test function for state initialize
    """
    print(">> ground_abort_shutdown_input <<")

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    state_globals["external"]["next_state"] = "remove_mouse_and_move_files2"


@state_transition
def ground_abort_shutdown_revert(state_globals):
    print("ground_abort_shutdown_confirm_revert stuff")


@state_transition
def eyetracking_dichroic_input(state_globals):
    """
    Input test function for state eyetracking_dichroic
    """
    # state_globals['external']['next_state'] = 'eye_visible_check'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def eye_visible_check_input(state_globals):
    """
    Input test function for state eye_visible_check
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if "eye_not_visible" in state_globals["external"] and state_globals["external"]["eye_not_visible"]:
        state_globals["external"]["next_state"] = "eye_visible_abort_confirm"
    elif "eye_visible" in state_globals["external"] and state_globals["external"]["eye_visible"]:
        state_globals["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False


@state_transition
def eye_visible_abort_confirm_input(state_globals):
    """
    Input test function for state initialize
    """
    print(">> eye_visible_abort_confirm_input <<")

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if (
        "eye_visible_abort_experiment" in state_globals["external"]
        and state_globals["external"]["eye_visible_abort_experiment"]
    ):
        state_globals["external"]["next_state"] = "remove_mouse_and_move_files2"
    elif (
        "eye_visible_abort_cancel" in state_globals["external"]
        and state_globals["external"]["eye_visible_abort_cancel"]
    ):
        state_globals["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False


@state_transition
def eye_visible_abort_confirm_revert(state_globals):
    print("doing ground_abort_confirm_revert stuff")


@state_transition
def lower_probe_cartridge_input(state_globals):
    """
    Input test function for state lower_probe_cartridge
    """
    # create a probe_cartridge_lower timestamp (YYYYMMDDHHMMSS)
    if state_globals["external"]["probe_cartridge_lower"]:
        state_globals["external"]["CartridgeLowerTime"] = dt.now().strftime("%Y%m%d%H%M%S")
        state_globals["external"][
            "local_log"
        ] = f'CartridgeLowerTime: {state_globals["external"]["CartridgeLowerTime"]}'
    # state_globals['external']['next_state'] = 'brain_surface_focus'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)
    try:
        proxy = state_globals["component_proxies"]["Notes"]
        try:
            npxc.rename_mlog(state_globals)
        except Exception as e:
            print(f"Notes rename log failure:{e}!")
    except Exception as e:
        print(f"Notes proxy failure:{e}!")


@state_transition
def brain_surface_focus_enter(state_globals):
    state_globals["external"]["clear_sticky"] = True


@state_transition
def brain_surface_focus_input(state_globals):
    """
    Input test function for state brain_surface_focus
    """
    if state_globals["external"]["dummy_mode"]:
        surface_2_left_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image2-left.png"),
        )
        surface_2_right_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image2-right.png"),
        )
        surface_2_local_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image2-left.png"),
        )
    else:
        surface_2_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-left.png'
        surface_2_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-right.png'
        surface_2_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-left.png'
    surface_2_left_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image2-left.png"),
    )
    surface_2_right_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image2-right.png"),
    )
    try:
        proxy = state_globals["component_proxies"]["Cam3d"]

        print(">>>>>>> brain_surface_image")
        print(f"surface_2_left_path:{surface_2_left_path}")
        print(f"surface_2_right_path:{surface_2_right_path}")
        print(f"surface_2_local_path:{surface_2_local_path}")
        print("<<<<<<<")

        state_globals["external"][
            "surface_2_left_name"
        ] = f'{state_globals["external"]["session_name"]}_surface-image2-left.png'
        state_globals["external"][
            "surface_2_right_name"
        ] = f'{state_globals["external"]["session_name"]}_surface-image2-right.png'

        try:
            npxc.mvr_capture(state_globals,surface_2_left_path)
            npxc.mvr_capture(state_globals,surface_2_right_path)

            state_globals["external"]["status_message"] = "success"
            state_globals["external"]["local_log"] = f"Surface_2_Path:{surface_2_local_path}"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state_globals["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state_globals["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state_globals["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state_globals["external"]["component_status"]["Cam3d"] = False

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
        state_globals["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state_globals["external"]["local_log"] = f"surface_2_path:{surface_2_local_path}"
    state_globals["external"][
        "surface_2_file_location"
    ] = surface_2_local_path  # this is what gets displayed in the GUI
    if not(os.path.exists(surface_2_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_2_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals["external"]["surface_2_left_local_file_location"] = surface_2_left_local_path
    state_globals["external"]["surface_2_right_local_file_location"] = surface_2_right_local_path

    # state_globals['external']['next_state'] = 'insert_probes_start'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def insert_probes_start_enter(state_globals):
    """
    Entry function for state insert_probes_start_enter
    """
    print("&& input probes start enter &&")
    npxc.set_open_ephys_name(state_globals)
    # start the open ephys acquisitino
    npxc.start_ecephys_acquisition(state_globals)


@state_transition
def insert_probes_start_input(state_globals):
    """
    Input test function for state insert_probes_start
    """
    # create a insert_probes timestamp (YYYYMMDDHHMMSS)
    print("&& insert probes input &&")

    if "retake_image_2" in state_globals["external"] and state_globals["external"]["retake_image_2"]:
        print("go back to brain surface focus")
        state_globals["external"]["next_state"] = "brain_surface_focus"
        state_globals["external"].pop("retake_image_2", None)
    else:
        state_globals["external"]["next_state"] = "confirm_ISI_match"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def probe_brain_surface_enter(state_globals):
    """
    Entry function for state probe_brain_surface
    """
    state_globals["external"]["clear_sticky"] = True


@state_transition
def probe_brain_surface_input(state_globals):
    """
    Input test function for state probe_brain_surface
    """

    # check to see if the none is checked

    probes_at_surface = 0
    probes_at_surface_list = []

    if "probe_A_surface" in state_globals["external"] and state_globals["external"]["probe_A_surface"]:
        state_globals["external"]["probe_A_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("A")
    else:
        state_globals["external"]["probe_A_surface_failure"] = True

    if "probe_B_surface" in state_globals["external"] and state_globals["external"]["probe_B_surface"]:
        state_globals["external"]["probe_B_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("B")
    else:
        state_globals["external"]["probe_B_surface_failure"] = True

    if "probe_C_surface" in state_globals["external"] and state_globals["external"]["probe_C_surface"]:
        state_globals["external"]["probe_C_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("C")
    else:
        state_globals["external"]["probe_C_surface_failure"] = True

    if "probe_D_surface" in state_globals["external"] and state_globals["external"]["probe_D_surface"]:
        state_globals["external"]["probe_D_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("D")
    else:
        state_globals["external"]["probe_D_surface_failure"] = True

    if "probe_E_surface" in state_globals["external"] and state_globals["external"]["probe_E_surface"]:
        state_globals["external"]["probe_E_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("E")
    else:
        state_globals["external"]["probe_E_surface_failure"] = True

    if "probe_F_surface" in state_globals["external"] and state_globals["external"]["probe_F_surface"]:
        state_globals["external"]["probe_F_surface_timestamp"] = dt.now().strftime("%Y%m%d%H%M%S")
        probes_at_surface += 1
        probes_at_surface_list.append("F")
    else:
        state_globals["external"]["probe_F_surface_failure"] = True

    if probes_at_surface >= 3:
        print(">>> More than 3 probes at surface")
        state_globals["external"]["local_log"] = "More than 3 probes at surface"
        state_globals["external"]["next_state"] = "photodoc_setup3"
    else:
        print(">>> Less than 3 probes at surface")
        state_globals["external"]["local_log"] = "Less than 3 probes at surface"
        state_globals["external"]["next_state"] = "brain_surface_abort_confirm"

    state_globals["external"]["probe_a_agar_insertions"] = state_globals["external"][
        "probe_b_agar_insertions"
    ] = state_globals["external"]["probe_c_agar_insertions"] = state_globals["external"][
        "probe_d_agar_insertions"
    ] = state_globals[
        "external"
    ][
        "probe_e_agar_insertions"
    ] = state_globals[
        "external"
    ][
        "probe_f_agar_insertions"
    ] = 1

    # Set the default values here so they don't reset if you need to revert
    for probe in state_globals["external"]["probe_list"]:
        lower_probe = probe.lower()
        state_globals["external"][f"probe_{lower_probe}_location_changed"] = None
        state_globals["external"][f"probe_{lower_probe}_bending_severity"] = 0
        state_globals["external"][f"probe_{lower_probe}_agar_insertions"] = 1
        state_globals["external"][f"probe_{lower_probe}_insert_failure"] = 0
        state_globals["external"][f"probe_{lower_probe}_bending_elsewhere_severity"] = 0

    state_globals["external"]["probes_aligned_string"] = ", ".join(probes_at_surface_list)
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def brain_surface_abort_confirm_input(state_globals):
    """
    Input test function for state initialize
    """
    print(">> brain_surface_abort_confirm_input <<")

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if (
        "brain_surface_abort_experiment" in state_globals["external"]
        and state_globals["external"]["brain_surface_abort_experiment"]
    ):
        state_globals["external"]["next_state"] = "end_experiment_photodocumentation"
    elif (
        "brain_surface_abort_cancel" in state_globals["external"]
        and state_globals["external"]["brain_surface_abort_cancel"]
    ):
        state_globals["external"]["next_state"] = "lower_probe_cartridge"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False

@state_transition
def photodoc_setup3_enter(state_globals):
    message = f"- Adjust the zoom to {state_globals['external']['high_zoom_level']}"
    npxc.alert_text(message, state_globals)

@state_transition
def photodoc_setup3_input(state_globals):
    """

    """
    if state_globals["external"]["dummy_mode"]:
        surface_3_left_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image3-left.png"),
        )
        surface_3_right_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image3-right.png"),
        )
        surface_3_local_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image3-left.png"),
        )
    else:
        surface_3_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image3-left.png'
        surface_3_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image3-right.png'
        surface_3_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image3-left.png'
    surface_3_left_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image3-left.png"),
    )
    surface_3_right_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image3-right.png"),
    )
    try:
        proxy = state_globals["component_proxies"]["Cam3d"]

        print(">>>>>>> pre_insertion_surface")
        print(f"surface_3_left_path:{surface_3_left_path}")
        print(f"surface_3_right_path:{surface_3_right_path}")
        print(f"surface_3_local_path:{surface_3_local_path}")
        print("<<<<<<<")

        state_globals["external"][
            "surface_3_left_name"
        ] = f'{state_globals["external"]["session_name"]}_surface-image3-left.png'
        state_globals["external"][
            "surface_3_right_name"
        ] = f'{state_globals["external"]["session_name"]}_surface-image3-right.png'

        try:
            print(f"saving left image to {surface_3_left_path}")
            npxc.mvr_capture(state_globals,surface_3_left_path)
            print(f"saving left image to {surface_3_right_path}")
            npxc.mvr_capture(state_globals,surface_3_right_path)

            state_globals["external"]["status_message"] = "success"
            state_globals["external"]["local_log"] = f"Surface_3_Path:{surface_3_local_path}"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state_globals["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state_globals["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state_globals["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state_globals["external"]["component_status"]["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_3_left_local_path)
    right_image_result = os.path.isfile(surface_3_right_local_path)

    image_error_message = "Image Error:"

    if not left_image_result:
        image_error_message += " Left Image Not Taken!"

    if not right_image_result:
        image_error_message += " Right Image Not Taken!"

    if not (left_image_result and right_image_result):
        state_globals["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3dViewer, and restart if necessary.  " "Retake Image",
        }

    state_globals["external"]["local_log"] = f"surface_3_path:{surface_3_local_path}"
    state_globals["external"]["surface_3_file_location"] = surface_3_local_path  # this is what gets displayed in GUI
    if not(os.path.exists(surface_3_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_3_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals["external"]["surface_3_left_local_file_location"] = surface_3_left_local_path
    state_globals["external"]["surface_3_right_local_file_location"] = surface_3_right_local_path

    # state_globals['external']['next_state'] = 'photodoc_confirm3'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def photodoc_confirm3_input(state_globals):
    """
    Input test function for state lower_probes_automatically
    """
    # create a insert_probes timestamp (YYYYMMDDHHMMSS)

    if "retake_image_3" in state_globals["external"] and state_globals["external"]["retake_image_3"]:
        state_globals["external"]["next_state"] = "photodoc_setup3"
        state_globals["external"].pop("retake_image_3", None)
    else:
        state_globals["external"]["next_state"] = "lower_probes_automatically"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)

@state_transition
def lower_probes_automatically_enter(state_globals):
    message = f"- Adjust the zoom to {state_globals['external']['low_zoom_level']}"
    npxc.alert_text(message, state_globals)

@state_transition
def lower_probes_automatically_input(state_globals):
    """
    Input test function for state eyetracking_dichroic
    """
    probe_insertion_start_time = dt.now().strftime("%Y%m%d%H%M%S")
    state_globals["external"]["ProbeInsertionStartTime"] = probe_insertion_start_time
    state_globals["external"][
        "local_log"
    ] = f'ProbeInsertionStartTime:{state_globals["external"]["ProbeInsertionStartTime"]}'

    InsertionNotes = {}
    if not ("InsertionNotes" in state_globals["external"]):
        state_globals["external"]["InsertionNotes"] = {}

    state_globals["external"]["next_state"] = "probe_a_notes"
    npxc.handle_2_choice_button('skip_insertion_notes', 'probes_final_depth', 'probe_a_notes', state_globals)
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def probe_a_notes_input(state_globals):
    """
    Input test function for state probe_a_notes
    """

    probe_a_notes = {}

    if "probe_A_surface_timestamp" in state_globals["external"]:
        probe_a_notes["InsertionTimes"] = state_globals["external"]["probe_A_surface_timestamp"]

    probe_a_notes["ProbeLocationChanged"] = state_globals["external"]["probe_a_location_changed"]
    probe_a_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_a_bending_severity"]
    probe_a_notes["NumAgarInsertions"] = state_globals["external"]["probe_a_agar_insertions"]
    probe_a_notes["FailedToInsert"] = state_globals["external"]["probe_a_insert_failure"]
    probe_a_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_a_bending_elsewhere_severity"]

    state_globals["external"]["InsertionNotes"]["ProbeA"] = probe_a_notes

    state_globals["external"]["next_state"] = "probe_b_notes"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def probe_b_notes_input(state_globals):
    """
    Input test function for state probe_b_notes
    """
    probe_b_notes = {}

    if "probe_B_surface_timestamp" in state_globals["external"]:
        probe_b_notes["InsertionTimes"] = state_globals["external"]["probe_B_surface_timestamp"]

    probe_b_notes["ProbeLocationChanged"] = state_globals["external"]["probe_b_location_changed"]
    probe_b_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_b_bending_severity"]
    probe_b_notes["NumAgarInsertions"] = state_globals["external"]["probe_b_agar_insertions"]
    probe_b_notes["FailedToInsert"] = state_globals["external"]["probe_b_insert_failure"]
    probe_b_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_b_bending_elsewhere_severity"]

    state_globals["external"]["InsertionNotes"]["ProbeB"] = probe_b_notes

    state_globals["external"]["next_state"] = "probe_c_notes"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def probe_c_notes_input(state_globals):
    """
    Input test function for state probe_c_notes
    """
    probe_c_notes = {}

    if "probe_C_surface_timestamp" in state_globals["external"]:
        probe_c_notes["InsertionTimes"] = state_globals["external"]["probe_C_surface_timestamp"]

    probe_c_notes["ProbeLocationChanged"] = state_globals["external"]["probe_c_location_changed"]
    probe_c_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_c_bending_severity"]
    probe_c_notes["NumAgarInsertions"] = state_globals["external"]["probe_c_agar_insertions"]
    probe_c_notes["FailedToInsert"] = state_globals["external"]["probe_c_insert_failure"]
    probe_c_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_c_bending_elsewhere_severity"]

    state_globals["external"]["InsertionNotes"]["ProbeC"] = probe_c_notes

    state_globals["external"]["next_state"] = "probe_d_notes"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def probe_d_notes_input(state_globals):
    """
    Input test function for state probe_d_notes
    """
    probe_d_notes = {}

    if "probe_D_surface_timestamp" in state_globals["external"]:
        probe_d_notes["InsertionTimes"] = state_globals["external"]["probe_D_surface_timestamp"]

    probe_d_notes["ProbeLocationChanged"] = state_globals["external"]["probe_d_location_changed"]
    probe_d_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_d_bending_severity"]
    probe_d_notes["NumAgarInsertions"] = state_globals["external"]["probe_d_agar_insertions"]
    probe_d_notes["FailedToInsert"] = state_globals["external"]["probe_d_insert_failure"]
    probe_d_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_d_bending_elsewhere_severity"]

    state_globals["external"]["InsertionNotes"]["ProbeD"] = probe_d_notes

    state_globals["external"]["next_state"] = "probe_e_notes"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def probe_e_notes_input(state_globals):
    """
    Input test function for state probe_e_notes
    """
    probe_e_notes = {}

    if "probe_E_surface_timestamp" in state_globals["external"]:
        probe_e_notes["InsertionTimes"] = state_globals["external"]["probe_E_surface_timestamp"]

    probe_e_notes["ProbeLocationChanged"] = state_globals["external"]["probe_e_location_changed"]
    probe_e_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_e_bending_severity"]
    probe_e_notes["NumAgarInsertions"] = state_globals["external"]["probe_e_agar_insertions"]
    probe_e_notes["FailedToInsert"] = state_globals["external"]["probe_e_insert_failure"]
    probe_e_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_e_bending_elsewhere_severity"]

    state_globals["external"]["InsertionNotes"]["ProbeE"] = probe_e_notes

    state_globals["external"]["next_state"] = "probe_f_notes"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def probe_f_notes_input(state_globals):
    """
    Input test function for state probe_f_notes
    """
    probe_f_notes = {}

    if "probe_F_surface_timestamp" in state_globals["external"]:
        probe_f_notes["InsertionTimes"] = state_globals["external"]["probe_F_surface_timestamp"]

    probe_f_notes["ProbeLocationChanged"] = state_globals["external"]["probe_f_location_changed"]
    probe_f_notes["ProbeBendingOnSurface"] = state_globals["external"]["probe_f_bending_severity"]
    probe_f_notes["NumAgarInsertions"] = state_globals["external"]["probe_f_agar_insertions"]
    probe_f_notes["FailedToInsert"] = state_globals["external"]["probe_f_insert_failure"]
    probe_f_notes["ProbeBendingElsewhere"] = state_globals["external"]["probe_f_bending_elsewhere_severity"]

    state_globals['external']['InsertionNotes']['ProbeF'] = probe_f_notes

    state_globals["external"]["next_state"] = "probes_final_depth"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def probes_final_depth_enter(state_globals):
    """
    Entry function for state insertion_photodocumentation
    """
    message = 'No probes had bumping limitations'
    alert = False
    probe_restrctions = {}
    probe_restrctions_string = '\n'
    probe_restrctions_template = 'Probe {}: {}\n'
    for probe in state_globals['external']['probe_list']:
        key = 'probe_' + probe + '_DiI_depth'
        max_depth = state_globals["external"][key]
        if not (max_depth == '6000'):
            alert = True
            probe_restrctions[probe] = max_depth
            probe_restrctions_string = probe_restrctions_string + probe_restrctions_template.format(probe, max_depth)

    if alert:
        message = 'The following probes have restrictions: ' + probe_restrctions_string
        npxc.alert_text(message, state_globals)


    default = 'Retract the probes 30 um to reduce drift? (corbett, sev do we want to do this? its unverified still)'
    key_list = ['depth_retract_bool_string']
    depth_retract_string = npxc.get_from_config(key_list, default)

    default = False
    key_list = ['depth_retract_bool']
    depth_retract_bool = npxc.get_from_config(key_list, default)
    if depth_retract_bool:
        npxc.alert_text(depth_retract_string, state_globals)

@state_transition
def probes_final_depth_exit(state_globals):
    """
    Exit function for state insertion_photodocumentation
    """
    pass


@state_transition
def probes_final_depth_input(state_globals):
    """
    Input test function for state probes_final_depth
    """
    # create a probes_final_depth timestamp (YYYYMMDDHHMMSS)
    print("in probes_final_depth")
    npxc.set_open_ephys_name(state_globals)
    state_globals["external"]["ProbeInsertionCompleteTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    print(f'ProbeInsertionCompleteTime:{state_globals["external"]["ProbeInsertionCompleteTime"]}')
    state_globals["external"][
        "local_log"
    ] = f'ProbeInsertionCompleteTime:{state_globals["external"]["ProbeInsertionCompleteTime"]}'
    if "retake_image_3" in state_globals["external"] and state_globals["external"]["retake_image_3"]:
        print(">>> retake image!")
        # state_globals['external']['next_state'] = 'check_data_dirs'
        state_globals["external"].pop("retake_image_3", None)
    else:
        print(">>> probes_final_depth!")
        # state_globals['external']['next_state'] = 'photodoc_setup4'

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    state_globals["resources"]["final_depth_timer_start"] = datetime.datetime.now()
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def photodoc_setup4_input(state_globals):
    """

    """
    if state_globals["external"]["dummy_mode"]:
        surface_4_left_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image4-left.png"),
        )
        surface_4_right_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image4-right.png"),
        )
        surface_4_local_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image4-left.png"),
        )
    else:
        surface_4_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image4-left.png'
        surface_4_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image4-right.png'
        surface_4_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image4-left.png'

    surface_4_left_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image4-left.png"),
    )
    surface_4_right_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image4-right.png"),
    )
    print(">>>>>>> post_insertion")
    print(f"surface_4_left_path:{surface_4_left_path}")
    print(f"surface_4_right_path:{surface_4_right_path}")
    print(f"surface_4_local_path:{surface_4_local_path}")
    print("<<<<<<<")

    state_globals["external"][
        "surface_4_left_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image4-left.png'
    state_globals["external"][
        "surface_4_right_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image4-right.png'

    try:
        proxy = state_globals["component_proxies"]["Cam3d"]
        try:
            npxc.mvr_capture(state_globals,surface_4_left_path)  # will be replaced by the real call to cam3d
            npxc.mvr_capture(state_globals,surface_4_right_path)  # will be replaced by the real call to cam3d
            state_globals["external"]["status_message"] = "success"
        except Exception as e:
            print(f"Cam3d take photo failure:{e}!")
            state_globals["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state_globals["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        print(f"Cam3d proxy failure:{e}!")
        state_globals["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state_globals["external"]["component_status"]["Cam3d"] = False

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
        state_globals["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state_globals["external"]["local_log"] = f"surface_3_path:{surface_4_local_path}"
    state_globals["external"]["surface_4_file_location"] = surface_4_local_path  # for displaying in the GUI
    if not(os.path.exists(surface_4_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_4_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals["external"]["surface_4_left_local_file_location"] = surface_4_left_local_path
    state_globals["external"]["surface_4_right_local_file_location"] = surface_4_right_local_path

    # state_globals['external']['next_state'] = 'insertion_photodocumentation'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def insertion_photodocumentation_input(state_globals):
    """
    Input test function for state insertion_photodocumentation
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if "bleeding_evident" in state_globals["external"] and state_globals["external"]["bleeding_evident"]:
        state_globals["external"]["next_state"] = "pre_experiment_bleeding_severity"
    elif "no_bleeding_evident" in state_globals["external"] and state_globals["external"]["no_bleeding_evident"]:
        state_globals["external"]["next_state"] = "check_data_dirs"
    elif "retake_image_4" in state_globals["external"] and state_globals["external"]["retake_image_4"]:
        state_globals["external"]["next_state"] = "photodoc_setup4"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def pre_experiment_bleeding_severity_enter(state_globals):
    """
    Entry function for bleeding_severity
    """
    state_globals["external"]["pre_probe_a_bleeding_severity"] = state_globals["external"][
        "pre_probe_b_bleeding_severity"
    ] = state_globals["external"]["pre_probe_c_bleeding_severity"] = state_globals["external"][
        "pre_probe_d_bleeding_severity"
    ] = state_globals[
        "external"
    ][
        "pre_probe_e_bleeding_severity"
    ] = state_globals[
        "external"
    ][
        "pre_probe_f_bleeding_severity"
    ] = 0


@state_transition
def pre_experiment_bleeding_severity_input(state_globals):
    """
    Input function for bleeding severity
    """

    if "pre_probe_a_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_a_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeA"
        ] = 5  # state_globals['external']['pre_probe_a_bleeding_severity']

    if "pre_probe_b_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_b_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeB"
        ] = 5  # state_globals['external']['pre_probe_b_bleeding_severity']

    if "pre_probe_c_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_c_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeC"
        ] = 5  # state_globals['external']['pre_probe_c_bleeding_severity']

    if "pre_probe_d_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_d_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeD"
        ] = 5  # state_globals['external']['pre_probe_d_bleeding_severity']

    if "pre_probe_e_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_e_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeE"
        ] = 5  # state_globals['external']['pre_probe_e_bleeding_severity']

    if "pre_probe_f_bleeding" in state_globals["external"] and state_globals["external"]["pre_probe_f_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnInsertion"][
            "ProbeF"
        ] = 5  # state_globals['external']['pre_probe_f_bleeding_severity']

    # state_globals['external']['next_state'] = 'bleeding_abort_confirm'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def bleeding_abort_confirm_input(state_globals):
    """
    Input test function for state initialize
    """
    print(">> bleeding abort_confirm_input <<")

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    if (
        "bleeding_abort_experiment" in state_globals["external"]
        and state_globals["external"]["bleeding_abort_experiment"]
    ):
        print("&& bleeding abort_experiment")
        state_globals["external"]["next_state"] = "end_experiment_photodocumentation"
    elif "bleeding_abort_cancel" in state_globals["external"] and state_globals["external"]["bleeding_abort_cancel"]:
        print("&& bleeding abort_cancel")
        state_globals["external"]["next_state"] = "check_data_dirs"
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False


@state_transition
def bleeding_abort_confirm_revert(state_globals):
    print("doing bleeding_abort_confirm_revert stuff")


def get_exp_wait_time(state_globals):
    defaults = 300
    key_list = ['final_depth_timer_s']
    wait_time = npxc.get_from_config(key_list, defaults)
    return wait_time

@state_transition
def pre_stimulus_wait_enter(state_globals):
    wait_time = get_exp_wait_time(state_globals)
    npxc.settle_timer_enter(state_globals, wait_time)


@state_transition
def pre_stimulus_wait_input(state_globals):
    """
    Input test function for state pre_stimulus_wait
    """
    # before transiting to the next state, get the listing for camstim files on the stim computer
    # recreate the proxy
    stim_files = []

    state_globals['external']['next_state'] = 'prime_lickspout'

    wait_time = get_exp_wait_time(state_globals)
    npxc.settle_timer_enter(state_globals, wait_time)
    if state_globals["external"].get("override_settle_timer", False):
        return  # TODO: make the checkbox optional

    if float(state_globals['external']['settle_time_remaining_num'])>0:  # total_seconds < npxc.config['final_depth_timer_s']:
        message = 'The settle time has not elapsed! Please wait until the state timer matches the remaining time'
        fail_state(message, state_globals)
        return

    # state_globals['external']['next_state'] = 'check_data_dirs'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def move_lickspout_to_mouse_offset_enter(state_globals):
    try:
        print('Attempting to send mouse ID to mouse director')
        mouse_director_proxy.set_mouse_id(state_globals["external"]["mouse_id"])
    except Exception as E:
        alert_string = f'Failed to send mouse ID to mouse director'
        npxc.alert_text(alert_string, state_globals)
    try:
        print('Attempting to send userID to mouse director')
        mouse_director_proxy.set_user_id(state_globals["external"]["user_id"])
    except Exception as E:
        alert_string = f'Failed to send userID to mouse director'
        npxc.alert_text(alert_string, state_globals)  # Todo put this back


@state_transition
def move_lickspout_to_mouse_offset_input(state_globals):
    ...

    failed = npxc.confirm_components(state_globals)
    if failed:
        # state_globals["external"]["next_state"] = "components_error"
        alert_string = f'The following proxies are not available: {", ".join(failed)}'
        # npxc.alert_text(alert_string, state_globals)#Todo put this back  - I think it was being wierd.
        npxc.overrideable_error_state(state_globals, 'move_lickspout_to_mouse_offset', 'initiate_behavior_experiment',
                                      message=alert_string)


@state_transition
def move_lickspout_to_mouse_offset_exit(state_globals):
    npxc.get_start_experiment_params(state_globals)


@state_transition
def probe_quiescence_enter(state_globals):
    rest_time = npxc.config['final_depth_timer_s']
    stop_time = datetime.datetime.now()
    total_seconds = (stop_time - state_globals["resources"]["final_depth_timer"]).total_seconds()
    wfltk_msgs.state_busy(message=f"Waiting for 5 minute delay time.  Resuming in {300 - total_seconds}")
    if total_seconds < rest_time:
        time.sleep(total_seconds)
        router.write(wfltk_msgs.state_ready(message="ready"))


@state_transition
def probe_quiescence_input(state_globals):
    ...


@state_transition
def check_data_dirs_enter(state_globals):
    print(">> check_data_dirs_enter <<")
    npxc.set_open_ephys_name(state_globals)


@state_transition
def check_data_dirs_input(state_globals):
    #npxc.set_open_ephys_name(state_globals)
    npxc.clear_open_ephys_name(state_globals)
    time.sleep(1)
    npxc.start_ecephys_recording(state_globals)
    time.sleep(3)
    npxc.stop_ecephys_recording(state_globals)
    time.sleep(5)
    try:
        failed = npxc.check_data_drives(state_globals)
    except Exception:
        message = f'There must be a bug in the function that moves the settings file and chakcs the data dirs'
        logging.debug("Data drive failure", exc_info=True)
        npxc.alert_text(message, state_globals)
    if failed:
        state_globals["external"]["next_state"] = "data_dir_error"
        npxc.alert_from_error_dict(state_globals, failed, primary_key=False)
    else:
        state_globals["external"]["next_state"] = "pre_stimulus_wait"
    state_globals["external"]["status_message"] = "success"
    state_globals["external"]["transition_result"] = True


@state_transition
def data_dir_error_input(state_globals):
    print(">> data_dir_error_input <<")

    if "check_dirs_retry" in state_globals["external"] and state_globals["external"]["check_dirs_retry"]:
        print("go back to check data dirs")
        state_globals["external"]["next_state"] = "check_data_dirs"
        state_globals["external"].pop("check_dirs_retry", None)
    else:
        state_globals["external"]["next_state"] = "pre_stimulus_wait"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def select_stimulus_input(state_globals):
    """
    Input test function for state select_stimulus
    """
    print(f'stim file selected:{state_globals["external"]["stimulus_selected"]}')
    state_globals["external"]["local_log"] = f'stim file selected:{state_globals["external"]["stimulus_selected"]}'

    # state_globals['external']['next_state'] = 'initiate_experiment'
    state_globals["external"]["status_message"] = "success"
    state_globals["external"]["transition_result"] = True
    npxc.set_open_ephys_name(state_globals)
    npxc.save_platform_json(state_globals, manifest=False)

    # failed = component_check(state_globals)
    # if failed:
    #    fail_state(f'The following proxies are not available: {", ".join(failed)}', state_globals)
    #    return


###################



@state_transition
def initiate_behavior_experiment_input(state_globals):
    """
    Input test function for state initiate_experiment
    """

    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    npxc.start_common_experiment_monitoring(state_globals)
    wait_time = npxc.get_from_config(['experiment_stream_check_wait_time'], default=90)
    failed = npxc.check_data_stream_size(state_globals, wait_time=wait_time)
    if failed:
        streams = [key.split('_')[0] for key in list(failed.keys())]
        message = f'STREAMS TO CHECK: {", ".join(streams).upper()}'
        npxc.alert_text(message, state_globals)
        message = npxc.streams_message
        npxc.alert_text(message, state_globals)
        fail_message_1 = npxc.alert_from_error_dict(state_globals, failed, primary_key=None)
        # fail_message_1 = f'The following data streams are not recording: {", ".join(failed)}'
        # npxc.alert_text(fail_message_1,state_globals)
        state_globals['external']['next_state'] = 'streams_error_state'
        # fail_state(f'The following data streams are not recording: {", ".join(failed)}', state_globals)
        # return
    else:
        state_globals['external']['next_state'] = 'experiment_running_timer'
        initiate_behavior_stimulus_input(state_globals)
    # state_globals['external']['clear_sticky'] = True
    # state_globals['external']['next_state'] = 'experiment_running_timer'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def initiate_behavior_stimulus_input(state_globals):
    """
    Input test function for state initiate_experiment
    """
    message = npxc.initiate_behavior_stimulus_input(state_globals)
    npxc.delete_dummy_recording(state_globals)
    if message:
        return
    else:
        do_second_stream_check = npxc.get_from_config(['do_second_stream_check'], default=False)
        if do_second_stream_check:
            initial_wait = npxc.get_from_config(['wait_time_before_checking_streams'], default=0)
            time.sleep(initial_wait)
            npxc.establish_data_stream_size(state_globals)
            wait_time = npxc.get_from_config(['experiment_2_stream_check_wait_time'], default=70)
            failed = npxc.check_data_stream_size(state_globals, wait_time=wait_time)
            if failed:
                message = npxc.streams_message

                npxc.alert_text(message, state_globals)
                fail_message_1 = npxc.alert_from_error_dict(state_globals, failed, primary_key=False)


@state_transition
def overrideable_error_state_input(state_globals):
    npxc.overrideable_error_state_input(state_globals)


@state_transition
def overrideable_error_state_exit(state_globals):
    npxc.overrideable_error_state_exit(state_globals)



@state_transition
def initiate_experiment_input(state_globals):
    """
    Input test function for state initiate_experiment
    """
    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    npxc.start_common_experiment_monitoring(state_globals)
    npxc.start_stim(state_globals)
    npxc.save_platform_json(state_globals, manifest=False)
    # state_globals['external']['clear_sticky'] = True
    # state_globals['external']['next_state'] = 'experiment_running_timer'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


def get_exp_message_1(state_globals):
    message_1 = ''
    if 'day1' in state_globals['external']['full_session_type'].lower():
        defaults = ("Please copy the surgery images from to the data directory\n"
            "- Then rename them to XXXXXXXXXX_XXXXXX_XXXXXXXX.surgeryImage1.jpg and "
            "XXXXXXXXXX_XXXXXX_XXXXXXXX.surgeryImage2.jpg")
        key_list = ['session_params', '2_days_before_experiment', 'message_1']
        message_1 = npxc.get_from_config(key_list, defaults)

    if message_1:
        npxc.alert_text(message_1, state_globals)
    return message_1

@state_transition
def experiment_running_timer_enter(state_globals):
    """
    Entry function for state experiment_running_timer
    """
    print("in experiment running enter")


@state_transition
def monitor_experiment_input(state_globals):
    npxc.monitor_experiment_input(state_globals)



@state_transition
def end_experiment_enter(state_globals):
    pass


@state_transition
def end_experiment_input(state_globals):
    """
    Input test function for state end_experiment
    """

    # get an experiment end time
    state_globals["external"]["ExperimentCompleteTime"] = dt.now().strftime("%Y%m%d%H%M%S")
    state_globals["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state_globals["external"]["ExperimentCompleteTime"]}'

    # end the stim process
    # opto_stim_file_path = os.path.join(
    #    state_globals["external"]["local_lims_location"], state_globals["external"]["session_name"] + ".opto.pkl"
    # )
    # visual_stim_file_path = os.path.join(
    #    state_globals["external"]["local_lims_location"], state_globals["external"]["session_name"] + ".stim.pkl"
    # )

    # state_globals["external"]["opto_stim_file_name"] = f'{state_globals["external"]["session_name"]}.opto.pkl'
    # state_globals["external"]["visual_stim_file_name"] = f'{state_globals["external"]["session_name"]}.stim.pkl'

    # state_globals["external"]["opto_stim_file_location"] = opto_stim_file_path
    # state_globals["external"]["visual_stim_file_location"] = visual_stim_file_path

    # print(
    #    f'mapped_lims_location:{state_globals["external"]["mapped_lims_location"]}, opto file name:{state_globals["external"]["opto_stim_file_name"]}'
    # )

    # recreate the proxy

    # stop the open ephys process
    # npxc.stop_ecephys_recording(state_globals)

    # stop the VideoMon process

    time.sleep(3)
    npxc.stop_common_experiment_monitoring(state_globals)
    # recreate the proxy

    # state_globals['external']['next_state'] = 'remove_probes_start'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"

    state_globals["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state_globals["external"]["ExperimentCompleteTime"]}'
    state_globals["external"]["transition_result"] = True
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def end_experiment_photodocumentation_input(state_globals):
    """
    Input test function for state end_experiment_photodocumentation
    """
    surface_5_left_path = 'undefined'
    if state_globals["external"]["dummy_mode"]:
        surface_6_left_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image5-left.png"),
        )
        surface_5_right_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image5-right.png"),
        )
        surface_5_local_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image5-left.png"),
        )
    else:
        surface_5_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image5-left.png'
        surface_5_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image5-right.png'
        surface_5_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image5-left.png'

    surface_5_left_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image5-left.png"),
    )
    surface_5_right_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image5-right.png"),
    )
    print(">>>>>>> post_experiment")
    print(f"surface_5_left_path:{surface_5_left_path}")
    print(f"surface_5_right_path:{surface_5_right_path}")
    print(f"surface_5_local_path:{surface_5_local_path}")
    print("<<<<<<<")

    state_globals["external"][
        "surface_5_left_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image5-left.png'
    state_globals["external"][
        "surface_5_right_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image5-right.png'

    try:
        proxy = state_globals["component_proxies"]["Cam3d"]
        try:
            print(f"taking surface 5 left:{surface_5_left_path}")
            result = npxc.mvr_capture(state_globals,surface_5_left_path)
            print(f"taking surface 5 right:{surface_5_right_path}")
            result = npxc.mvr_capture(state_globals,surface_5_right_path)
            state_globals["external"]["status_message"] = "success"
        except Exception as e:
            state_globals["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state_globals["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        state_globals["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state_globals["external"]["component_status"]["Cam3d"] = False

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
        state_globals["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state_globals["external"]["local_log"] = f"surface_5_path:{surface_5_local_path}"
    state_globals["external"]["surface_5_file_location"] = surface_5_local_path
    if not(os.path.exists(surface_5_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_5_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals["external"]["surface_5_left_local_file_location"] = surface_5_left_local_path
    state_globals["external"]["surface_5_right_local_file_location"] = surface_5_right_local_path

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def remove_probes_start_input(state_globals):
    """
    Input test function for state remove_probes_start
    """
    print(">>>remove_probes_start_input<<<")
    if "retake_image_5" in state_globals["external"] and state_globals["external"]["retake_image_5"]:
        state_globals["external"]["next_state"] = "end_experiment_photodocumentation"
        state_globals["external"].pop("retake_image_5", None)
    else:
        state_globals["external"]["next_state"] = "remove_probes_end"

    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def remove_probes_end_input(state_globals):
    """
    Input test function for state remove_probes_end
    """
    # state_globals['external']['next_state'] = 'post_removal_photodocumentation'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def post_removal_photodocumentation_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    if state_globals["external"]["dummy_mode"]:
        surface_6_left_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image6-left.png"),
        )
        surface_6_right_path = os.path.join(
            state_globals["external"]["mapped_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image6-right.png"),
        )
        surface_6_local_path = os.path.join(
            state_globals["external"]["local_lims_location"],
            (state_globals["external"]["session_name"] + "_surface-image6-left.png"),
        )
    else:
        surface_6_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image6-left.png'
        surface_6_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image6-right.png'
        surface_6_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image6-left.png'

    surface_6_left_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image6-left.png"),
    )
    surface_6_right_local_path = os.path.join(
        state_globals["external"]["local_lims_location"],
        (state_globals["external"]["session_name"] + "_surface-image6-right.png"),
    )
    print(">>>>>>> post_removal")
    print(f"surface_6_left_path:{surface_6_left_path}")
    print(f"surface_6_right_path:{surface_6_right_path}")
    print(f"surface_6_local_path:{surface_6_local_path}")
    print("<<<<<<<")

    state_globals["external"][
        "surface_6_left_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image6-left.png'
    state_globals["external"][
        "surface_6_right_name"
    ] = f'{state_globals["external"]["session_name"]}_surface-image6-right.png'

    try:
        proxy = state_globals["component_proxies"]["Cam3d"]
        try:
            result = npxc.mvr_capture(state_globals,surface_6_left_path)
            result = npxc.mvr_capture(state_globals,surface_6_right_path)
            state_globals["external"]["status_message"] = "success"
        except Exception as e:
            state_globals["external"]["status_message"] = f"Cam3d take photo failure:{e}"
            state_globals["external"]["component_status"]["Cam3d"] = False
    except Exception as e:
        state_globals["external"]["status_message"] = f"Cam3d proxy failure:{e}"
        state_globals["external"]["component_status"]["Cam3d"] = False

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
        state_globals["external"]["alert"] = {
            "msg_text": image_error_message,
            "severity": "Critical",
            "informative_text": "Check Cam3d Viewer, and restart if necessary.  Retake Image",
        }

    state_globals["external"]["local_log"] = f"surface_6_path:{surface_6_local_path}"
    state_globals["external"]["surface_6_file_location"] = surface_6_local_path
    if not(os.path.exists(surface_6_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_6_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals["external"]["surface_6_left_local_file_location"] = surface_6_left_local_path
    state_globals["external"]["surface_6_right_local_file_location"] = surface_6_right_local_path
    # state_globals['external']['next_state'] = 'post_removal_image'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def post_removal_image_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"

    if "post_bleeding_evident" in state_globals["external"] and state_globals["external"]["post_bleeding_evident"]:
        state_globals["external"]["next_state"] = "post_experiment_bleeding_severity"
    elif (
        "no_post_bleeding_evident" in state_globals["external"]
        and state_globals["external"]["no_post_bleeding_evident"]
    ):
        state_globals["external"]["next_state"] = "remove_mouse_and_move_files2"
    elif "retake_image_6" in state_globals["external"] and state_globals["external"]["retake_image_6"]:
        state_globals["external"]["next_state"] = "post_removal_photodocumentation"
        state_globals["external"].pop("retake_image_6", None)
    else:
        state_globals["external"]["status_message"] = f"No valid inputs"
        state_globals["external"]["transition_result"] = False
    npxc.save_platform_json(state_globals, manifest=False)


@state_transition
def post_removal_image_exit(state_globals):
    npxc.reset_open_ephys(state_globals)


@state_transition
def post_experiment_bleeding_severity_exit(state_globals):
    npxc.reset_open_ephys(state_globals)


@state_transition
def post_experiment_bleeding_severity_enter(state_globals):
    """
    Entry function for bleeding_severity
    """
    state_globals["external"]["post_probe_a_bleeding_severity"] = state_globals["external"][
        "post_probe_b_bleeding_severity"
    ] = state_globals["external"]["post_probe_c_bleeding_severity"] = state_globals["external"][
        "post_probe_d_bleeding_severity"
    ] = state_globals[
        "external"
    ][
        "post_probe_e_bleeding_severity"
    ] = state_globals[
        "external"
    ][
        "post_probe_f_bleeding_severity"
    ] = 0


@state_transition
def post_experiment_bleeding_severity_input(state_globals):
    """
    Input function for bleeding severity
    """
    if "post_probe_a_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_a_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeA"
        ] = 5  # state_globals['external']['post_probe_a_bleeding_severity']

    if "post_probe_b_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_b_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeB"
        ] = 5  # state_globals['external']['post_probe_b_bleeding_severity']

    if "post_probe_c_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_c_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeC"
        ] = 5  # state_globals['external']['post_probe_c_bleeding_severity']

    if "post_probe_d_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_d_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeD"
        ] = 5  # state_globals['external']['post_probe_d_bleeding_severity']

    if "post_probe_e_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_e_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeE"
        ] = 5  # state_globals['external']['post_probe_e_bleeding_severity']

    if "post_probe_f_bleeding" in state_globals["external"] and state_globals["external"]["post_probe_f_bleeding"]:
        state_globals["external"]["ExperimentNotes"]["BleedingOnRemoval"][
            "ProbeF"
        ] = 5  # state_globals['external']['post_probe_f_bleeding_severity']

    # state_globals['external']['next_state'] = 'remove_mouse_and_move_files2'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def remove_mouse_and_move_files2_input(state_globals):
    message = npxc.remove_mouse_input(state_globals)
    if message:
        fail_state(message, state_globals)
    npxc.save_platform_json(state_globals, manifest=False)
    if 'day2' in state_globals['external']['full_session_type'].lower():
        message = 'Please mark the cap so LAS knows this mouse is safe to perfuse'
        message = npxc.get_from_config(['cap_message'], default=message)
        npxc.alert_text(message, state_globals)

@state_transition
def water_mouse_enter(state_globals):
    print('water_mouse_enter')
    npxc.water_mouse_enter(state_globals)

@state_transition
def water_mouse_input(state_globals):
    print('water_mouse_input')
    npxc.water_mouse_input(state_globals)

@state_transition
def water_mouse_exit(state_globals):
    print('water_mouse_exit')
    npxc.water_mouse_exit(state_globals)


@state_transition
def check_files1_input(state_globals):
    print('>> check_files1_input <<')
    session_type = state_globals['external']['full_session_type']
    pkl_keyword = 'behavior-'
    behavior_pkl_path = npxc.get_pkl_path(pkl_keyword, state_globals)
    npxc.overwrite_foraging_id(behavior_pkl_path, session_type, state_globals)
    checkpoint = 1
    npxc.videomon_copy_wrapup(state_globals)
    missing_files = {}
    try:
        missing_files = npxc.check_files_input(state_globals, session_type, checkpoint)
        print(f'Missing files is {missing_files}')
    except Exception as E:
        message = (f'Error checking files: see the prompt for more details')
        traceback.print_tb(E.__traceback__)
        npxc.alert_text(message, state_globals)

    try:
        data_missing = {}
        if not (state_globals['external']['PXI']):
            for probe in state_globals['external']['probe_list']:
                if state_globals['external'][f'probe_{probe}_surface']:
                    probeDir = f'{state_globals["openephys_drives"][probe]}/{state_globals["external"]["session_name"]}_probe{probe}'
                    if not (os.path.isdir(probeDir)):
                        message = 'Cannot find data directory for ' + probe
                        data_missing[probe] = message
                        print(message)
        else:
            try:
                npxc.reset_open_ephys(state_globals)
            except Exception as E:
                message = f'Failed to reset open ephys'
                key = f'open_ephys_reset'
                data_missing[key] = message
            print('attempting to rename probdirs')
            for slot, drive in state_globals['external']['PXI'].items():
                print(f'for slot {slot}')
                a_probe = state_globals['external']['reverse_mapping'][slot][
                    0]  # list(state_globals['external']['probe_list'].keys())[0]
                probeDir, computer = npxc.get_probeDir(state_globals, slot, drive)
                # print('computer:'+ computer + ', tail:'+x)
                new_dir, computer = npxc.get_final_probeDir(state_globals, a_probe)
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
        npxc.alert_text(message, state_globals)
    missing_files.update(data_missing)
    if missing_files:
        # state_globals['external']['next_state'] = 'files_error1'
        npxc.alert_from_error_dict(state_globals, missing_files)
    validation_type = state_globals['external']['full_session_type'] + '_lims_ready'
    npxc.run_validation(state_globals, validation_type)
    failed = npxc.get_validation_results(state_globals, validation_type)
    state_globals['external']['next_state'] = 'create_manifest_and_platform_json'
    if failed:
        foraging_failed = False
        for key, message in failed.items():
            if 'foraging' in key or 'foraging' in message:
                foraging_failed = True
        if foraging_failed:
            npxc.overrideable_error_state(state_globals, 'check_files1', message=message)
            state_globals['external']['next_state'] = 'foraging_ID_error'
        else:
            state_globals['external']['next_state'] = 'files_error1'
            npxc.alert_from_error_dict(state_globals, failed)
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


@state_transition
def files_error1_input(state_globals):
    print(">> files_error_input <<")
    if "check_files_retry" in state_globals["external"] and state_globals["external"]["check_files_retry"]:
        state_globals["external"].pop("check_files_retry", None)
        check_files1_input(state_globals)
        #print("go back to initialize")
        #state_globals["external"]["next_state"] = "check_files1"
    # elif "move_files_retry" in state_globals["external"] and state_globals["external"]["move_files_retry"]:
    #    print("go back to initialize")
    #    state_globals["external"]["next_state"] = "remove_mouse_and_move_files2"
    #    state_globals["external"].pop("move_files_retry", None)
    else:
        state_globals["external"]["next_state"] = "create_manifest_and_platform_json"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def foraging_ID_error_input(state_globals):
    #print('foraging ID:' + state_globals['external']['foraging_id'])
    #print('stimulus_name:' + state_globals['external']['stimulus_name'])
    #print('script_name:' + state_globals['external']['script_name'])
    state_globals['external']['retry_state'] = 'water_mouse'
    state_globals['external']['override_state'] = 'check_files1'
    #npxc.overrideable_error_state_input(state_globals)


@state_transition
def foraging_ID_error_exit(state_globals):
    #npxc.overrideable_error_state_exit(state_globals)
    pass


@state_transition
def create_manifest_and_platform_json_enter(state_globals):
    """
    Input test function for state create_manifest_and_platform_json_and_sync_report
    """
    # LIMS Stuff will wait until I get the LIMS Session stuff sorted out with RH
    npxc.save_platform_json(state_globals, manifest=True)
    print("going to workflow complete...")
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def cleanup_enter(state_globals):
    """
    Entry function for state workflow_complete
    """
    print(f">> Cleanup enter!")

    # if aborting, make sure to stop capture and release cameras in Cam3d



@state_transition
def cleanup_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    print(f">> Cleanup Input <<")
    if'stop_streams' in state_globals['external'] and state_globals['external']['stop_streams']:
        npxc.stop_common_experiment_monitoring(state_globals)

    npxc.save_platform_json(state_globals, manifest=False)
    state_globals["external"]["next_state"] = "workflow_complete"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def check_files2_input(state_globals):
    print(">> check_files2_input <<")
    stop = npxc.check_files2(state_globals)
    print(f"stop is {stop}")
    if stop:
        state_globals["external"]["next_state"] = "files_error2"
    else:
        state_globals["external"]["next_state"] = "copy_files_to_network"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def check_files2_revert(state_globals):
    print(">> check_files2_revert <<")
    message = 'Reverting to this state will generate a second platform_json. You should delete the old one'
    npxc.alert_text(message, state_globals)


@state_transition
def files_error2_input(state_globals):
    print(">> files_error2_input <<")
    if "check_files2_retry" in state_globals["external"] and state_globals["external"]["check_files2_retry"]:
        print("go back to initialize")
        state_globals["external"]["next_state"] = "check_files2"
        state_globals["external"].pop("check_files2_retry", None)
    elif "create_files2_retry" in state_globals["external"] and state_globals["external"]["create_files2_retry"]:
        print("go back to initialize")
        state_globals["external"]["next_state"] = "create_manifest_and_platform_json"
        state_globals["external"].pop("create_files2_retry", None)
    else:
        state_globals["external"]["next_state"] = "copy_files_to_network"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def initiate_data_processing_enter(state_globals):
    """
    Entry function for state workflow_complete
    """
    print("enter workflow complete")
    logging.info("Neuropixels behavior workflow complete", extra={"weblog": True})


@state_transition
def initiate_data_processing_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    npxc.stop_ecephys_acquisition(state_globals)
    print(">> initiate_data_processing_input <<")
    initiated = False
    try:
        initiated = npxc.initiate_data_processing(state_globals)
    except Exception as E:
        message = f'Error initiating data processing: {E}'
        npxc.alert_text(message, state_globals)
    if initiated:
        print("initiated all data processing sucessfully")
        state_globals["external"]["next_state"] = "copy_files_to_network"
        state_globals["external"].pop("data_retry", None)
    else:
        state_globals["external"]["next_state"] = "data_error"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def data_error_input(state_globals):
    print(">> data_error_input <<")
    if "data_retry" in state_globals["external"] and state_globals["external"]["data_retry"]:
        print("go back to initialize")
        state_globals["external"]["next_state"] = "initiate_data_processing"
        state_globals["external"].pop("data_retry", None)
    else:
        state_globals["external"]["next_state"] = "copy_files_to_network"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def copy_files_to_network_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    print(">> copy_files_to_network_input <<")
    npxc.save_notes(state_globals)
    npxc.backup_files(state_globals)


@state_transition
def ready_to_check_network_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    # TODO This will be broken
    print(">> ready_to_check_network_input <<")
    npxc.save_notes(state_globals)
    session_type = state_globals['external']['full_session_type']
    try:
        if npxc.global_processes['network_backup_process'].poll() is None:
            message = 'Files are not finished copying to the network. Wait a bit and try again'
            fail_state(message, state_globals)
    except Exception as E:
        npxc.alert_text('Failed to test if network backup is finished'. state_globals)
    failed = {}
    failed = npxc.check_files_network(state_globals, session_type, {1, 2})
    print(f"missing_files network is {failed}")
    validation_type = state_globals['external']['full_session_type'] + '_local_qc'
    npxc.run_validation(state_globals, validation_type)
    failed.update(npxc.get_validation_results(state_globals, validation_type))
    state_globals['external']['next_state'] = 'create_manifest_and_platform_json'
    if failed:
        npxc.alert_from_error_dict(state_globals, failed)
        state_globals["external"]["next_state"] = "network_backup_error"
    else:
        state_globals["external"]["next_state"] = "workflow_complete"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def network_backup_error_input(state_globals):
    print(">> files_error2_input <<")
    if "copy_retry" in state_globals["external"] and state_globals["external"]["copy_retry"]:
        print("go back to initialize")
        state_globals["external"]["next_state"] = "copy_files_to_network"
        state_globals["external"].pop("copy_retry", None)
    elif "data_retry" in state_globals["external"] and state_globals["external"]["data_retry"]:
        print("go back to initialize")
        state_globals["external"]["next_state"] = "ready_to_check_network"
        state_globals["external"].pop("data_retry", None)
    else:
        state_globals["external"]["next_state"] = "workflow_complete"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


@state_transition
def workflow_complete_enter(state_globals):
    """
    Entry function for state workflow_complete
    """
    print("enter workflow complete")
    logging.info("Neuropixels behavior workflow complete", extra={"weblog": True})
    state_globals["external"]["workflow_complete_time"] = dt.now().strftime('%Y%m%d%H%M%S')


@state_transition
def workflow_complete_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    print("workflow complete input")

    state_globals["external"]["next_state"] = None
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
