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
from camstim import SweepStim_v2, MovieStim
from camstim import Warp, Window
from camstim.misc import wecanpicklethat


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

# monkey-patch MovieStim to serialize without large redundant arrays
# ----------------------------------------------------------------------------

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
    del self_dict['frame_list']
    self_dict['stim'] = str(self_dict['stim'])
    return wecanpicklethat(self_dict)

MovieStim.package = package

# setup main stim
# -----------------------------------------------------------------------
# build the stimulus array with parameterized repeats & durations

main_stimuli = []

old_repeats, reversed_repeats, annotated_repeats = (
    json_params["stim_repeats"][key] for key in ("old", "reversed", "annotated")
)
old_sec, reversed_sec, annotated_sec = (
    json_params["stim_lengths_sec"][key] for key in ("old", "reversed", "annotated")
)

segment_stim_secs = (
    [("old_stim.stim", old_sec)] * old_repeats
    + [
        ("shuffle_reversed.stim", reversed_sec),
        ("shuffle_reversed_1st.stim", reversed_sec),
        ("shuffle_reversed_2nd.stim", reversed_sec),
    ] * reversed_repeats
    + [("densely_annotated_%02d.stim" % i, annotated_sec) for i in range(19)]
    * annotated_repeats
    + [("old_stim.stim", old_sec)] * old_repeats
    + [
        ("shuffle_reversed.stim", reversed_sec),
        ("shuffle_reversed_1st.stim", reversed_sec),
        ("shuffle_reversed_2nd.stim", reversed_sec),
    ] * reversed_repeats
)

# setup stim list and timing
cumulative_duration_sec = (
    main_sequence_start_sec
) = 0  # if stims are daisy-chained within one script, this should be the end of the prev stim
for stim_file, duration_sec in segment_stim_secs:
    segment = Stimulus_v2.from_file(stim_file, window) # stim file actually instantiates MovieStim
    segment_ds = [(cumulative_duration_sec, cumulative_duration_sec + duration_sec)]
    segment.set_display_sequence(segment_ds)

    cumulative_duration_sec += duration_sec
    main_stimuli.append(segment)

main_sequence_end_sec = cumulative_duration_sec  # if daisy-chained, the next stim in this script should start at this time

# create SweepStim_v2 instance for main stimulus
ss = SweepStim_v2(
    window,
    stimuli=main_stimuli,
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
