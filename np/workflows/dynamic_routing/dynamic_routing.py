import pdb
import socket

# pdb.set_trace()
try:
    import inspect
    import json
    import logging
    import os
    #
    # limstk functions are auto-generated so the ide might warn it can't see them.
    import shutil
    import threading
    from time import sleep

    import mpetk
    import mpetk.aibsmw.routerio.router as router
    import np.workflows.npxcommon as npxc
    import requests
    import yaml
    from mpetk import limstk, mpeconfig, zro
    from np.models import model
    from np.models.model import \
        DynamicRouting  # It can make sense to have a class to store experiment data.
    from np.services.ephys_api import EphysHTTP as ephys
    from np.services.mvr import \
        MVRConnector  # This will eventually get incorporated into the workflow launcher
    from wfltk import middleware_messages_pb2 as messages

except Exception as e:
    # import errors aren't printed to console by default
    print(e)

# Setup your typical components; the config, services, and perhaps some typical models for an experiment.
# An alternative is to store these in the state.  However, this is one of those times that globals are ok because
# these values are "read only" and used ubiquitously.

config: dict = mpeconfig.source_configuration("dynamic_routing")

pc = socket.gethostname()
acq = {
    "W10DTSM112719":"W10DTSM112722", # NP0
    "W10DTSM18306":"W10DTSM18278", # NP1
    "W10DTSM18307":"W10DTSM18280", # NP2
    "W10DTMJ0AK6GM":"W10SV108131", #ben-desktop:btTest
}
hostname = acq.get(pc,"localhost")
config['MVR'] = {
    "host": hostname,  
    "port": 50000
}

experiment = DynamicRouting()

io: router.ZMQHandler

mvr: MVRConnector
camstim_agent: zro.Proxy
sync: zro.Proxy
mouse_director: zro.Proxy


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
    state_name = calling_frame[:calling_frame.rfind("_")]

    #  These are values expected by the UI
    state['external']['alert'] = True
    state["external"]["transition_result"] = False
    state["external"]["next_state"] = state_name
    state["external"]["msg_text"] = message
    logging.warning(f"{state_name} failed: {message}")


