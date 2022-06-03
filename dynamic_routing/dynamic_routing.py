import inspect
import json
import logging
import os

# limstk functions are auto-generated so the ide might warn it can't see them.
import shutil
import threading
from time import sleep

import requests
from mpetk import limstk, mpeconfig, zro
from wfltk import middleware_messages_pb2 as messages

from .model import DynamicRouting  # It can make sense to have a class to store experiment data.
from .mvr import MVRConnector  # This will eventually get incorporated into the workflow launcher

# Setup your typical components; the config, services, and perhaps some typical models for an experiment.
# An alternative is to store these in the state.  However, this is one of those times that globals are ok because
# these values are "read only" and used ubiquitously.

config: dict = mpeconfig.source_configuration("dynamic_routing")
experiment = DynamicRouting()

mvr: MVRConnector
camstim_agent: zro.Proxy
sync: zro.Proxy
mouse_director: zro.Proxy


# TODO:  connect to open ephys
#        check drive space
#
def fail_state(message: str, state: dict):
    """
    This is an example of how to create a single failure reporting function.  It inspects the stack to figure out which
    state is reporting the failure and fills out the necessary messaging.  This will post a message to the user and
    allow them to redo the state.

    :param message: the fail reason
    :param state: the state dictionary
    :return:
    """
    current_frame = inspect.currentframe()
    calling_frame = inspect.getouterframes(current_frame, 2)[1][3]
    state_name = calling_frame[: calling_frame.rfind("_")]

    #  These are values expected by the UI
    state['external']['alert'] = True
    state["external"]["transition_result"] = False
    state["external"]["next_state"] = state_name
    state["external"]["msg_text"] = message
    logging.warning(f"{state_name} failed: {message}")

def wait_on_snapshot():  # you could define this outside of the state of course.
    while True:
        for message in mvr.read():
            if message.get('mvr_broadcast', False) == "snapshot_taken":
                drive, filepath = os.path.splitdrive(message['snapshot_filepath'])
                experiment.pre_brain_surface_photo = f"\\\\{config['MVR']['host']}\\{drive[0]}${filepath}"
                sleep(1)  # MVR has responded too quickly.  It hasn't let go of the file so we must wait.
                dest = shutil.copy(experiment.pre_brain_surface_photo, "C:/ProgramData/AIBS_MPE/dynamic_routing")
                logging.info(f"Copied: {experiment.pre_brain_surface_photo} -> {dest}")
                return True, dest
            elif message.get('mvr_broadcast', False) == "snapshot_failed":
                return False, message['error_message']


