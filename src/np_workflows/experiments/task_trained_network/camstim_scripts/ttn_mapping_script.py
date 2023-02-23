"""
Oct'22 task-trained ephys stimuli
"""

import argparse
import json
import logging
import os
import time

import numpy as np
from psychopy import visual
from camstim import Foraging
from camstim import Stimulus_v2
from camstim import SweepStim_v2
from camstim import Warp, Window


# get params ------------------------------------------------------------------
# stored in json file -
# path to json supplied by camstim via command line arg when this script is called

parser = argparse.ArgumentParser()
parser.add_argument(
    "params_path",
    nargs="?",
    type=str,
    default="",
)
args, _ = parser.parse_known_args()

with open(args.params_path, "r") as f:
    json_params = json.load(f)

# Create display window
# ----------------------------------------------------------------------------
window = Window(
    fullscr=True,
    monitor=json_params["monitor"],
    screen=0,
    warp=Warp.Spherical,
)

# patch the Stimulus_v2 class to allow for serializing without large arrays
# ----------------------------------------------------------------------------
class Stimulus_v2_MinusFrameArrays(Stimulus_v2):

    def __init__(self, *args, **kwargs):
        super(Stimulus_v2_MinusFrameArrays, self).__init__(*args, **kwargs)

    def package(self):
        """
        Package for serializing - minus large arrays of frame timing/order.
        """
        if not self.save_sweep_table:
            self.sweep_table = None
            self.sweep_params = self.sweep_params.keys()
        self_dict = self.__dict__
        del self_dict['sweep_frames']
        del self_dict['sweep_order']
        self_dict['stim'] = str(self_dict['stim'])
        return wecanpicklethat(self_dict)
    
# ----------------------------------------------------------------------------
# setup mapping stim
"""from mapping_script_v2.py"""

mapping_stimuli = []

# load common stimuli
gabor_path = json_params["gabor_path"]
flash_path = json_params["flash_path"]
gabor = Stimulus_v2_MinusFrameArrays.from_file(gabor_path, window)
flash = Stimulus_v2_MinusFrameArrays.from_file(flash_path, window)

gabor_duration_sec = json_params["default_gabor_duration_seconds"]
flash_duration_sec = json_params["default_flash_duration_seconds"]

original_duration_sec = gabor_duration_sec + flash_duration_sec

# if max total duration is set, and less than original movie length, cut down display sequence:
max_mapping_duation_minutes = json_params[
    "max_total_duration_minutes"
]  # can be zero, in which case we use the full movie length
max_mapping_duration_sec = max_mapping_duation_minutes * 60
if 0 < max_mapping_duration_sec < original_duration_sec:
    logging.info("Mapping duration capped at %s minutes", max_mapping_duation_minutes)

    logging.info("original gabor duration: %s sec", gabor_duration_sec)
    logging.info("original flash duration: %s sec", flash_duration_sec)
    logging.info("max mapping duration: %s sec", max_mapping_duration_sec)

    gabor_duration_sec = (
        max_mapping_duration_sec * gabor_duration_sec
    ) / original_duration_sec
    flash_duration_sec = (
        max_mapping_duration_sec * flash_duration_sec
    ) / original_duration_sec

    logging.info("modified gabor duration: %s sec", gabor_duration_sec)
    logging.info("modified flash duration: %s sec", flash_duration_sec)

# setup timing
mapping_sequence_start_sec = 0  # if stims are daisy-chained within one script, this should be the end of the prev stim
gabor.set_display_sequence([(mapping_sequence_start_sec, gabor_duration_sec)])
flash.set_display_sequence(
    [(gabor_duration_sec, gabor_duration_sec + flash_duration_sec)]
)

mapping_stimuli = [gabor, flash]

mapping_sequence_end_sec = (
    gabor_duration_sec + flash_duration_sec
)  # if daisy-chained, the next stim in this script should start at this time

# create SweepStim_v2 instance for main stimulus
ss = SweepStim_v2(
    window,
    stimuli=mapping_stimuli,
    pre_blank_sec=json_params["pre_blank_screen_sec"],
    post_blank_sec=json_params["post_blank_screen_sec"],
    params=json_params["sweepstim"],
)

# add in foraging so we can track wheel, potentially give rewards, etc
f = Foraging(
    window=window,
    auto_update=False,
    params=json_params["sweepstim"],
    nidaq_tasks={
        "digital_input": ss.di,
        "digital_output": ss.do,
    },
)  # share di and do with SS

ss.add_item(f, "foraging")

ss.run()
