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


class TTNSession(enum.Enum):
    """Enum for the different TTN sessions available, each with different param sets."""

    PRETEST = "pretest"
    HAB_60 = "hab 60"
    HAB_90 = "hab 90"
    HAB_120 = "hab 120"
    EPHYS = "ephys"


# setup parameters ---------------------------------------------------------------------
default_ttn_params = {}

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

## no longer added to default_ttn_params:
# default_ttn_params.update(**camstim_defaults())


# main stimulus defaults ---------------------------------------------------------------
default_ttn_params["main"] = {}

default_ttn_params["main"]["sweepstim"] = {
    "trigger_delay_sec": 5.0,           #! does it matter?
    'sync_sqr_loc': (870, 525),         # for Window.warp=Warp.Disabled 
    'sync_sqr_loc_warp': (540, 329),    # for Window.warp=Warp.Spherical
}
# default_ttn_params["main"]["movie_path"] = "C:/ProgramData/StimulusFiles/dev/"
default_ttn_params["main"][
    "monitor"
] = 'Gamma1.Luminance50'

# other parameters that vary depending on session type (pretest, hab, ephys):
def per_session_main_stim_params(session: TTNSession) -> dict[str, Any]:
    def build_session_stim_params(
        key: str, old: int, reversed: int, annotated: int
    ) -> dict[str, dict[str, int]]:
        return {key: dict(old=old, reversed=reversed, annotated=annotated)}

    def session_stim_repeats(session: TTNSession) -> dict[str, dict[str, int]]:
        repeats = functools.partial(build_session_stim_params, "stim_repeats")
        match session:
            case TTNSession.PRETEST:
                return repeats(1, 1, 1)
            case TTNSession.HAB_60:
                return repeats(15, 5, 1)
            case TTNSession.HAB_90:
                return repeats(20, 7, 2)
            case TTNSession.HAB_120 | TTNSession.EPHYS:
                return repeats(25, 8, 2)
            case _:
                raise ValueError(f"Stim repeats not implemented for {session}")

    def session_stim_lengths(session: TTNSession) -> dict[str, dict[str, int]]:
        lengths_sec = functools.partial(build_session_stim_params, "stim_lengths_sec")
        match session:
            case TTNSession.PRETEST:
                return lengths_sec(1, 1, 1)
            case _:
                return lengths_sec(40, 40, 60)

    def main_blank_screen(session: TTNSession) -> dict[str, float | int]:
        match session:
            case TTNSession.PRETEST:
                return {"pre_blank_screen_sec": .5, "post_blank_screen_sec": .5}
            case _:
                return {"pre_blank_screen_sec": 2, "post_blank_screen_sec": 2}

    params = copy.deepcopy(default_ttn_params["main"])
    params.update(session_stim_repeats(session))
    params.update(session_stim_lengths(session))
    params.update(main_blank_screen(session))
    return params


# optotagging defaults -----------------------------------------------------------------

default_ttn_params["opto"] = {}

# all parameters depend on session type (pretest, hab, ephys):

def per_session_opto_params(
    session: TTNSession, mouse: str | int | np_session.Mouse
) -> dict[str, dict[str, str | list[float] | Literal["pretest", "experiment"]]]:
    "All params for opto depending on session (e.g. will be empty for habs)."

    def opto_mouse_id(mouse_id: str | int | np_session.Mouse) -> dict[str, str]:
        return {"mouseID": str(mouse_id)}

    def opto_levels(session: TTNSession) -> dict[str, list[float]]:
        default_opto_levels: list[float] = camstim_defaults()["Optogenetics"][
            "level_list"
        ]
        match session:
            case TTNSession.PRETEST | TTNSession.EPHYS:
                return {"level_list": sorted(default_opto_levels)[-2:]}
            case _:
                raise ValueError(f"Opto levels not implemented for {session}")

    def opto_operation_mode(
        session: TTNSession,
    ) -> dict[str, Literal["pretest", "experiment"]]:
        match session:
            case TTNSession.PRETEST:
                return {"operation_mode": "pretest"}
            case TTNSession.EPHYS:
                return {"operation_mode": "experiment"}
            case _:
                raise ValueError(f"Opto levels not implemented for {session}")

    match session:
        case TTNSession.PRETEST | TTNSession.EPHYS:
            params = copy.deepcopy(default_ttn_params["opto"])
            params.update(opto_mouse_id(mouse))
            params.update(opto_levels(session))
            params.update(opto_operation_mode(session))
            return params
        case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
            return {}
        case _:
            raise ValueError(f"Opto params not implemented for {session}")


# mapping defaults ---------------------------------------------------------------------

default_ttn_params["mapping"] = {}

default_ttn_params["mapping"]["monitor"] = "Gamma1.Luminance50"
default_ttn_params["mapping"]["gabor_path"] = "gabor_20_deg_250ms.stim" 
default_ttn_params["mapping"]["flash_path"] = "flash_250ms.stim" # relpath in StimulusFiles dir (for _v2 objects)
default_ttn_params["mapping"]["default_gabor_duration_seconds"] = 1200
default_ttn_params["mapping"]["default_flash_duration_seconds"] = 300 # may be overriden by 'max_total_duration_minutes'

default_ttn_params["mapping"]["sweepstim"] = {
    'sync_sqr_loc_warp': (540, 329),     # for Window.warp=Warp.Spherical
} 
# trigger_delay_sec not specified
# all stim parameters depend on session type (pretest, hab, ephys):
def per_session_mapping_params(session: TTNSession) -> dict[str, dict[str, int]]:
    "`'mapping'` key in params dict should be updated with the returned dict (which will be empty for habs)."

    def mapping_duration(session: TTNSession) -> dict[str, float]:
        # 0 = full length = gabor_duration + flash_duration = maximum possible
        match session:
            case TTNSession.PRETEST:
                return {"max_total_duration_minutes": 0.1}
            case TTNSession.EPHYS | TTNSession.HAB_120:
                return {"max_total_duration_minutes": 10}
            case _:
                raise ValueError(f"Mapping params not implemented for {session}")

    def mapping_blank_screen(session: TTNSession) -> dict[str, float | int]:
        match session:
            case TTNSession.PRETEST:
                return {"pre_blank_screen_sec": .5, "post_blank_screen_sec": .5}
            case TTNSession.EPHYS | TTNSession.HAB_120:
                return {"pre_blank_screen_sec": 2, "post_blank_screen_sec": 2}
            case _:
                raise ValueError(f"Mapping params not implemented for {session}")

    match session:
        case TTNSession.PRETEST | TTNSession.EPHYS | TTNSession.HAB_120:
            params = copy.deepcopy(default_ttn_params["mapping"])
            params.update(mapping_duration(session))
            params.update(mapping_blank_screen(session))
            return params
        case TTNSession.HAB_60 | TTNSession.HAB_90:
            return {}
        case _:
            raise ValueError(f"Mapping params not implemented for {session}")