def init_input(state):
    """
    Since this is the first state, you might do some basic startup functionality in the enter state.
    The expectation is these services are on (i.e., you have started them with RSC).  If the connection fails you can
    send a message to the user with the fail_state() function above.
    """
    import os
    logging.info(f"PYTHON PATH={os.getenv('PYTHON_PATH', default='NA')}")
    component_errors = []
    global mvr
    mvr = MVRConnector(args=config['MVR'])
    if not mvr._mvr_connected:
        logging.info("Failed to connect to mvr")
        component_errors.append(f"Failed to connect to MVR on {config['MVR']}")

    global camstim_agent
    service = config['camstim_agent']
    camstim_agent = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'], serialization='json')
    try:
        logging.info(f'Camstim Agent Uptime: {camstim_agent.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Camstim Agent.")

    global sync
    service = config['sync']
    sync = zro.Proxy(f"{service['host']}:{service['port']}", timeout=service['timeout'])
    try:
        logging.info(f'Sync Uptime: {sync.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Sync.")

    #  At this point, You could send the user to an informative state to repair the remote services.
    if component_errors:
        fail_state('\n'.join(component_errors), state)
    else:
        state["external"]["next_state"] = "get_user_id"


def get_user_id_input(state):
    """
      Description: The user will input their user name and it will ve validated against the LIMS db.
    """

    # external contains values coming from the UI.  "user_id" is a key specified in the wfl file.
    user_name = state["external"]["user_id"]
    if not limstk.user_details(user_name):
        fail_state(
            f"Could not find user {user_name} in LIMS.  You might get this error if you have never logged into LIMS",
            state)
    state["user_name"] = user_name  # It is ok to save data into the state.





def get_mouse_id_input(state):
    """
    input function for state get_mouse_id
    :param state: dictionary representing data passing between states and the UI
    """

    # external contains values coming from the UI.  "mouse_id" is a key specified in the wfl file.
    mouse_id = state["external"]["mouse_id"]
    mouse_result = limstk.donor_info_with_parent(mouse_id)

    if "error" in mouse_result:
        fail_state(f"Could not find mouse id {mouse_id} in LIMS", state)
        return

    #  You might want to get data about this mouse from MTRain
    response = requests.get("http://mtrain:5000/get_script/", data=json.dumps({"LabTracks_ID": mouse_id}))
    if response.status_code != 200:
        fail_state(f"Could not find mouse id {mouse_id} in MTrain", state)
        return

    experiment.mouse_id = mouse_id
    experiment.script = response.json()['data']['script'].split('/')[-1]
    experiment.stimulus_name = response.json()['data']['name']

    #  The following is a typical message MPE would expect at this stage.
    log_message = (
        f"MID, {mouse_id},\n "
        f'UID, {state["external"]["user_id"].ljust(10)},\n '
        f'BID, {os.getenv("aibs_comp_id")},\n '
        f"Action, \n"
        f"Received\n"
    )
    logging.info(log_message, extra={"weblog": True})

def pre_brain_surface_photo_doc_enter(state):
    mvr.take_snapshot()
    success, mesg = wait_on_snapshot()
    if not success:
        fail_state("Error taking snapshot: {mesg}")
    else:
        state['external']['pre_insertion_image'] = mesg

def pre_brain_surface_photo_doc_input(state):
    """
    In this state, the next transition depends on user input.  The fields snapshot_retry and snapshot_continue are
    defined in the wfl file.  The item the user selected will be true, the others will be false.
    Here, we just evaluate the user choice and set the next state as desired.
    """
    if state['external'].get('snapshot_retry', False):
        state["external"]["next_state"] = "pre_brain_surface_photo_doc"
    else:
        state["external"]["next_state"] = "probe_insertion_instructions"

"""
def probe_insertion_instructions_exit(state):
    filename = mvr.take_photo()
    experiment.platform_json.add_file(filename)
"""


def flush_water_lines_input(state):
    ...


def run_stimulus_enter(state):
    """
    This is a typical method to start recordings and stimulus but is also a model of how to handle user events.
    Note that this is the "enter" function and not the "input" function.  This executes before the screen is presented
    to the user.  The green arrow will not be available to them as a result of the state_busy message.
    """

    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(message="Waiting for stimulus script to complete."))

    #  Normally, we want to start the recording devices just before camstim
    sync.start()
    mvr.start_record(file_name_prefix=experiment.experiment_id)

    #  If you are using the start session api (usually with mtrain, you would do the following.
    camstim_agent.start_session(experiment.mouse_id, experiment.user_name)

    #  TODO:  Add an example of start_script

    #  The following technique for turning off the next arrow and calling a "is ready" function is applicable for
    #  any user event. (timers, waiting on services, etc)

    def check_stimulus():  # you could define this outside of the state of course.
        io = state['resources']['io']
        sleep(5.0)  # gives camstim agent a chance to get started
        while True:

            try:
                if not camstim_agent.is_running():
                    break
                sleep(3.0)
            except Exception as err:
                pass  # time outs are possible depending on the script.  Maybe implement a max retry

        #  It is possible here to check camstim agent for an error and take some action.
        mvr.stop_record()
        sync.stop()
        io.write(messages.state_ready(message="Stimulus complete."))  # re-enables the arrow

    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=check_stimulus)
    t.start()


def wait_on_sync_enter(state):
    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(message="Waiting for sync to complete."))

    def wait_on_sync():  # you could define this outside of the state of course.
        io = state['resources']['io']
        while "READY" not in sync.get_state():
            sleep(3)
        io.write(messages.state_ready(message="Stimulus complete."))  # re-enables the arrow

    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=wait_on_sync)
    t.start()


def settle_timer_enter(state):
    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(message="Waiting for stimulus script to complete."))

    def wait_on_timer():  # you could define this outside of the state of course.
        io = state['resources']['io']
        sleep(5.0)  # gives camstim agent a chance to get started
        io.write(messages.state_ready(message="Stimulus complete."))  # re-enables the arrow

    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=wait_on_timer)
    t.start()


def write_manifest():
    lims_session = limstk.Session("neuropixel", "neuropixels", id=experiment.session_id)
    lims_session.trigger_data["sessionid"] = experiment.session_id
    lims_session.trigger_data["location"] = lims_session.path_data["location"]
    # ims_session.add_to_manifest(platform_file_path)
    # lims_session.write_manifest(trigger_filename=trigger_file_name)