def connect_to_services(state):
    """    The expectation is these services are on (i.e., you have started them with RSC).  If the connection fails you can
    send a message to the user with the fail_state() function above.
    """
    global io
    io = state['resources']['io']

    component_errors = []

    # TODO Here we either need to test open ephys by trying to record or we get the status message.  awaiting testing.

    global mvr
    try:
        mvr = MVRConnector(args=config['MVR'])
        if not mvr._mvr_connected:
            logging.info("Failed to connect to mvr")
            component_errors.append(
                f"Failed to connect to MVR on {config['MVR']}")
    except Exception:
        component_errors.append(f"Failed to connect to MVR.")

    global camstim_agent
    try:
        service = config['camstim_agent']
        camstim_agent = zro.Proxy(
            f"{service['host']}:{service['port']}", timeout=service['timeout'], serialization='json')
        logging.info(f'Camstim Agent Uptime: {camstim_agent.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Camstim Agent.")

    global sync
    try:
        service = config['sync']
        sync = zro.Proxy(
            f"{service['host']}:{service['port']}", timeout=service['timeout'])
        logging.info(f'Sync Uptime: {sync.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Sync.")

    global mouse_director
    try:
        service = config['mouse_director']
        mouse_director = zro.Proxy(
            f"{service['host']}:{service['port']}", timeout=service['timeout'])
        logging.info(f'MouseDirector Uptime: {mouse_director.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to MouseDirector.")
        # TODO MDir currently not working - switch this line back on when fixes
        # logging.info(" ** skipping connection to MouseDirector **")

    #  At this point, You could send the user to an informative state to repair the remote services.
    if component_errors:
        fail_state('\n'.join(component_errors), state)


def state_transition(state_transition_function):
    def wrapper(state):

        transition_type = state_transition_function.__name__.split('_')[-1]
        npxc.save_state(state)
        state_transition_function(state)
        return None
    return wrapper


@state_transition
def resume_enter(state):
    pass


@state_transition
def resume_input(state):
    prompt = state['external'].get('prompt', False)
    if prompt is True:
        state = npxc.load_previous_state()
    else:
        pdb.set_trace()
        state['external']['next_state'] = state['external']['next_state']


@state_transition
def restore_enter(state):
    pass


@state_transition
def restore_input(state):
    pass


@state_transition
def init_enter(state):
    """
    Testing image display
    """
    state['external']["exp_logo"] = R"C:\progra~1\AIBS_MPE\workflow_launcher\np\images\logo_np_vis.png"
    connect_to_services(state)

    state["external"]["user_id"] = "ben.hardcastle"
    state["external"]["mouse_id"] = "366122"


@state_transition
def init_input(state):
    """
    Since this is the first state, you might do some basic startup functionality in the enter state.
    """
    pass


@state_transition
def get_user_id_entry(state):
    pass


@state_transition
def get_user_id_input(state):
    """
      Description: The user will input their user name and it will ve validated against the LIMS db.
    """

    # external contains values coming from the UI.  "user_id" is a key specified in the wfl file.
    user_name = state["external"]["user_id"]
    if not limstk.user_details(user_name):
        fail_state(f"Could not find user \"{user_name}\" in LIMS", state)
    state["user_name"] = user_name  # It is ok to save data into the state.
    try:
        mouse_director.set_user_id(user_name)
    except:
        pass


@state_transition
def get_mouse_id_input(state):
    """
    input function for state get_mouse_id
    :param state: dictionary representing data passing between states and the UI
    """

    # external contains values coming from the UI.  "mouse_id" is a key specified in the wfl file.
    mouse_id = state["external"]["mouse_id"]

    try:
        mouse_result = limstk.donor_info_with_parent(mouse_id)
    except:
        # mouse_result not returned if prev limstk query errors
        fail_state(f"Could not find mouse id \"{mouse_id}\" in LIMS", state)
        return

    #  You might want to get data about this mouse from MTRain
    response = requests.get("http://mtrain:5000/get_script/",
                            data=json.dumps({"LabTracks_ID": mouse_id}))
    if response.status_code != 200:
        fail_state(f"Could not find mouse id \"{mouse_id}\" in MTrain", state)
        return

    try:
        mouse_director.set_mouse_id(mouse_id)
    except:
        pass
    experiment.mouse_id = mouse_id
    experiment.script = response.json()['data']['script'].split('/')[-1]
    experiment.stimulus_name = response.json()['data']['name']

    #  The following is a typical message MPE would expect at this stage.
    log_message = (f"MID, {mouse_id},\n "
                   f'UID, {state["external"]["user_id"].ljust(10)},\n '
                   f'BID, {os.getenv("aibs_comp_id")},\n '
                   f"Action, \n"
                   f"Received\n")
    logging.info(log_message, extra={"weblog": True})



def mvr_capture_on_enter(state_globals,photo_path=None):
    """standard mvr image snapshot func, returning error mesg or img  """
    mvr_writer.take_snapshot()
    
    def wait_on_snapshot():  
        while True: #TODO add timeout to prevent infinite loop
            # try:
            for message in mvr_writer.read():
                pdb.set_trace()
                if message.get('mvr_broadcast', False) == "snapshot_taken":
                    drive, filepath = os.path.splitdrive(message['snapshot_filepath'])
                    source_photo_path = f"\\\\{config['MVR']['host']}\\{drive[0]}${filepath}"
                    sleep(1) # MVR has responded too quickly.  It hasn't let go of the file so we must wait.
                    dest_photo_path = shutil.copy(source_photo_path, photo_path or "C:/ProgramData/AIBS_MPE/wfltk/temp")
                    logging.info(f"Copied: {source_photo_path} -> {dest_photo_path}")
                    return True, dest_photo_path
                elif message.get('mvr_broadcast', False) == "snapshot_failed":
                    return False, message['error_message']
            # except Exception as e:
            #     return False, e
            
    success, mesg_or_img = wait_on_snapshot()
    if not success:
        try:
            fail_state(f"Error taking snapshot: {mesg_or_img}",state_globals)
        except: 
            pass
    else:
        return mesg_or_img # return the captured image


@state_transition
def binary_next_state_prompt(state, tf, next_if_true, next_if_false):
    if tf:
        state["external"]["next_state"] = next_if_true
    else:
        state["external"]["next_state"] = next_if_false


@state_transition
def pre_brain_surface_photo_doc_enter(state):
    # display new mvr image
    pdb.set_trace()
    state['external']['new_snapshot'] = mvr_capture_on_enter(state)


@state_transition
def pre_brain_surface_photo_doc_input(state):
    # prompt to retake mvr image
    # pdb.set_trace()
    prompt = state['external'].get('snapshot_retry', False)
    binary_next_state_prompt(state, prompt, next_if_true="pre_brain_surface_photo_doc",
                             next_if_false="probe_insertion_instructions")


@state_transition
def pre_brain_surface_photo_doc_exit(state):
    #  TODO add to platform json:
    # experiment.platform_json.add_file(dest_photo_path)
    ...


@state_transition
def flush_lines_enter(state):
    # pdb.set_trace()
    # io["external"][] = "enter"
    # fail_state("testing enter fail", state)
    ...


@state_transition
def flush_lines_input(state):
    # pdb.set_trace()
    # fail_state("testing input fail", state)
    # io["test2"] = "input"
    ...


@state_transition
def run_stimulus_enter(state):
    """
    This is a typical method to start recordings and stimulus but is also a model of how to handle user events.
    Note that this is the "enter" function and not the "input" function.  This executes before the screen is presented
    to the user.  The green arrow will not be available to them as a result of the state_busy message.
    """

    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    io.write(messages.state_busy(
        message="Waiting for stimulus script to complete."))

    #  Normally, we want to start the recording devices just before camstim
    sync.start()
    mvr.start_record(file_name_prefix=experiment.experiment_id)
    io.write(ephys.start_ecephys_recording())
    #  If you are using the start session api (usually with mtrain, you would do the following.
    camstim_agent.start_session(experiment.mouse_id, experiment.user_name)

    #  TODO:  Add an example of start_script

    #  The following technique for turning off the next arrow and calling a "is ready" function is applicable for
    #  any user event. (timers, waiting on services, etc)

    def check_stimulus():  # you could define this outside of the state of course.
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
        io.write(ephys.stop_ecephys_recording())
        sync.stop()
        # re-enables the arrow
        io.write(messages.state_ready(message="Stimulus complete."))
        try:
            mouse_director.finalize_session('')
        except:
            pass
    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=check_stimulus)
    t.start()


@state_transition
def wait_on_sync_enter(state):
    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(
        message="Waiting for sync to complete."))

    def wait_on_sync():  # you could define this outside of the state of course.
        while "READY" not in sync.get_state():
            sleep(3)
        # re-enables the arrow
        io.write(messages.state_ready(message="Stimulus complete."))

    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=wait_on_sync)
    t.start()


@state_transition
def settle_timer_enter(state):
    #  This is in the enter state because we want to do things before the user sees the screen (like turn off arrow)
    state["resources"]["io"].write(messages.state_busy(
        message="Waiting for stimulus script to complete."))

    def wait_on_timer():  # you could define this outside of the state of course.
        sleep(5.0)  # gives camstim agent a chance to get started
        # re-enables the arrow
        io.write(messages.state_ready(message="Stimulus complete."))

    #  Because the next arrow is disabled, we can wait on this thread to re-enable it without the user being able to
    #  progress the workflow.
    t = threading.Thread(target=wait_on_timer)
    t.start()


def write_manifest():
    lims_session = limstk.Session(
        "neuropixel", "neuropixels", id=experiment.session_id)
    lims_session.trigger_data["sessionid"] = experiment.session_id
    lims_session.trigger_data["location"] = lims_session.path_data["location"]
    # ims_session.add_to_manifest(platform_file_path, remove_source = False)
    # lims_session.write_manifest(trigger_filename=trigger_file_name)
