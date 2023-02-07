from __future__ import annotations
import datetime
import time 

try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import inspect
    from typing import Any, Type

    from np_workflows.models import baseclasses, classes
    from np_workflows.models.baseclasses import Experiment
    from np_workflows.models import zro, utils
    from np_services import Initializable, Testable, TestFailure
    from np_config import Rig
    
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
operator: np_session.User | str = ''
mouse: np_session.Mouse | str = ''
is_mouse_in_lims: bool | None = None
session: np_session.Session | str | int = ''

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

def initialize_services() -> None:
    experiment.apply_config_to_services()
    for service in experiment.services:
        
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
    time_remaining = datetime.timedelta(
        seconds = wait_sec - (time.time() - get(state_globals, 'timer_start'))
        )
    
    if time_remaining.total_seconds() >= 0:
        print(f'{time_remaining} remaining')
        return time_remaining
    set(state_globals, timer_start=0) # reset for re-use
    return datetime.timedelta(0)