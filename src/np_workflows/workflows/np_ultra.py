from typing import Hashable


try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import datetime
    import time
    import pdb
    from typing import Type

    from np_workflows.services import open_ephys as OpenEphys
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



# name each photodoc state `capture_photodoc_<state>`, `review_photodoc_<state>`
photodoc_states_to_labels = {
    0: 'brain_surface',
    1: 'pre_experiment', # once per experiment loop
    2: 'post_experiment', # once, after all experiment loops
    }

# def generate_photodoc_states(photodoc_states_to_labels: dict[Hashable, str]):
#* can we put this in shared.photodoc module (/in a function) and import them automatically?
for state, label in photodoc_states_to_labels.items():
    exec(
        f"""def capture_photodoc_{state}_enter(state_globals):
            npxc.set(state_globals, photodoc_label='{label}')
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
    # TODO move time to experiment.config
    npxc.set(state_globals, settle_time_total_sec=10*60)
    npxc.set(state_globals, settle_time_remaining_sec=10*60)
    
    