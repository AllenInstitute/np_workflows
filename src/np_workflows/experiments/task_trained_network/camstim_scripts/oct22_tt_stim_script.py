"""
Oct'22 task-trained ephys stimuli
"""
#from psychopy import visual
from camstim import Stimulus, SweepStim
from camstim import Foraging
from camstim import Window, Warp
import numpy as np

# Create display window
window = Window(fullscr=True,
                monitor='GammaCorrect30', #MUST CONFIRM
                screen=0,
                warp=Warp.Disabled
                )

movie_path = 'C:\\Users\\svc_flex4\\tt_testing\\'

session = 1
segment_secs = (
    [('old_stim.stim', 40)]*25 +
    [('shuffle_reversed.stim', 40), ('shuffle_reversed_1st.stim', 40), ('shuffle_reversed_2nd.stim', 40),]*8 +
    [('densely_annotated_%02d.stim'%i, 60) for i in range(19)] * 2 +
    [('old_stim.stim', 40)]*25 +
    [('shuffle_reversed.stim', 40), ('shuffle_reversed_1st.stim', 40), ('shuffle_reversed_2nd.stim', 40),]*8
)  # 6200 seconds = 103 minutes, 20 secs
       
num_segments = len(segment_secs)

#build the stimulus array
frames_per_sec = 30.0
stim = []
total_movie_sec = 0

for file, length in segment_secs:
    segment = Stimulus.from_file(movie_path+file, window)
    segment_ds = [(total_movie_sec,total_movie_sec+length)]
    segment.set_display_sequence(segment_ds)
    
    total_movie_sec += length
    
    stim.append(segment)
    
params = {'syncsqr': True,
          'syncsqrloc': (875,550),
          'syncsqrsize': (150,150),
          'syncpulse': True,
          'syncpulseport': 1,
          'syncpulselines': [5, 6],  # frame, start/stop
          'trigger_delay_sec': 5.0}

# create SweepStim instance
ss = SweepStim(window,
               stimuli=stim,
               pre_blank_sec=2,
               post_blank_sec=2,
               params=params,
               )

# add in foraging so we can track wheel, potentially give rewards, etc
f = Foraging(window=window,
             auto_update=False,
             params=params,
             nidaq_tasks={'digital_input': ss.di,
                          'digital_output': ss.do,})  #share di and do with SS
ss.add_item(f, "foraging")

# run it
ss.run()
