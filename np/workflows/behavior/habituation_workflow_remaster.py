try:
    
    import inspect
    import json
    import logging
    import os
    import socket
    import time
    import webbrowser

    # from physync.report import NeuropixelsReport
        # -*- coding: latin-1 -*-
    from datetime import date as date
    from datetime import datetime as dt
    from datetime import timedelta as timedelta
    from pprint import pformat

    # pdb.set_trace()
    from mpetk.aibsmw.routerio.router import ZMQHandler
    import np.workflows.npxcommon as npxc
    import requests
    from mpetk import limstk
    from mpetk.zro import Proxy
    from np.models.model import (  # It can make sense to have a class to store experiment data.
        Behavior, DynamicRouting)
    from np.services.config import Rig

except Exception as e:
    # import errors aren't printed to console by default
    print(e)
    
# -------------- experiment-specific objects --------------
global experiment
# this should be the first line of code executed
if Rig.ID == "NP.0":
    experiment = npxc.experiment = Behavior()
elif Rig.ID == "NP.1":
    experiment = npxc.experiment = DynamicRouting()

global config
config = npxc.config = npxc.get_config()

# ---------------- Network Service Objects ----------------

router: ZMQHandler = None
camstim: Proxy = None
mouse_director: Proxy = None
sync: Proxy = None


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
            #reload(npxc)
            transition_type = state_transition_function.__name__.split('_')[-1]
            if (transition_type == 'input') and ('msg_text' in state_globals["external"]):
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

# function to interlace the left/right images into one image and save to a path
def interlace(left, right, stereo):
    npxc.interlace(left, right, stereo)

def handle_message(message_id, message, timestamp, io):
    npxc.handle_message(message_id, message, timestamp, io)

# @state_transition
def default_exit(state_globals,label):
    npxc.default_exit(state_globals,label)

# @state_transition
def default_enter(state_globals,label):
    npxc.default_enter(state_globals,label)

# @state_transition
def default_input(state_globals, label):
    npxc.default_input(state_globals, label)


@state_transition
def overrideable_error_state_input(state_globals):
    npxc.overrideable_error_state_input(state_globals)

@state_transition
def overrideable_error_state_exit(state_globals):
    npxc.overrideable_error_state_exit(state_globals)

# @state_transition
def initialize_enter(state_globals):
    
    state_globals["external"]["logo"] = R".\np\images\logo_np_hab.png"

    state_globals['external']['session_type'] = 'behavior_habituation'
    npxc.initialize_enter(state_globals)


def initialize_input(state_globals):
    """
    Input test function for state initialize
    """
    npxc.initialize_input(state_globals)

    global mouse_director
    md_host = npxc.config['components']['MouseDirector']['host']
    md_port = npxc.config['components']['MouseDirector']['port']
    #! should md_host be localhost, not vidmon?
    md_host = 'localhost'
    mouse_director = Proxy(f'{md_host}:{md_port}')  # TODO: config this

    global camstim
    host = npxc.config["components"]["Stim"]["host"]
    port = npxc.config["components"]["Stim"]["port"]
    state_globals['mtrain_text'] =  pformat(npxc.config['MTrainData'])
    camstim = Proxy(f"{host}:{port}", serialization="json")

    global router
    router = state_globals["resources"]["io"]


    beh_mon_str_def = 'Limited adjustment to the wheel and the spout location should be made as necessary. '
    state_globals['external']['behavior_monitoring_string'] = npxc.get_from_config(['behavior_monitoring_string_hab'], default=beh_mon_str_def)


    defaults = ['first', 'normal', '2_days_before_experiment', '1_day_before_experiment']
    key_list = ['Habituaiton_Sessions']
    hab_sessions = npxc.get_from_config(key_list, defaults)
    #hab_sessions = []
    #try:
    #    hab_sessions = npxc.config['Habituaiton_Sessions']
    #except Exception as E:
    #    message = f'Failed to find habitaution sessions in  config. Using default instead'
    #    print(message)
    #    #npxc.alert_text(message, state_globals)
    #    hab_sessions = ['first', 'normal', '2_days_before_experiment', '1_day_before_expeirment']

    #session_list = [hab_session.lower() for hab_session in hab_sessions]
    state_globals["external"]["session_type_option_string"] = ', '.join(hab_sessions)
    state_globals["external"]["session_types_options"] = hab_sessions

    logging.info(
        f'Neuropixel workflow running on {state_globals["external"]["rig_id"]} '
        f'by user:{state_globals["external"]["user_id"]}',
        extra={"weblog": True},
    )

    result = limstk.user_details(state_globals["external"]["user_id"])
    failed = npxc.confirm_components(state_globals)

    state_globals["external"]["next_state"] = "scan_mouse_id"
    if failed:
        state_globals["external"]["next_state"] = "components_error"
        alert_string = f'The following proxies are not available: {", ".join(failed)}'
        npxc.alert_text(alert_string, state_globals)#Todo put this back  - I think it was being wierd.

    #else:
    #    state_globals["external"]["next_state"] = "components_error"
    try:
        if result != -1:
            state_globals["external"]["lims_user_id"] = result[0]["id"]
            state_globals["external"]["status_message"] = "success"
            state_globals["external"][
                "local_log"
            ] = f'LIMS ID for User:{state_globals["external"]["user_id"]} Found in LIMS'
        else:
            state_globals["external"]["next_state"] = "initialize"
            print('Failed user ID test')
            fail_state(f'No LIMS ID for User:{state_globals["external"]["user_id"]} found in LIMS', state_globals)
    except (KeyError, IndexError):
        print('Failed user ID test')
        fail_state(f'No LIMS ID for User:{state_globals["external"]["user_id"]} found in LIMS', state_globals)
        state_globals["external"]["next_state"] = "initialize"


    # state_globals['external']['next_state'] = 'probes_final_depth'  # NOTE: REMOVE this




