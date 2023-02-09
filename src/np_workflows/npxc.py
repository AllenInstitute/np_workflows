import datetime
import inspect
import time
from typing import Any, Type

import np_config
import np_logging
import np_session
# from np_workflows.models import baseclasses, classes
# from np_workflows.models.baseclasses import Experiment
# from np_workflows.models import utils
from np_services import Initializable, Testable, TestError
from np_config import Rig

logger = np_logging.getLogger(__name__)

# Assign default values to global variables so they can be imported elsewhere
# experiment: Experiment | str = ''
operator: np_session.User | str = ''
mouse: np_session.Mouse | str = ''
is_mouse_in_lims: bool | None = None
session: np_session.Session | str | int = ''

# experiments: tuple[Type[Experiment], ...] = (
#     classes.Pretest,
#     classes.NpUltra,
#     ) # TODO plug-in experiments

CONFIG = np_config.Rig().config

lims_user_ids: tuple[str, ...] = tuple(
    sorted(
        CONFIG.get('lims_user_ids',
            [
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
            ]
        )
    )
)

default_mouse_id: int = int(CONFIG.get('default_mouse_id', 366122))

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
                
def print_countdown_timer(seconds: int | float | datetime.timedelta = 0, **kwargs):
    """Block execution for a given number of seconds (or any timedelta kwargs), printing a countdown timer to the console."""
    if isinstance(seconds, datetime.timedelta):
        wait = seconds
    else:
        wait = datetime.timedelta(seconds = seconds, **kwargs)
    time_0: float = time.time()
    time_remaining = lambda: datetime.timedelta(seconds = wait.total_seconds() - (time.time() - time_0))
    while time_remaining().total_seconds() > 0:
        print(f'Waiting {wait}: \t{time_remaining()}', end='\r', flush=True)
        time.sleep(.1)