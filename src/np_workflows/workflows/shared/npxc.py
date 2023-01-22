import inspect
from typing import Any, Type

from np_workflows.experiments import baseclasses, classes
from np_workflows.experiments.baseclasses import Experiment

import np_logging

# logger = np_logging.getLogger(__name__)

def current_state() -> tuple[str, str]:
    "State name (before last underscore) and transition type (enter, input, exit)"
    current_frame = inspect.currentframe()
    calling_frame = inspect.getouterframes(current_frame, 2)[1][3] #?
    return calling_frame[: calling_frame.rfind("_")], calling_frame[calling_frame.rfind("_") + 1 :]

experiments: tuple[Type[Experiment], ...] = (classes.PretestNP2,) # TODO plug-in experiments

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
    return [cls.__name__ for cls in experiments if with_lims == issubclass(cls, baseclasses.WithLims)]

def set(state_globals, **kwargs) -> None:
    "Update `state_globals['external'][key]` with kwargs"
    state_globals['external'].update(kwargs)

def get(state_globals, *args: str) -> tuple[Any, ...]:
    "Get `state_globals['external'][arg]` for each arg in args"
    return tuple(state_globals['external'][arg] for arg in args)