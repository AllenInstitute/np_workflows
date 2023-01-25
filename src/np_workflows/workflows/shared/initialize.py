from __future__ import annotations

try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import datetime
    import time
    from typing import Type

    from np_workflows.experiments import baseclasses, classes
    from np_workflows.experiments.baseclasses import Experiment
    from np_workflows.workflows.shared import npxc

    # we need all experiment classes imported into this namespace
    from np_workflows.experiments.classes import *

    import np_session
    import np_logging

except Exception as exc:
    print(repr(exc))
    import pdb; pdb.set_trace()
    quit()
    
logger = np_logging.getLogger(__name__)


# mouse & operator --------------------------------------------------------------------- 

def select_mouse_and_operator_enter(state_globals) -> None:
    npxc.set(state_globals, operators=[""] + npxc.get_operators())
    state_globals["external"]["mouse"] = '366122' #! defaults for testing
    # state_globals["external"]["operator"] = 'ben.hardcastle' #! defaults for testing
    
    # import pdb; pdb.set_trace()
    # state_globals['resources']['workflow']['states'].keys()
     
def select_mouse_and_operator_input(state_globals) -> None:
        
    mouse_input = state_globals["external"]["mouse"]
    try:
        npxc.mouse = np_session.lims.MouseInfo(mouse_input)
    except KeyError as exc:
        logger.warning("LIMS | Mouse %r could not be fetched from database: %r", mouse_input, exc)
        npxc.mouse = mouse_input
        npxc.is_mouse_in_lims = False
    
    try:
        project = npxc.mouse.project_name
    except (AttributeError, KeyError):
        project = "NA"
        if npxc.is_mouse_in_lims is None:
            logger.warning("LIMS | Mouse %r could not be fetched from database: %r", mouse_input)
        npxc.mouse = mouse_input
        npxc.is_mouse_in_lims = False
    else:  
        npxc.is_mouse_in_lims = True
    finally:
        npxc.set(state_globals, project=str(project), is_mouse_in_lims=npxc.is_mouse_in_lims)
    
    operator_input = state_globals["external"]["operator"]
    try:
        npxc.operator = np_session.lims.UserInfo(operator_input)
    except KeyError as exc:
        logger.warning("LIMS | Operator %r could not be fetched from database: %r", operator_input, exc)
        npxc.operator = operator_input
        
    status_message = f'Mouse {"is" if npxc.is_mouse_in_lims else "is not"} in lims'
    transition_result = False if not npxc.is_mouse_in_lims else True # None isn't allowed in s_g['external'] 
    npxc.set(state_globals, status_message=status_message, transition_result=transition_result)
        
# experiment ---------------------------------------------------------------------------

def select_experiment_enter(state_globals) -> None:
    experiments = npxc.get_experiments(with_lims = bool(npxc.is_mouse_in_lims))
    if not experiments:
        alert = True
        msg_text = "No experiments found"
        npxc.set(state_globals, alert=alert, msg_text=msg_text)
    npxc.set(state_globals, experiments=[""] + experiments)
    
    
def select_experiment_input(state_globals) -> None:
    "Create Experiment instance and configure services"
    
    experiment_class: Type[Experiment] = eval(npxc.get(state_globals, 'experiment'))
    
    if issubclass(experiment_class, baseclasses.WithLims):
        npxc.experiment = experiment_class(
            *npxc.get(state_globals, 'mouse', 'operator'),
            lims_session_id = int(npxc.session) if npxc.session else None,
            )
    else:
        npxc.experiment = experiment_class(npxc.mouse)
        
    if not isinstance(npxc.experiment, Experiment):
        raise ValueError(f"Experiment {npxc.experiment!r} was not converted to a valid Experiment class")
        
    npxc.experiment.state_globals = state_globals
    npxc.experiment.initialize_services()

    
# -------------------------------------------------------------------------------------- #

# def initialize_experiment_enter(state_globals) -> None:
#     state_globals["external"]["msg_text"] = 'Initializing services... check progress in output window'
#     state_globals['external']['alert'] = True # T adds warning icon 
# def initialize_experiment_input(state_globals) -> None:
    
