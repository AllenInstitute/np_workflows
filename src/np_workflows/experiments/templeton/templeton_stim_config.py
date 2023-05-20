import configparser
import contextlib
import copy
import enum
import functools
from typing import ClassVar, Literal, NamedTuple, NoReturn, Optional, TypedDict

import IPython.display
import ipywidgets as ipw
import np_config
import np_logging
import np_session
import np_workflows
from typing import Any

logger = np_logging.getLogger(__name__)


class TempletonWorkflow(enum.Enum):
    """Enum for the different TTN sessions available, each with a different task."""
    PRETEST = "test"
    HAB_AUD = "hab: stage 2 aud"
    EPHYS_AUD = "ephys: stage 2 aud"
    HAB_VIS = "hab: stage 2 vis"
    EPHYS_VIS = "ephys: stage 2 vis"

AVAILABLE_TASK_NAMES = tuple(_.value for _ in TempletonWorkflow)

def camstim_defaults() -> dict:
    """Try to load defaults from camstim config file on the Stim computer.
    
    May encounter permission error if not running as svc_neuropix.
    """
    with contextlib.suppress(OSError):
        parser = configparser.RawConfigParser()
        parser.read(
            (np_config.Rig().paths["Camstim"].parent / "config" / "stim.cfg").as_posix()
        )

        camstim_default_config = {}
        for section in parser.sections():
            camstim_default_config[section] = {}
            for k, v in parser[section].items():
                try:
                    value = eval(
                        v
                    )  # this removes comments in config and converts values to expected datatype
                except:
                    continue
                else:
                    camstim_default_config[section][k] = value
        return camstim_default_config
    logger.warning("Could not load camstim defaults from config file on Stim computer.")
    return {}
