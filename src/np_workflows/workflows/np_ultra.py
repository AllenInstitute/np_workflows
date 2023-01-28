from typing import Hashable


try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import datetime
    import time
    import pdb
    from typing import Type

    from np_workflows.services import open_ephys as OpenEphys
    from np_workflows.services import middleware_messages_pb2 as router_msg

    from np_workflows.workflows.shared.npxc import experiment
    from np_workflows.workflows.shared import npxc
    from np_workflows.workflows.shared.initialize import (
        select_mouse_and_operator_enter, 
        select_mouse_and_operator_input,
        select_experiment_enter, 
        select_experiment_input,
        )
    from np_workflows.workflows.shared.photodoc import (
        capture_photodoc_input,
        review_photodoc_enter,
    )
    from np_workflows.workflows.shared.pretest import (
        run_pretest_input,
    )
    
except Exception as exc:
    print(repr(exc))
    import pdb; pdb.set_trace()
    exit()

npxc.start_rsc_apps()

# name each photodoc state `capture_photodoc_<state>`, `review_photodoc_<state>`
photodoc_states_to_labels = {
    0: 'brain_surface',
    1: 'pre_recording', # once per trial loop, after probes advanced + settled 
    2: 'post_experiment', # once, after all experiment loops
    }

# def generate_photodoc_states(photodoc_states_to_labels: dict[Hashable, str]):
#* can we put this in shared.photodoc module (/in a function) and import them automatically?
for state, label in photodoc_states_to_labels.items():
    exec(
        f"""def capture_photodoc_{state}_enter(state_globals):
            npxc.set(state_globals, photodoc_label='{label}')
            npxc.set(state_globals, msg_text='{label} will be captured')
        """
    )
    exec(
        f"""def capture_photodoc_{state}_input(state_globals): 
            capture_photodoc_input(state_globals)
        """
    )
    exec(
        f"""def review_photodoc_{state}_enter(state_globals):
            review_photodoc_enter(state_globals)
        """
    )

def start_experiment_loop_enter(state_globals) -> None:
    npxc.experiment.advance_trial_idx(state_globals)

def start_recording_input(state_globals) -> None:
    npxc.experiment.start_recording()
    
def start_stimulus_input(state_globals) -> None:
    npxc.experiment.start_stimulus()
        
def stop_recording_input(state_globals) -> None:
    npxc.experiment.stop_recording()
       
def settle_timer_enter(state_globals):
    
    import pdb; pdb.set_trace()
    if npxc.get(state_globals, 'override_settle_timer'):
        npxc.set(state_globals, await_timer=False)
        npxc.set(state_globals, override_settle_timer=True)
        return
    
    # TODO move time to experiment.config
    settle_timer_sec = 10
    
    if time_remaining := npxc.await_timer(state_globals, wait_sec=settle_timer_sec):
        time.sleep(.1) # when time is printed on-screen it will be higher than actual time remaining by at least this much
        npxc.set(state_globals, time_remaining=f'{time_remaining}')
        npxc.set(state_globals, override_settle_timer=False)
        npxc.set(state_globals, await_timer=True)
        state_globals['resources']['io'].write(router_msg.state_busy(message="Awaiting settle timer"))
        return
    # print('Finished waiting')
    # # import pdb; pdb.set_trace()
    npxc.set(state_globals, override_settle_timer=True)
    npxc.set(state_globals, await_timer=False)
    state_globals['resources']['io'].write(router_msg.state_ready(message="Finished awaiting settle timer"))

# def settle_timer_input(state_globals): 
#     npxc.set(state_globals, next_state='settle_timer')
#     # import pdb; pdb.set_trace()
    # if npxc.get(state_globals, 'override_settle_timer') or not npxc.get(state_globals, 'await_timer'):
    #     return
    # settle_timer_sec = 10
    # if time_remaining := npxc.await_timer(state_globals, wait_sec=settle_timer_sec):
    #     time.sleep(.1) # when time is printed on-screen will be higher than actual value by at least this much
    #     npxc.set(state_globals, time_remaining=f'{time_remaining}')
    #     npxc.set(state_globals, override_settle_timer=False)
    #     npxc.set(state_globals, await_timer=True)
    #     state_globals['resources']['io'].write(router_msg.state_busy(message="Awaiting settle timer"))
    #     return
    # state_globals['resources']['io'].write(router_msg.state_ready(message="Finished
    # awaiting settle timer"))
    

def settle_timer_enter(state_globals):
    state_globals['resources']['io'].write(router_msg.state_busy(message="Awaiting settle timer"))
    npxc.set(state_globals, override_settle_timer=False)
    # pass
# def settle_timer_input(state_globals):
    # npxc.set(state_globals, msg_text='Awaiting settle timer')
    import threading
    def wait_on_timer():
        settle_timer_sec = 10
        while time_remaining := npxc.await_timer(state_globals, wait_sec=settle_timer_sec):
            time.sleep(.1)
            if npxc.get(state_globals, 'override_settle_timer'):
                break
            npxc.set(state_globals, time_remaining=f'{time_remaining}')
        state_globals['resources']['io'].write(router_msg.state_ready(message="Finished awaiting settle timer"))
        # npxc.set(state_globals, msg_text='Finished awaiting settle timer')
        # npxc.set(state_globals, alert=True)
            
    t = threading.Thread(target=wait_on_timer)
    t.start()

def settle_timer_input(state_globals):
    if npxc.get(state_globals, 'override_settle_timer'):
        return
    npxc.set(state_globals, next_state='settle_timer')