def components_error_input(state_globals):
    npxc.components_error_input(state_globals, 'scan_mouse_id')


def mtrain_change_stage_enter(state):

    state["external"]["current_regimen"] = npxc.mtrain.regimen['name']
    state["external"]["current_stage"] = npxc.mtrain.stage['name'].title()
    state["external"]["new_stage"] = npxc.mtrain.stage['name'].title()

    available_stages = sorted([stage['name'].title() for stage in npxc.mtrain.stages])

    state["external"]["available_stages"] = npxc.circshift_to_item(available_stages, state['external']['current_stage'])

    # state['resources']['io'].write(npxc.messages.state_ready(message="ready"))


def mtrain_change_stage_input(state):

    print(state["external"]["next_state"])
    new_stage = state["external"]["new_stage"]
    confirm_stage = state['external']['confirm_stage']
    change_regimen = state['external']['change_regimen']

    if confirm_stage and new_stage.lower() != npxc.mtrain.stage['name'].lower():
        npxc.mtrain.stage = new_stage
        state["external"]["next_state"] = 'mtrain_stage'

    elif change_regimen:
        state["external"]["next_state"] = 'mtrain_regimen_1'


def mtrain_change_regimen_1_enter(state):
    # current regimen is already set in prev screen, but we set it again here anyway
    state["external"]["current_regimen"] = npxc.mtrain.regimen['name']

    available_regimens = sorted([regimen for regimen in npxc.mtrain.all_regimens().values()])
    state["external"]["available_regimens"] = npxc.circshift_to_item(available_regimens,
                                                                     state['external']['current_regimen'])


def mtrain_change_regimen_1_input(state):
    new_regimen = state["external"].get("new_regimen", None)
    confirm_regimen = state['external']['confirm_regimen']
    cancel_regimen_select = state['external']['cancel_regimen_select']

    if cancel_regimen_select:
        # cancel
        state["external"]["next_state"] = 'mtrain_stage'
    elif confirm_regimen and new_regimen:
        # don't set anything yet - we need a corresponding stage for the new regimen
        state["external"]["next_state"] = 'mtrain_regimen_2'
    elif confirm_regimen and not new_regimen:
        # apparently no change is requested - go back to the stage selection
        state["external"]["next_state"] = 'mtrain_stage'


def mtrain_change_regimen_2_enter(state):
    # get the stages available for the new regimen:
    new_regimen_dict = [
        regimen for regimen in npxc.mtrain.get_all("regimens")
        if regimen['name'].lower() == state["external"]["new_regimen"].lower()
    ][0]
    new_stages = new_regimen_dict['stages']
    # no need to circshift the list below, since there's no concept of "current stage" on a newly selected regimen
    state['external']['available_stages_new_regimen'] = sorted([stage['name'].title() for stage in new_stages])
    state["external"]["new_regimen"] = new_regimen_dict['name']


def mtrain_change_regimen_2_input(state):
    # copy verbatim this line from mtrain_regimen_2_enter
    new_regimen_dict = [
        regimen for regimen in npxc.mtrain.get_all("regimens")
        if regimen['name'].lower() == state["external"]["new_regimen"].lower()
    ][0]

    selected_stage_new_regimen = state["external"].get("selected_stage_new_regimen", None)
    confirm_regimen_and_stage = state['external']['confirm_regimen_and_stage']
    cancel_regimen_select = state['external']['cancel_regimen_select']

    # pdb.set_trace()
    # state['external']['confirm_stage_or_change_regimen']
    #TODO get the next state(s) from brb in wfl/state if possible

    if cancel_regimen_select:
        state["external"]["next_state"] = 'mtrain_stage'

    elif confirm_regimen_and_stage and selected_stage_new_regimen:

        new_stage_dict = [
            stage for stage in new_regimen_dict['stages']
            if stage['name'].lower() == selected_stage_new_regimen.lower()
        ][0]
        npxc.mtrain.set_regimen_and_stage(new_regimen_dict, new_stage_dict)
        state["external"]["next_state"] = 'mtrain_stage'

    elif confirm_regimen_and_stage and not selected_stage_new_regimen:
        # state["external"]["next_state"] = 'run_stimulus'
        # not sure if it's even possible to continue (green arrow) without selecting a stage
        state["external"]["next_state"] = 'mtrain_stage'



def pretest_input(state_globals):
    print('>> pretest_error_input <<')
    npxc.handle_2_choice_button('pretest_failed', 'pretest_error', 'configure_hardware', state_globals)


def pretest_error_input(state_globals):
    print('>> pretest_error_input <<')
    npxc.handle_2_choice_button('pretest_override', 'configure_hardware', 'pretest', state_globals)

def flush_lines_enter(state_globals):
    state_globals['external']['clear_sticky'] = True

def prepare_for_pretest_enter(state_globals):
    npxc.prepare_for_pretest_input(state_globals)



def scan_mouse_id_input(state_globals):
    """
    Input test function for state initialize
    """
    comp_id = os.environ.get('aibs_comp_id', socket.gethostname())
    mouse_id = state_globals["external"]["mouse_id"]
    user_id = state_globals["external"]["user_id"]

    #@Ross do you want this only for experiments? I copied over for hab...
    logging.info(f'MID, {mouse_id}, UID, {user_id}, BID, {comp_id}, Received')

    state_globals["external"]["local_log"] = f'Mouse ID :{mouse_id}'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    state_globals["external"]["clear_sticky"] = True

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
    session_dict = state_globals['external']['hab_sessions']
    session_count = npxc.count_sessions(session_dict, state_globals['external']['mouse_id'])
    guess_exp_day = 'normal'
    if session_count==0:
        guess_exp_day = 'first'
    elif (dt.today().weekday() == 1) and (session_count>3):
        guess_exp_day = '1_day_before_experiment'
    elif (dt.today().weekday() == 0) and (session_count>2):
        guess_exp_day ='2_days_before_experiment'
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




