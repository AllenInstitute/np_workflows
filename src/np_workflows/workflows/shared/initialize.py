from __future__ import annotations

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

logger = np_logging.getLogger(__name__)

#* move these vars to npxc and import from there

# Assign default values to global variables so they can be imported elsewhere
experiment: Experiment | str = ''
operator: np_session.lims.UserInfo | str = 'ben.hardcastle'
mouse: np_session.lims.MouseInfo | str = '366122'
is_mouse_in_lims: bool | None = None
session: np_session.lims.SessionInfo | str | int = ''

# For reference:
transition_result: bool = True # T/F just sets text at bottom green/red, then returns to green in proceeding state
status_message: str = 'Ok'
msg_text: str = ''
alert: bool = False # T adds warning icon

# mouse & operator --------------------------------------------------------------------- 

def select_mouse_and_operator_enter(state_globals) -> None:
    operators = npxc.get_operators()
    npxc.set(state_globals, operators=[""] + operators)
    state_globals["external"]["mouse"] = str(mouse) #! defaults for testing
    state_globals["external"]["operator"] = str(operator) #! defaults for testing
    
def select_mouse_and_operator_input(state_globals) -> None:
    
    global mouse, is_mouse_in_lims
    
    mouse_input = state_globals["external"]["mouse"]
    try:
        mouse = np_session.lims.MouseInfo(mouse_input)
    except KeyError as exc:
        logger.warning("LIMS | Mouse %r could not be fetched from database: %r", mouse_input, exc)
        mouse = mouse_input
        is_mouse_in_lims = False
    
    try:
        project = mouse.project_name
    except (AttributeError, KeyError):
        project = "NA"
        if is_mouse_in_lims is None:
            logger.warning("LIMS | Mouse %r could not be fetched from database: %r", mouse_input, exc)
        mouse = mouse_input
        is_mouse_in_lims = False
    else:  
        is_mouse_in_lims = True
    finally:
        npxc.set(state_globals, project=project, is_mouse_in_lims=is_mouse_in_lims)
    
    global operator
    operator_input = state_globals["external"]["operator"]
    try:
        operator = np_session.lims.UserInfo(operator_input)
    except KeyError as exc:
        logger.warning("LIMS | Operator %r could not be fetched from database: %r", operator_input, exc)
        operator = operator_input
        
    status_message = f'Mouse {"is" if is_mouse_in_lims else "is not"} in lims'
    transition_result = False if not is_mouse_in_lims else True # None isn't allowed in s_g['external'] 
    npxc.set(state_globals, status_message=status_message, transition_result=transition_result)
        
# experiment ---------------------------------------------------------------------------

def select_experiment_enter(state_globals) -> None:
    experiments = npxc.get_experiments(with_lims = bool(is_mouse_in_lims))
    if not experiments:
        alert = True
        msg_text = "No experiments found"
        npxc.set(state_globals, alert=alert, msg_text=msg_text)
    npxc.set(state_globals, experiments=[""] + experiments)
    
    state_globals["external"]["experiment"] = 'PretestNP2' #! hard-coded for testing
    
def select_experiment_input(state_globals) -> None:
    "Create Experiment instance"
    
    experiment_class: Type[Experiment] = eval(*npxc.get(state_globals, 'experiment'))

    global experiment
    
    if issubclass(experiment_class, baseclasses.WithLims):
        experiment = experiment_class(
            *npxc.get(state_globals, 'mouse', 'operator'),
            lims_session_id = int(session) if session else None,
            )
    
    import pdb; pdb.set_trace()
    
    
