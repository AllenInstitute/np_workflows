import inspect
import json
import logging
import os

# limstk functions are auto-generated so the ide might warn it can't see them.
import threading
from time import sleep

import requests
from mpetk import limstk, mpeconfig, zro
from wfltk import middleware_messages_pb2 as messages

from model import DynamicRouting  # It can make sense to have a class to store experiment data.
from mvr import MVRConnector  # This will eventually get incorporated into the workflow launcher

# Setup your typical components; the config, services, and perhaps some typical models for an experiment.
# An alternative is to store these in the state.  However, this is one of those times that globals are ok because
# these values are "read only" and used ubiquitously.

config: dict = mpeconfig.source_configuration("dynamic_routing", use_local_config=True)
experiment = DynamicRouting()
mvr: MVRConnector
camstim_agent: zro.Proxy
sync: zro.Proxy


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


def init_enter(state):
    """
    Since this is the first state, you might do some basic startup functionality in the enter state.
    """
    global mvr
    try:
        mvr = MVRConnector(args=config['MVR'])
    except Exception as e:
        fail_state(f"Failed to connect to MVR on {config['MVR']}", state)

    global camstim_agent
    camstim_agent = zro.Proxy("stim_pc:6000", timeout=1.0)  # or some config value

    global sync
    sync = zro.Proxy("sync_pc:5001", timeout=1.0)  # or some config value

def get_user_id_input(state):
    """
      Description: The user will input their user name and it will ve validated against the LIMS db.
    """

    # external contains values coming from the UI.  "user_id" is a key specified in the wfl file.
    user_name = state["external"]["user_id"]
    if not limstk.user_details(user_name):
        fail_state(f"Could not validate user {user_name} in LIMS.", state)
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

    # At this point, if there was more than one state a user could go to you would do the following
    state["external"]["next_state"] = "run_stimulus_enter"

def run_stimulus_enter(state):
    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(message="Waiting for stimulus script to complete."))

    #  Normally, we want to start the recording devices just before camstim
    sync.start()
    mvr.start_record(file_name_prefix=experiment.experiment_id)
    camstim_agent.start_session(experiment.mouse_id, experiment.user_name)

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

    t = threading.Thread(target=check_stimulus)
    t.start()