def LIMS_request_enter(state_globals):
    """
    Entry function for state initialize
    """
    state_globals['external']['clear_sticky'] = True
    state_globals['external']['Manual.Project.Code'] = state_globals['external']['Project.Code2']

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



def LIMS_request_revert(state_globals):
    ...

def check_stimulus_enter(state_globals):
    defaults = ['3']
    key_list = ['MTrainData', 'Habituation_Sessions', 'days_to_open_mtrain']
    days_to_open_mtrain = npxc.get_from_config(key_list, defaults)
    days_to_open_mtrain = [str(day_num) for day_num in days_to_open_mtrain]
    if (str(dt.today().weekday()+1) in days_to_open_mtrain) or ('first' in state_globals['external']['full_session_type']):
        message = "It looks like today is a day that requires an mtrain stage change. You should be extra careful to check the mtrain stage."
        npxc.alert_text(message, state_globals)
        url = f'http://mtrain:5000/set_state/{state_globals["external"]["mouse_id"]}'
        webbrowser.open(url)
    session_day = state_globals['external']['entered_experiment_day']
    mtrain_string = None
    try:
        default=None
        key_list = ['MTrainData', 'Habituation_Sessions', str(dt.today().weekday()+1), 'Full_String']
        mtrain_string = npxc.get_from_config(key_list, default)

        
    except Exception as E:
        pass
    if mtrain_string == None:
        key_list = ['MTrainData', 'Habituation_Sessions', 'Habituation', 'Full_String']
        default = 'VisualBehaviorEPHYS_Task1G_v0.0.10/HABITUATION_5_images_G_handoff_ready_3uL_reward '
        mtrain_string = npxc.get_from_config(key_list, default=default)

    state_globals['external']['mtrain_string'] = mtrain_string
    state_globals["external"]["clear_sticky"] = True


def date_string_check_enter(state_globals):
    state_globals['external']['Manual.Date.String'] = state_globals['external']['Auto.Date.String1']


def date_string_check_input(state):
    """
    Input test function for state initialize
    """
    print('>>>date_string_check_input<<<')
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
    state["external"]["local_log"] = f'LIMS pull for isi experiments associated with mouse id:{state["external"]["mouse_id"]} success!'
    """
    Input test function for state initialize
    """
    # lots of LIMS interactions to be added here when service is available
    # find which experiment was selected:
    selected_index = 0
    #TODO - Have it actually select the ISI map that we put our coordinates on?
    #for x in range(len(state["external"]["ISI_experiments"])):
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
    print(session_name_timestamp)
    assert(len(session_name_timestamp) == 8)
    session_name_string = f'{state["external"]["mouse_id"]}_{session_name_timestamp}'
    print('Session name string: '+session_name_string)
    state["external"]["session_name"] = session_name_string
    state["external"]["sessionNameTimestamp"] = session_name_timestamp


    timestamp = dt.now().strftime("%Y%m%d%H%M%S")
    name = f'HAB_{timestamp}_{state["external"]["lims_user_id"]}'
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
    print('Old session name: '+state["external"]["session_name"])
    print('ecephys_session_id: '+state["external"]["ecephys_session_id"])
    state["external"]["session_name"] = f'{ecephys_session_id}_{state["external"]["session_name"]}'
    print('New session name: '+state["external"]["session_name"])

    # create direction for session ID on local drive
    session_name_directory = os.path.join(
        state["external"]["local_lims_head"], state["external"]["session_name"]
    )
    state["external"]["session_name_directory"] = session_name_directory
    local_lims_location = session_name_directory
    print(f"local lims lication: {local_lims_location}")
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
    #     return
    # except Exception:
    #     fail_state('Error setting mouse and session name in SurgeryNotes', state)
    #     state["external"]["component_status"]["Notes"] = False
    #     return
    mapped_lims_location = f"{npxc.config['mapped_lims_location']}/{state['external']['session_name']}"
    state["external"]["mapped_lims_location"] = mapped_lims_location
    state["external"]["local_lims_location"] = local_lims_location
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"

def date_string_check_revert(state_globals):
    ...


def lims_abort_confirm_input(state):
    """
    Input test function for state initialize
    """
    state["external"]["transition_result"] = True
    state["external"]["status_message"] = "success"
    if "lims_abort_experiment" in state["external"] and state["external"]["lims_abort_experiment"]:
        state["external"]["next_state"] = "create_manifest_and_platform_json_and_sync_report"
    elif "lims_abort_cancel" in state["external"] and state["external"]["lims_abort_cancel"]:
        state["external"]["next_state"] = "pull_ISI_data"
    else:
        fail_state('No valid inputs', state)


def lims_abort_confirm_revert(state):
    ...


def check_experiment_day_enter(state_globals):
    defaults = ['3']
    key_list = ['MTrainData', 'Habituation_Sessions', 'days_to_open_mtrain']
    days_to_open_mtrain = npxc.get_from_config(key_list, defaults)
    days_to_open_mtrain = [str(day_num) for day_num in days_to_open_mtrain]
    # if (str(dt.today().weekday()+1) in days_to_open_mtrain) or ('first' in state_globals['external']['full_session_type']):
    #     url = f'http://mtrain:5000'
    #     webbrowser.open(url)

    state_globals["external"]["clear_sticky"] = True


