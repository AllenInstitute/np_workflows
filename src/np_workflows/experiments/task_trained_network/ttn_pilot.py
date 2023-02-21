import configparser
import copy
import dataclasses
import datetime
import enum
import functools
import pathlib
import threading
import time
from typing import ClassVar, Literal, NamedTuple, NoReturn, Optional, TypedDict

import IPython
import IPython.display
import ipywidgets as ipw
import np_config
import np_logging
import np_services
import np_session
from np_workflows import Hab as BaseHab, Ecephys as BaseEcephys
import PIL.Image
import pydantic
from pyparsing import Any
from np_services import (
    Service, Finalizable,
    ScriptCamstim, OpenEphys, Sync, VideoMVR, NewScaleCoordinateRecorder, MouseDirector,
)

logger = np_logging.getLogger(__name__)

# --------------------------------------------------------------------------------------

class TTNSession(enum.Enum):
    """Enum for the different TTN sessions available, each with different param sets."""
    PRETEST = 'pretest'
    HAB_60 = 'hab 60'
    HAB_90 = 'hab 90'
    HAB_120 = 'hab 120'
    ECEPHYS = 'ecephys'

# --------------------------------------------------------------------------------------
class TTNMixin:
    
    selected_session: 'TTNSelectedSession'
    
    @property
    def recorders(self) -> tuple[Service, ...]:
        match self.selected_session.session:
            case TTNSession.PRETEST | TTNSession.ECEPHYS:
                return (Sync, VideoMVR, OpenEphys)
            case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
                return (Sync, VideoMVR)
    @property
    def stims(self) -> tuple[Service, ...]:
        return (ScriptCamstim, )   
    
    def initialize_and_test_services(self) -> None:
        """Initialize and test services."""
        MouseDirector.user = self.user.id
        MouseDirector.mouse = self.mouse.id

        OpenEphys.folder = self.session.folder

        NewScaleCoordinateRecorder.log_root = self.session.npexp_path   
        
        super().initialize_and_test_services()
            
    def start_stim(self) -> None:

        # mapping and main -------------------------------------------------------------------- #
        ScriptCamstim.script = self.selected_session.mapping_and_main_script
        ScriptCamstim.params = self.selected_session.all_params

        logger.info('Starting mapping and main script')

        ScriptCamstim.start()

        while not ScriptCamstim.is_ready_to_start():
            time.sleep(10)

        if isinstance(ScriptCamstim, Finalizable):
            ScriptCamstim.finalize()

        logger.info('Mapping and main script complete')

        # opto --------------------------------------------------------------------------------- #
        if self.selected_session.opto_params:
            ScriptCamstim.script = self.selected_session.opto_script
            ScriptCamstim.params = self.selected_session.opto_params
            
            logger.info('Starting opto-tagging script')
            ScriptCamstim.start()

            while not ScriptCamstim.is_ready_to_start():
                time.sleep(10)

            if isinstance(ScriptCamstim, Finalizable):
                ScriptCamstim.finalize()

            logger.info('Opto-tagging script complete')
        else:
            logger.info('Opto-tagging skipped')
            
class Hab(TTNMixin, BaseHab):
        
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            ScriptCamstim,
            )
        super().__init__(*args, **kwargs)
        
class Ecephys(TTNMixin, BaseEcephys):
    
    def __init__(self, *args, **kwargs):
        self.services = (
            MouseDirector,
            Sync,
            VideoMVR,
            self.imager,
            ScriptCamstim,
            OpenEphys,
            NewScaleCoordinateRecorder,
            )
        super().__init__(*args, **kwargs)
        
def new_experiment(mouse, user, selected_session) -> Ecephys | Hab:
    """Create a new experiment for the given mouse and user."""
    match selected_session:
        case TTNSession.PRETEST | TTNSession.ECEPHYS:
            experiment = Ecephys(mouse, user, session_type='ecephys')
        case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
            experiment = Hab(mouse, user, session_type='hab')
        case _: raise ValueError(f'Invalid session type: {selected_session}')
    experiment.selected_session = selected_session
    return experiment

# setup parameters ---------------------------------------------------------------------
default_ttn_params = {}

# camstim defaults ---------------------------------------------------------------------
# fetched from config file on the Stim computer

parser = configparser.RawConfigParser()
parser.read((np_config.Rig().paths['Camstim'].parent / 'config' / 'stim.cfg').as_posix())

camstim_default_config = {}
for section in parser.sections():
    camstim_default_config[section] = {}
    for k,v in parser[section].items():
        try:
            value = eval(v) # this removes comments in config and converts values to expected datatype
        except:
            continue
        else:
            camstim_default_config[section][k] = value

default_ttn_params.update(**camstim_default_config)



# main stimulus defaults ---------------------------------------------------------------
default_ttn_params['main'] = {}

