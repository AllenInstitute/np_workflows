from __future__ import annotations
import datetime
import time 

try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import inspect
    from typing import Any, Type

    from np_workflows.experiments import baseclasses, classes
    from np_workflows.experiments.baseclasses import Experiment
    from np_workflows.services.protocols import Initializable, Testable, TestFailure
    from np_workflows.services.config import Rig
    from np_workflows.services import zro, utils
    
    import np_session
    import np_logging
    import np_config
    
except Exception as exc:
    print(repr(exc))
    import pdb; pdb.set_trace()
    quit()
    
logger = np_logging.getLogger(__name__)

# Assign default values to global variables so they can be imported elsewhere
experiment: Experiment | str = ''
operator: np_session.lims.UserInfo | str = ''
mouse: np_session.lims.MouseInfo | str = ''
is_mouse_in_lims: bool | None = None
session: np_session.lims.SessionInfo | str | int = ''

# For reference:
transition_result: bool = True # T/F just sets text at bottom green/red, then returns to green in proceeding state
status_message: str = 'Ok'
msg_text: str = ''
alert: bool = False # T adds warning icon


def current_state() -> tuple[str, str]:
    "State name (before last underscore) and transition type (enter, input, exit)"
    current_frame = inspect.currentframe()
    calling_frame = inspect.getouterframes(current_frame, 2)[1][3] #?
    return calling_frame[: calling_frame.rfind("_")], calling_frame[calling_frame.rfind("_") + 1 :]

experiments: tuple[Type[Experiment], ...] = (
    classes.Pretest,
    classes.NpUltra,
    ) # TODO plug-in experiments

# TODO get userlist from np_config on per-rig basis
lims_user_ids: tuple[str, ...] = (         
        "hannah.belski",
        "hannah.cabasco",
        "ryan.gillis",
        "henry.loeffler",
        "corbettb",
        "ben.hardcastle",
        "samg",
        "ethan.mcbride",
        "jackie.kuyat",
        "andrew.shelton",
    )

def get_operators() -> list[str]:
    return list(lims_user_ids)
    
def get_experiments(with_lims: bool = True) -> list[str]:
    return [cls.__name__ for cls in experiments if with_lims or not issubclass(cls, baseclasses.WithLims)]

def start_rsc_apps() -> None:
    rsc_app_ids_required = np_config.from_zk(f'/rigs/{Rig.ID}')['rsc_app_ids_required']
    for comp in (Rig.Sync, Rig.Stim, Rig.Mon, Rig.Acq):
        rsc_node = zro.Proxy(comp.host, 6000)
        status = rsc_node.p_status()
        if any(apps_required_in_node := [_ for _ in rsc_app_ids_required if _ in status]):
            for app in apps_required_in_node:
                rsc_node.p_start(app)
        
def set(state_globals, **kwargs) -> None:
    "Update `state_globals['external'][key]` with kwargs"
    state_globals['external'].update(kwargs)

def get(state_globals, *args: str) -> Any | None | tuple[Any, ...]:
    "Get `state_globals['external'][arg]` for each arg in args"
    results = [state_globals['external'].get(arg) for arg in args]
    if len(results) == 1:
        return results[0]
    return tuple(results) if any(results) else None

def initialize_services(state_globals) -> None:
    experiment.apply_config_to_services()
    for service in experiment.services:
        
        set(state_globals, component_status={service.__name__: False})
        
        if isinstance(service, Initializable):
            while True:
                try:
                    service.initialize()
                    
                except Exception as exc:
                    logger.error("%s | %r", service.__name__, exc)
                    import pdb; pdb.set_trace()
                    continue
                
                else:
                    break
        
        if isinstance(service, Testable):
            while True:
                try:
                    service.test()
                    
                except TestFailure as exc:
                    try:
                        logger.error("%s | %r", service.__name__, service.exc)
                    except AttributeError:
                        logger.error("%s | %r", service.__name__, exc)
                    import pdb; pdb.set_trace()
                    continue
                
                except Exception as exc:
                    logger.error("%s | %r", service.__name__, exc)
                    import pdb; pdb.set_trace()
                    continue
                
                else:
                    break
                
def await_timer(state_globals: dict, wait_sec: int) -> datetime.timedelta:
    """
    Return timedelta until wait_sec is reached, then returns timedelta(0) which
    evaluates to False in Boolean contexts.
    
    Usage::
        >>> while time_remaining := await_timer(state_globals, wait_sec=600):
                print(f'{time_remaining} remaining')
                time.sleep(.1)
            print('Finished waiting')
    """
    if not get(state_globals, 'timer_start'):
        set(state_globals, timer_start=time.time()) # can't put datetime obj in state_globals['external']
    
    time_remaining = datetime.timedelta(
        seconds = wait_sec - (time.time() - get(state_globals, 'timer_start'))
        )
    
    if time_remaining.total_seconds() >= 0:
        print(f'{time_remaining} remaining')
        return time_remaining
    set(state_globals, timer_start=0) # reset for re-use
    return datetime.timedelta(0)