def check_experiment_day_input(state_globals):
    entered_experiment_day = state_globals['external']['entered_experiment_day']
    session_types_options = state_globals['external']['session_types_options']
    if not (entered_experiment_day in session_types_options):
        message = 'Session Type Not Valid: please type exactly as listed'
        fail_state(message, state_globals)
    else:
        state_globals['external']['full_session_type'] = state_globals['external'][
                                                             'session_type'] + '_' + entered_experiment_day.lower()




def configure_hardware_enter(state_globals):
    """
    Entry function for state configure_hardware
    """
    print('>> configure_hardware_enter')

    # good place to test the components turning on/off

    # load the sync config file
    try:
        proxy = state_globals['component_proxies']['Sync']

        try:
            proxy.init()
            #proxy.load_config("C:/ProgramData/AIBS_MPE/sync/last.yml")
            state_globals['external']['status_message'] = 'success'
            state_globals['external']['component_status']["Sync"] = True
        except Exception as e:
            print(f'Sync load config failure:{e}!')
            state_globals['external']['status_message'] = f'Sync load config failure:{e}'
            state_globals['external']['component_status']["Sync"] = False
    except Exception as e:
        print(f'Sync proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Sync proxy failure:{e}'
        state_globals['external']['component_status']["Sync"] = False

    # set the data file path for open ephys



def configure_hardware_input(state_globals):
    """
    Input test function for state configure_hardware
    """
    print('>>> in configure_hardware input')

    # find which experiment was selected:
    #for x in range(len(state_globals["external"]["ISI_experiments"])):
    #    if (state_globals["external"]["ISI_experiments"][x] == state_globals["external"]["isi_experiment_selected"]):
    #        selected_index = x
    #        break

    #selected_targets = state_globals['external']['ISI_targets'][selected_index]
    #print(f'selected_targets:{selected_targets}')
    # build up string of targets to display
    #probe_list = ['A', 'B', 'C', 'D', 'E', 'F']
    #for x in range(len(selected_targets)):
    #    print(f'target_data:{selected_targets[x]}')
    #    state_globals['external'][f'target_{probe_list[x]}_data'] = json.dumps(selected_targets[x])

    #state_globals['external']['next_state'] = 'load_mouse'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'

def load_mouse_enter(state_globals):
    npxc.load_mouse_enter(state_globals)

def load_mouse_input(state_globals):
    """
    Input test function for state load_mouse_headframe
    """
    # create a mouse in headframe timestamp (YYYYMMDDHHMMSS)
    #state_globals['external']['mouse_in_headframe_holder']
    state_globals['external']["HeadFrameEntryTime"] = dt.now().strftime('%Y%m%d%H%M%S')
    state_globals['external']['local_log'] = f'HeadFrameEntryTime:{state_globals["external"]["HeadFrameEntryTime"]}'
    #state_globals['external']['next_state'] = 'lower_probe_cartridge'
    message = npxc.load_mouse_behavior(state_globals)
    if message:
        fail_state(message, state_globals)
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'
    npxc.save_platform_json(state_globals, manifest=False)

    state_globals["resources"]["final_depth_timer_start"] = dt.now()
    if 'day2' in state_globals['external']['full_session_type'].lower():
        npxc.alert_text('Remove extra agarose and ensure adequate silicon oil.', state_globals)


def lower_probe_cartridge_enter(state_globals):
    state_globals['external']['clear_sticky'] = True




def get_hab_wait_time(state_globals):
    wait_time = .1
    if '2_days_before_experiment' in state_globals['external']['full_session_type']:
        defaults = 600
        key_list = ['session_params', '2_days_before_experiment', 'wait_time']
        wait_time = npxc.get_from_config(key_list, defaults)
    elif '1_day_before_experiment' in state_globals['external']['full_session_type']:
        defaults = 900
        key_list = ['session_params', 'day1', 'message_1']
        wait_time = npxc.get_from_config(key_list, defaults)
    return wait_time


def get_hab_message_1(state_globals):
    message_1 = ''
    if '2_days_before_experiment' in state_globals['external']['full_session_type'].lower():
        defaults = ("Please have each probe descend 500um at 100um/min\n"
            "- Then retract to 0")
        key_list = ['session_params', '2_days_before_experiment', 'message_1']
        message_1 = npxc.get_from_config(key_list, defaults)
    elif '1_day_before_experiment' in state_globals['external']['full_session_type'].lower():
        defaults = ("Please tap the coverslip with the probes close to the targets\n"
            "- Take a screenshot of the coordintes \n\n"

            f"- Adjust the zoom to {state_globals['external']['high_zoom_level']}\n\n"
            "- Then retract them to 0 and have each porbe descend 500um at 100um/min\n"
            )
        key_list = ['session_params', '1_day_before_experiment', 'message_1']
        message_1 = npxc.get_from_config(key_list, defaults)
    if message_1:
        npxc.alert_text(message_1, state_globals)
    return message_1

def get_hab_message_2(state_globals):
    message_2 = ''
    if '1_day_before_experiment' in state_globals['external']['full_session_type'].lower():
        defaults = ("- Please run probeLocator on the image from todays tap\n\n"
            "- Please check the perfusions requests for this week\n")
        key_list = ['session_params', '1_day_before_experiment', 'message_2']
        message_2 = npxc.get_from_config(key_list, defaults)
    if message_2:
        npxc.alert_text(message_2, state_globals)
    return message_2

def get_hab_message_3(state_globals):
    message_1 = ''
    session_type = state_globals['external']['full_session_type']
    if ('2_days_before_experiment' in session_type) or ('1_day_before_experiment' in session_type):
        defaults = ("Please retract the probes to Z height 0 um\n")
        key_list = ['session_params', 'day_before_experiment', 'message_3']
        message_1 = npxc.get_from_config(key_list, defaults)
    if message_1:
        npxc.alert_text(message_1, state_globals)
    return message_1


def get_hab_message_4(state_globals):
    message_1 = ''
    if (dt.today().weekday() == 2) or (dt.today().weekday() == 3):
        defaults = ("Please clean the eytracking mirror in preperation for the afternoons\n")
        key_list = ['session_params', 'day_before_experiment', 'message_3']
        message_1 = npxc.get_from_config(key_list, defaults)
    if message_1:
        npxc.alert_text(message_1, state_globals)
    return message_1


def pre_stimulus_wait_enter(state_globals):
    wait_time = get_hab_wait_time(state_globals)
    npxc.settle_timer_enter(state_globals, wait_time)
    #get_hab_message_1(state_globals)
    state_globals["external"]["clear_sticky"] = True


def pre_stimulus_wait_input(state_globals):
    """
    Input test function for state pre_stimulus_wait
    """
    # before transiting to the next state, get the listing for camstim files on the stim computer
    # recreate the proxy
    stim_files = []

    state_globals['external']['next_state'] = 'prime_lickspout'

    wait_time = get_hab_wait_time(state_globals)
    npxc.settle_timer_enter(state_globals, wait_time)

    if state_globals["external"].get("override_settle_timer", False):
        return  # TODO: make the checkbox optional

    if state_globals['external']['settle_time_remaining_num']:  # total_seconds < npxc.config['final_depth_timer_s']:
        message = 'The settle time has not elapsed! Please wait until the state timer matches the remaining time'
        fail_state(message, state_globals)
        return

    # state_globals['external']['next_state'] = 'check_data_dirs'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
    npxc.save_platform_json(state_globals, manifest=False)


def lower_probe_cartridge_input(state_globals):
    """
    Input test function for state lower_probe_cartridge
    """
    # create a probe_cartridge_lower timestamp (YYYYMMDDHHMMSS)
    if state_globals['external']['probe_cartridge_lower']:
        state_globals['external']["CartridgeLowerTime"] = dt.now().strftime('%Y%m%d%H%M%S')
        state_globals['external']['local_log'] = f'CartridgeLowerTime: {state_globals["external"]["CartridgeLowerTime"]}'
    try:
        npxc.rename_mlog(state_globals)
    except Exception as e:
        print(f"Notes rename log failure:{e}!")
    #state_globals['external']['next_state'] = 'brain_surface_focus'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def brain_surface_focus_enter(state_globals):
    state_globals['external']['clear_sticky'] = True
    state_globals['external']['old_low_zoom_level'] = state_globals['external']['low_zoom_level']
    state_globals['external']['low_zoom_level'] = state_globals['external']['high_zoom_level']
    get_hab_message_1(state_globals)


def brain_surface_focus_input(state_globals):
    """
    Input test function for state brain_surface_focus
    """
    # take a photo here from CamViewer3d
    if state_globals['external']['dummy_mode']:
        surface_1_left_path = os.path.join(state_globals['external']['mapped_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-left.png'))
        surface_1_right_path = os.path.join(state_globals['external']['mapped_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-right.png'))
        surface_1_local_path = os.path.join(state_globals['external']['local_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-left.png'))
        surface_1_left_local_path = os.path.join(state_globals['external']['local_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-left.png'))
        surface_1_right_local_path = os.path.join(state_globals['external']['local_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-right.png'))
    else:
        surface_1_left_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-left.png'
        surface_1_right_path = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-right.png'
        surface_1_local_path = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}_surface-image2-left.png'
        surface_1_left_local_path = os.path.join(state_globals['external']['local_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-left.png'))
        surface_1_right_local_path = os.path.join(state_globals['external']['local_lims_location'], (state_globals['external']["session_name"] + '_surface-image2-right.png'))
    try:

        print('>>>>>>> brain_surface_image')
        print(f'surface_1_left_path:{surface_1_left_path}')
        print(f'surface_1_right_path:{surface_1_right_path}')
        print(f'surface_1_local_path:{surface_1_local_path}')
        print('<<<<<<<')

        state_globals['external']['surface_1_left_name'] = f'{state_globals["external"]["session_name"]}_surface-image1-left.png'
        state_globals['external']['surface_1_right_name'] = f'{state_globals["external"]["session_name"]}_surface-image1-right.png'

        try:
            npxc.take_left_snapshot(state_globals,surface_1_left_path)
            npxc.take_right_snapshot(state_globals,surface_1_right_path)


            state_globals['external']['status_message'] = 'success'
            state_globals['external']['local_log'] = f'Surface_1_Path:{surface_1_local_path}'
        except Exception as e:
            print(f'Cam3d take photo failure:{e}!')
            state_globals['external']['status_message'] = f'Cam3d take photo failure:{e}'
            state_globals['external']['component_status']["Cam3d"] = False
    except Exception as e:
        print(f'Cam3d proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Cam3d proxy failure:{e}'
        state_globals['external']['component_status']["Cam3d"] = False

    # check for the image files...make sure they were taken succesfully
    left_image_result = os.path.isfile(surface_1_left_local_path)
    right_image_result = os.path.isfile(surface_1_right_local_path)

    image_error_message ="Image Error:"

    if not left_image_result:
        image_error_message = image_error_message + " Left Image Not Taken!"

    if not right_image_result:
        image_error_message = image_error_message + " Right Image Not Taken!"

    if (not left_image_result) or (not right_image_result): # if one of the images not take successfully, force the warning box
        state_globals["external"]["alert"] = {'msg_text': image_error_message, 'severity':'Critical', 'informative_text': 'Check Cam3d Viewer, and restart if necessary.  Retake Image'}

    state_globals['external']['local_log'] = f'surface_1_path:{surface_1_local_path}'
    state_globals['external']['surface_1_file_location'] = surface_1_local_path # this is what gets displayed in the GUI
    if not(os.path.exists(surface_1_local_path)):
        time.sleep(5)
        if not(os.path.exists(surface_1_local_path)):
            message = 'You may need to click the blue button and blue triangle on Cam3d or restart it, Please also confirm there is only one camviewer gui open'
            npxc.alert_text(message, state_globals)
    state_globals['external']['surface_1_left_local_file_location'] = surface_1_left_local_path
    state_globals['external']['surface_1_right_local_file_location'] = surface_1_right_local_path

    #state_globals['external']['next_state'] = 'photodoc_confirm'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def photodoc_confirm_input(state_globals):
    """
    Input test function for state lower_probes_automatically
    """
    # create a insert_probes timestamp (YYYYMMDDHHMMSS)
    if 'retake_image' in state_globals['external'] and state_globals['external']['retake_image']:
        state_globals['external']['next_state'] = 'brain_surface_focus'
        state_globals['external'].pop('retake_image', None)
    else:
        state_globals['external']['next_state'] = 'confirm_ISI_match'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def confirm_ISI_match_enter(state_globals):
    if '1_day_before_experiment' in state_globals['external']['full_session_type'].lower():
        message = f"- Adjust the zoom to {state_globals['external']['old_low_zoom_level']}"
        npxc.alert_text(message, state_globals)

def select_stimulus_enter(state_globals):
    """
    Entry function for state select_stimulus
    """
    stim_files=[]


    state_globals['external']['local_log'] = f'Available Stim Files:{stim_files}'
    state_globals['external']['stim_files'] = stim_files



def select_stimulus_input(state_globals):
    """
    Input test function for state select_stimulus
    """
    print(f'stim file selected:{state_globals["external"]["stimulus_selected"]}')
    state_globals['external']['local_log'] = f'stim file selected:{state_globals["external"]["stimulus_selected"]}'

    #state_globals['external']['next_state'] = 'initiate_experiment'
    state_globals['external']['status_message'] = 'success'
    state_globals['external']['transition_result'] = True


def initiate_experiment_input(state_globals):
    """
    Input test function for state initiate_experiment
    """
    print('>> initiate_experiment_input <<')

    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    state_globals['external']["ExperimentStartTime"] = dt.now().strftime('%Y%m%d%H%M%S')
    state_globals['external']['local_log'] = f'ExperimentStartTime:{state_globals["external"]["ExperimentStartTime"]}'
    state_globals['external']['sync_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.sync'

    state_globals['external']['sync_temp_file'] = f'C:/ProgramData/AIBS_MPE/sync/output/{state_globals["external"]["session_name"]}_temp'

    try:
        proxy = state_globals['component_proxies']['Sync']
        try:
            proxy.start(path=state_globals['external']['sync_temp_file'])
            state_globals['external']['sync_temp_file'] = f'C:/ProgramData/AIBS_MPE/sync/output/{state_globals["external"]["session_name"]}_temp.h5'
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            print(f'Sync start failure:{e}!')
            state_globals['external']['status_message'] = f'Sync start failure:{e}'
            state_globals['external']['component_status']["Sync"] = False
    except Exception as e:
        print(f'Sync proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Sync proxy failure:{e}'
        state_globals['external']['component_status']["Sync"] = False


    # put in a delay between the sync starting and the videomon starting
    time.sleep(3)

    try:
        proxy = state_globals['component_proxies']['VideoMon']
        try:
            state_globals['external']['behavior_file_path'] = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}.behavior.avi'
            state_globals['external']['behavior_local_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.behavior.avi'
            state_globals['external']['behavior_local_file_name'] = f'{state_globals["external"]["session_name"]}.behavior.avi'

            state_globals['external']['eyetracking_file_path'] = f'{state_globals["external"]["mapped_lims_location"]}/{state_globals["external"]["session_name"]}.eye.avi'
            state_globals['external']['eyetracking_local_file_path'] = f'{state_globals["external"]["local_lims_location"]}/{state_globals["external"]["session_name"]}.eye.avi'
            state_globals['external']['eyetracking_local_file_name'] = f'{state_globals["external"]["session_name"]}.eye.avi'

            state_globals['external']['videomon_file_path'] = f'C:/ProgramData/AIBS_MPE/videomon/data/{state_globals["external"]["session_name"]}'
            print(f'videomon file path:{state_globals["external"]["videomon_file_path"]}')
            proxy.start_record(path=state_globals["external"]["videomon_file_path"])
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            logging.exception(f'VideoMon start failure:')
            print(f'VideoMon start failure:{e}!')
            state_globals['external']['status_message'] = f'Videomon start failure:{e}'
            state_globals['external']['component_status']["VideoMon"] = False
    except Exception as e:
        print(f'VideoMon proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Videomon proxy failure:{e}'
        state_globals['external']['component_status']["VideoMon"] = False

    # start the stim process
    # recreate the proxy
    try:
        proxy = state_globals['component_proxies']['Stim']
        try:
            print(f'starting stim:{state_globals["external"]["stimulus_selected"]}')
            proxy.start_script_from_path(state_globals["external"]["stimulus_selected"])
            state_globals['external']['status_message'] = 'success'
        except Exception as e:
            print(f'Stim start failure:{e}!')
            state_globals['external']['status_message'] = f'Stim start failure:{e}'
            state_globals['external']['component_status']["Stim"] = False
    except Exception as e:
        print(f'Stim proxy failure:{e}!')
        state_globals['external']['status_message'] = f'Stim proxy failure:{e}'
        state_globals['external']['component_status']["Stim"] = False

    state_globals['external']['clear_sticky'] = True
    #state_globals['external']['next_state'] = 'experiment_running_timer'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'
    npxc.save_platform_json(state_globals, manifest=False)


def experiment_running_timer_enter(state_globals):
    get_hab_message_2(state_globals)
    if 'first' in state_globals['external']['full_session_type']:
        message = ("It looks like its the first session with this mouse. "
        "You should be extra careful to check the lickspout position. "
        "Please watch for chin hits, paw hits, "
        "compulsive licking and first lick misses.\n"
        "Adjust spout position and wheel height accordingly.")
        npxc.alert_text(message, state_globals)


def monitor_experiment_input(state_globals):
    npxc.monitor_experiment_input(state_globals)


def initiate_behavior_stimulus_input(state_globals):
    npxc.initiate_behavior_stimulus_input(state_globals)
    npxc.save_platform_json(state_globals, manifest=False)

    print('>> initiate_behavior_experiment_input <<')


def camstim_ping_error_input(state_globals):
    npxc.camstim_ping_error_input(state_globals)

    print('>> camstim_ping_error_input <<')


def end_experiment_enter(state_globals):
    get_hab_message_3(state_globals)

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
    #opto_stim_file_path = os.path.join(
    #    state_globals["external"]["local_lims_location"], state_globals["external"]["session_name"] + ".opto.pkl"
    #)
    #visual_stim_file_path = os.path.join(
    #    state_globals["external"]["local_lims_location"], state_globals["external"]["session_name"] + ".stim.pkl"
    #)

    #state_globals["external"]["opto_stim_file_name"] = f'{state_globals["external"]["session_name"]}.opto.pkl'
    #state_globals["external"]["visual_stim_file_name"] = f'{state_globals["external"]["session_name"]}.stim.pkl'

    #state_globals["external"]["opto_stim_file_location"] = opto_stim_file_path
    #state_globals["external"]["visual_stim_file_location"] = visual_stim_file_path

    #print(
    #    f'mapped_lims_location:{state_globals["external"]["mapped_lims_location"]}, opto file name:{state_globals["external"]["opto_stim_file_name"]}'
    #)

    # recreate the proxy



    # stop the open ephys process
    #npxc.stop_ecephys_recording(state_globals)

    # stop the VideoMon process


    time.sleep(3)
    npxc.stop_common_session_monitoring(state_globals)
    # recreate the proxy

    # state_globals['external']['next_state'] = 'remove_probes_start'
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"

    state_globals["external"][
        "local_log"
    ] = f'ExperimentCompleteTime:{state_globals["external"]["ExperimentCompleteTime"]}'
    state_globals["external"]["transition_result"] = True
    npxc.save_platform_json(state_globals, manifest=False)


def initiate_behavior_habituation_input(state_globals):
    """
    Input test function for state initiate_experiment
    """

    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)

    print('>> initiate_behavior_habituation_input <<')

    npxc.start_common_session_monitoring(state_globals)

    state_globals['external']['next_statet'] = 'experiment_running_timer'
    initiate_behavior_stimulus_input(state_globals)

    #state_globals['external']['clear_sticky'] = True
    #state_globals['external']['next_state'] = 'experiment_running_timer'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def initiate_passive_habituaiton_input(state_globals):
    """
    Input test function for state initiate_experiment
    """
    # recreate the proxy
    # create a experiment start time timestamp (YYYYMMDDHHMMSS)


    print('>> initiate_passive_habituaiton_input <<')


    npxc.start_common_session_monitoring(state_globals)
    npxc.start_stim(state_globals)
    #state_globals['external']['clear_sticky'] = True
    #state_globals['external']['next_state'] = 'experiment_running_timer'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def remove_mouse_and_move_files2_input(state_globals):
    print('>> remove_mouse_and_move_files2_input <<')
    message = npxc.remove_mouse_input(state_globals)
    if message:
        fail_state(message, state_globals)

    ###Can't do this without retrieving water earned which we don't do...
    predict_earned = npxc.get_from_config(['predict_earned'], default=True)
    if predict_earned:
        try:
            pre_weight = float(state_globals['external']['mouse_weight_pre'])
            post_weight = float(state_globals['external']['mouse_weight_post'])
            loss_per_hour = float(npxc.get_from_config(['mouse_weight_loss_per_hr'], default=0))
            loss_per_session = float(npxc.get_from_config(['mouse_weight_loss_per_session'], default=.3))
            try:
                enter_time = int(state_globals["external"]["HeadFrameEntryTime"])
                exit_time = int(state_globals["external"]["HeadFrameExitTime"])
                experient_length_hrs = (exit_time - enter_time)/10000 #(approximate hours since I don't want to bother converting back to a real dt)

            except Exception as E:
                experient_length_hrs = float(npxc.get_from_config(['expected_hab_time'], default=1.2))

            print(f'approximate experiment length in hours: {experient_length_hrs}')
            expected_loss = experient_length_hrs*loss_per_hour
            expected_earned = round(((post_weight+ expected_loss +loss_per_session) - pre_weight), 2)
            state_globals['external']['expected_earned'] = expected_earned
            message = (f"Based on the pre and post weight we expect that the mouse earned {expected_earned} mls of water. "
            "If this is significantly wrong the solonoid may need to be calibrated")
            #npxc.alert_text(message, state_globals)
        except Exception as E:
            message = 'Failure occured trying to predict water earned'
            npxc.alert_text(message, state_globals)

    npxc.save_platform_json(state_globals, manifest=False)

def water_mouse_enter(state_globals):
    npxc.water_mouse_enter(state_globals)


def water_mouse_input(state_globals):
    npxc.water_mouse_input(state_globals)


def water_mouse_exit(state_globals):
    npxc.water_mouse_exit(state_globals)
    npxc.save_platform_json(state_globals, manifest=False)


def check_files1_enter(state_globals):
    get_hab_message_4(state_globals)

def check_files1_input(state_globals):
    print('>> check_files1_input <<')

    session_type = state_globals['external']['full_session_type']
    pkl_keyword = 'behavior-'
    behavior_pkl_path = npxc.get_pkl_path(pkl_keyword, state_globals)
    npxc.overwrite_foraging_id(behavior_pkl_path, session_type, state_globals)
    checkpoint = 1
    npxc.videomon_copy_wrapup(state_globals)
    if '1_day' in state_globals['external']['full_session_type']:
        session_type = state_globals['external']['full_session_type']
    else:
        session_type = 'behavior_habituation'
    failed = npxc.check_files_input(state_globals, session_type, checkpoint)

    if failed:
        #state_globals['external']['next_state'] = 'files_error1'
        npxc.alert_from_error_dict(state_globals, failed, primary_key=False)
    validation_type = state_globals['external']['session_type']
    validation_type = state_globals['external']['full_session_type'] + '_lims_ready'
    npxc.run_validation(state_globals, validation_type)
    failed = npxc.get_validation_results(state_globals, validation_type)
    state_globals['external']['next_state'] = 'create_manifest_and_platform_json_and_sync_report'
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
    npxc.save_platform_json(state_globals, manifest=False)

    print(f'Missing files is {failed}')


@state_transition
def foraging_ID_error_input(state_globals):
    #print('foraging ID:' + state_globals['external']['foraging_id'])
    #print('stimulus_name:' + state_globals['external']['stimulus_name'])
    #print('script_name:' + state_globals['external']['script_name'])
    #npxc.overrideable_error_state_input(state_globals, 'water_mouse', 'check_files1')
    pass


@state_transition
def foraging_ID_error_exit(state_globals):
    pass
    #npxc.overrideable_error_state_exit(state_globals)


def files_error1_input(state_globals):
    print(">> files_error_input <<")
    if "check_files_retry" in state_globals["external"] and state_globals["external"]["check_files_retry"]:
        state_globals["external"].pop("check_files_retry", None)
        check_files1_input(state_globals)
    else:
        state_globals['external']['next_state'] = 'create_manifest_and_platform_json_and_sync_report'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'

def create_manifest_and_platform_json_and_sync_report_input(state_globals):
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
        npxc.stop_common_session_monitoring(state_globals)

    npxc.save_platform_json(state_globals, manifest=False)
    state_globals["external"]["next_state"] = "workflow_complete"
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"


def check_files2_input(state_globals):
    print('>> check_files2_input <<')
    session_type = state_globals['external']['session_type']
    checkpoint = 2
    stop = npxc.check_files2(state_globals, session_type, checkpoint)
    print(f'stop is {stop}')
    if stop:
        state_globals['external']['next_state'] = 'files_error2'
    else:
        state_globals['external']['next_state'] = 'copy_files_to_network'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'


def files_error2_input(state_globals):
    print('>> files_error2_input <<')
    if 'check_files2_retry' in state_globals['external'] and state_globals['external']['check_files2_retry']:
        print('go back to initialize')
        state_globals['external']['next_state'] = 'check_files2'
        state_globals['external'].pop('check_files2_retry', None)
    elif 'create_files2_retry' in state_globals['external'] and state_globals['external']['create_files2_retry']:
        print('go back to initialize')
        state_globals['external']['next_state'] = 'create_manifest_and_platform_json_and_sync_report'
        state_globals['external'].pop('create_files2_retry', None)
    else:
        state_globals['external']['next_state'] = 'initiate_data_processing'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'

def copy_files_to_network_input(state_globals):
    """
    Entry function for state workflow_complete
    """
    print(f'>> copy_files_to_network_enter <<')
    npxc.save_notes(state_globals)
    time.sleep(1)
    npxc.backup_files(state_globals)
    npxc.probes_need_cleaning(state_globals)

    if dt.today().weekday() == 2:
        message = ("It looks like there may be an experiment later today. "
            "Please make sure to dip the probes in MilliQ water")
        npxc.alert_text(message, state_globals)
        probes_need_cleaning = True
    print('enter workflow complete')
    logging.start_stop('Neuropixels workflow complete', extra={'weblog': True})

def ready_to_check_network_input(state_globals):
    print('>> copy_files_to_network input <<')
    npxc.save_notes(state_globals)
    session_type = state_globals['external']['session_type']
    if npxc.global_processes['network_backup_process'].poll() is None:
        message = 'Files are not finished copying to the network. Wait a bit and try again'
        fail_state(message, state_globals)
    else:
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

def network_backup_error_input(state_globals):
    print('>> files_error2_input <<')
    if 'copy_retry' in state_globals['external'] and state_globals['external']['copy_retry']:
        print('go back to initialize')
        state_globals['external']['next_state'] = 'copy_files_to_network'
        state_globals['external'].pop('copy_retry', None)
    elif 'data_retry' in state_globals['external'] and state_globals['external']['data_retry']:
        print('go back to initialize')
        state_globals['external']['next_state'] = 'ready_to_check_network'
        state_globals['external'].pop('data_retry', None)
    else:
        state_globals['external']['next_state'] = 'workflow_complete'
    state_globals['external']['transition_result'] = True
    state_globals['external']['status_message'] = 'success'

def workflow_complete_enter(state_globals):
    """
    Entry function for state workflow_complete
    """
    logging.start_stop('Neuropixels workflow complete', extra={'weblog': True})
    state_globals['workflow_complete'] = True

def workflow_complete_input(state_globals):
    """
    Input test function for state workflow_complete
    """
    print("workflow complete input")
    state_globals["external"]["workflow_complete_time"] = dt.now().strftime('%Y%m%d%H%M%S')
    npxc.save_platform_json(state_globals, manifest=False)

    state_globals["external"]["next_state"] = None
    state_globals["external"]["transition_result"] = True
    state_globals["external"]["status_message"] = "success"