default_ttn_params['main']['sweepstim'] = {
	    # 'syncsqr': True,
        # 'syncsqrloc': (875,550),
        # 'syncsqrsize': (150,150),
        # 'syncpulse': True,
        # 'syncpulseport': 1,
        # 'syncpulselines': [5, 6],  # frame, start/stop
        'trigger_delay_sec': 5.0,
    }
default_ttn_params['main']['movie_path'] = 'C:/ProgramData/StimulusFiles/dev/'
default_ttn_params['main']['frames_per_sec'] = 30.0         #! unused
default_ttn_params['main']['session'] = 1                   #! unused
default_ttn_params['main']['monitor'] = 'GammaCorrect30'    #! MUST CONFIRM: value used in other scripts = 'Gamma1.Luminance50'

# other parameters that vary depending on session type (pretest, hab, ecephys):

def build_session_stim_params(key: str, old: int, reversed: int, annotated: int) -> dict[str, dict[str, int]]:
    return {key: dict(old=old, reversed=reversed, annotated=annotated)}

def session_stim_repeats(session: TTNSession) -> dict[str, dict[str, int]]:
    repeats = functools.partial(build_session_stim_params, 'stim_repeats')
    match session:
        case TTNSession.PRETEST:
            return repeats(1, 1, 1)
        case TTNSession.HAB_60:
            return repeats(15, 5, 1)
        case TTNSession.HAB_90:
            return repeats(20, 7, 2)
        case TTNSession.HAB_120 | TTNSession.ECEPHYS:
            return repeats(25, 8, 2)
        case _:
            raise ValueError(f"Stim repeats not implemented for {session}")
        
def session_stim_lengths(session: TTNSession) -> dict[str, dict[str, int]]:
    lengths_sec = functools.partial(build_session_stim_params, 'stim_lengths_sec')
    match session:
        case TTNSession.PRETEST:
            return lengths_sec(2, 2, 2)
        case _:
            return lengths_sec(40, 40, 60)
        
def main_blank_screen(session: TTNSession) -> dict[str, int]:
    match session:
        case TTNSession.PRETEST:
            return {'pre_blank_screen_sec': 1, 'post_blank_screen_sec': 1}
        case _:
            return {'pre_blank_screen_sec': 2, 'post_blank_screen_sec': 2}
            
def session_stim_params(session: TTNSession) -> dict[str, Any]:
    params  = copy.deepcopy(default_ttn_params['main'])
    params.update(session_stim_repeats(session))
    params.update(session_stim_lengths(session))
    params.update(main_blank_screen(session))
    return params



# optotagging defaults -----------------------------------------------------------------

default_ttn_params['opto'] = {}
default_ttn_params['opto_script'] = 'C:/ProgramData/StimulusFiles/dev/opto_tagging_v2.py'

# all parameters depend on session type (pretest, hab, ecephys):

def session_opto_params(session: TTNSession, mouse: str | int | np_session.Mouse) -> dict[str, dict[str, str | list[float] | Literal['pretest', 'experiment']]]:
    "All params for opto depending on session (e.g. will be empty for habs)."

    def opto_mouse_id(mouse_id: str | int | np_session.Mouse) -> dict[str, str]:
        return {'mouseID': str(mouse_id)}

    def opto_levels(session: TTNSession) -> dict[str, list[float]]:
        default_opto_levels: list[float] = default_ttn_params['Optogenetics']['level_list']
        match session:
            case TTNSession.PRETEST | TTNSession.ECEPHYS:
                return {'level_list': sorted(default_opto_levels)[-2:]}
            case _:
                raise ValueError(f"Opto levels not implemented for {session}")
            
    def opto_operation_mode(session: TTNSession) -> dict[str, Literal['pretest', 'experiment']]:
        match session:
            case TTNSession.PRETEST:
                return {'operation_mode': 'pretest'}
            case TTNSession.ECEPHYS:
                return {'operation_mode': 'experiment'}
            case _:
                raise ValueError(f"Opto levels not implemented for {session}")
            
    match session:
        case TTNSession.PRETEST | TTNSession.ECEPHYS:
            params  = copy.deepcopy(default_ttn_params['opto'])
            params.update(opto_mouse_id(mouse))
            params.update(opto_levels(session))
            params.update(opto_operation_mode(session))
            return params
        case TTNSession.HAB_60 | TTNSession.HAB_90 | TTNSession.HAB_120:
            return {}
        case _:
            raise ValueError(f"Opto params not implemented for {session}")
        
        
# mapping defaults ---------------------------------------------------------------------

default_ttn_params['mapping'] = {}

default_ttn_params['mapping']['monitor'] = 'Gamma1.Luminance50'
default_ttn_params['mapping']['gabor_path'] = 'gabor_20_deg_250ms.stim'
default_ttn_params['mapping']['flash_path'] = 'flash_250ms.stim'
default_ttn_params['mapping']['gabor_duration_seconds'] = 1200 
default_ttn_params['mapping']['flash_duration_seconds'] = 300 

# two alternative sweepstim paramsets from different scripts:

default_ttn_params['mapping']['sweepstim'] = {
    'syncpulse': True,
    'syncpulseport': 1,
    'syncpulselines': [4, 7],  # frame, start/stop
    'trigger_delay_sec': 0.0,
    'bgcolor': (-1,-1,-1),
    'eyetracker': False,
    'eyetrackerip': "W7DT12722", #! np.0 mon is w10dtsm112722
    'eyetrackerport': 1000,
    'syncsqr': True,
    'syncsqrloc': (0,0),
    'syncsqrfreq': 60,
    'syncsqrsize': (100,100),
    'showmouse': True
} # from dv_spontaneous_stimulus.py

default_ttn_params['mapping']['sweepstim'] = {
    
} # from a DR experiment with mapping_script_v2.py

# all parameters depend on session type (pretest, hab, ecephys):
def session_mapping_params(session: TTNSession) -> dict[str, dict[str, int]]:
    "`'mapping'` key in params dict should be updated with the returned dict (which will be empty for habs)."
    
    def mapping_duration(session: TTNSession) -> dict[str, float]:
        # 0 = full length = gabor_duration + flash_duration = maximum possible
        match session:
            case TTNSession.PRETEST:
                return {'max_total_duration_minutes': 0.1}
            case TTNSession.ECEPHYS | TTNSession.HAB_120:
                return {'max_total_duration_minutes': 10} 
            case _:
                raise ValueError(f"Mapping params not implemented for {session}")
            
    def mapping_blank_screen(session: TTNSession) -> dict[str, int]:
        match session:
            case TTNSession.PRETEST:
                return {'pre_blank_screen_sec': 1, 'post_blank_screen_sec': 1}
            case TTNSession.ECEPHYS | TTNSession.HAB_120:
                return {'pre_blank_screen_sec': 10, 'post_blank_screen_sec': 10}
            case _:
                raise ValueError(f"Mapping params not implemented for {session}")
            
    match session:
        case TTNSession.PRETEST | TTNSession.ECEPHYS | TTNSession.HAB_120:
            params = copy.deepcopy(default_ttn_params['mapping'])
            params.update(mapping_duration(session))
            params.update(mapping_blank_screen(session))
            return params
        case TTNSession.HAB_60 | TTNSession.HAB_90:
            return {}
        case _:
            raise ValueError(f"Mapping params not implemented for {session}")

 
class TTNSelectedSession:
    
    common_params: ClassVar[dict] = default_ttn_params
    "Will be updated with `session_params` when a session is selected."
    
    opto_script: ClassVar[str] = 'C:/ProgramData/StimulusFiles/dev/opto_tagging_v2.py'
    "Used with `opto_params`"
    
    def __init__(self, session: str | TTNSession, mouse: str | int | np_session.Mouse):
        if isinstance(session, str):
            session = TTNSession(session)
        self.session = session
        self.mouse = str(mouse)
        
    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.session}, {self.mouse})'
    
    @property
    def mapping_and_main_script(self) -> str:
        "Used together with `all_params` to run mapping and main stim script."
        logger.warning(f'Using hard-coded script in notebooks directory for testing')
        # will eventually point to 'C:/ProgramData/StimulusFiles/dev/oct22_tt_stim_script.py'
        return np_config.local_to_unc(np_config.Rig().sync, pathlib.Path('oct22_tt_stim_script.py').resolve()).as_posix()
    
    @property
    def all_params(self) -> dict[Literal['main', 'mapping', 'opto'], dict[str, Any]]:
        params = copy.deepcopy(self.common_params)
        params['main'] = self.main_params
        params['mapping'] = self.mapping_params
        params['opto'] = self.opto_params
        return params
    
    @property
    def main_params(self) -> dict[str, Any]:
        return session_stim_params(self.session)
    
    @property
    def mapping_params(self) -> dict[str, Any]:
        return session_mapping_params(self.session)
    
    @property
    def opto_params(self) -> dict[str, Any]:
        return session_opto_params(self.session, self.mouse)
    
def stim_session_select_widget(mouse: str | int | np_session.Mouse) -> TTNSelectedSession:
    """Select a stimulus session (hab, pretest, ecephys) to run.
    
    An object with mutable attributes is returned, so the selected session can be
    updated along with the GUI selection. (Preference would be to return an enum
    directly, and change it's value, but that doesn't seem possible.)
    
    """
    
    selection = TTNSelectedSession(TTNSession.PRETEST, mouse)
       
    session_dropdown = ipw.Select(
        options = tuple(_.value for _ in TTNSession),
        description = 'Session',
    )
    console = ipw.Output()
    with console:
        print(f'Selected: {selection.session}')
    
    def update(change):
        if change['name'] != 'value':
            return
        if (options := getattr(change['owner'], 'options', None)) and change['new'] not in options:
            return
        if change['new'] == change['old']:
            return
        selection.__init__(str(session_dropdown.value), mouse)
        with console:
            print(f'Selected: {selection.session}')
            
    session_dropdown.observe(update)
    
    IPython.display.display(ipw.VBox([session_dropdown, console]))
    
    return selection
