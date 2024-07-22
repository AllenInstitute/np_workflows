# -*- coding: utf-8 -*-
"""
optotagging.py

runs optotagging code for ecephys pipeline experiments

by joshs@alleninstitute.org

(c) 2018 Allen Institute for Brain Science

"""
import camstim  # ensures "magic" gets setup properly by importing first
import logging  # must occur after camstim import for "magic"
from camstim.zro import agent

import numpy as np
from toolbox.IO.nidaq import AnalogOutput
from toolbox.IO.nidaq import DigitalOutput

import datetime
import numpy as np
import time
import pickle as pkl


# %%


def run_optotagging(levels, conditions, waveforms, isis, sampleRate=10000.):

    from toolbox.IO.nidaq import AnalogOutput
    from toolbox.IO.nidaq import DigitalOutput

    sweep_on = np.array([0, 0, 1, 0, 0, 0, 0, 0], dtype=np.uint8)
    stim_on = np.array([0, 0, 1, 1, 0, 0, 0, 0], dtype=np.uint8)
    stim_off = np.array([0, 0, 1, 0, 0, 0, 0, 0], dtype=np.uint8)
    sweep_off = np.array([0, 0, 0, 0, 0, 0, 0, 0], dtype=np.uint8)

    ao = AnalogOutput('Dev1', channels=[1])
    ao.cfg_sample_clock(sampleRate)

    do = DigitalOutput('Dev1', 2)

    do.start()
    ao.start()

    do.write(sweep_on)
    time.sleep(5)

    for i, level in enumerate(levels):

        print(level)

        data = waveforms[conditions[i]]

        do.write(stim_on)
        ao.write(data * level)
        do.write(stim_off)
        time.sleep(isis[i])

    do.write(sweep_off)
    do.clear()
    ao.clear()

# %%


def generatePulseTrain(pulseWidth, pulseInterval, numRepeats, riseTime, sampleRate=10000.):

    data = np.zeros((int(sampleRate),), dtype=np.float64)
   # rise_samples =

    rise_and_fall = (
        ((1 - np.cos(np.arange(sampleRate*riseTime/1000., dtype=np.float64)*2*np.pi/10))+1)-1)/2
    half_length = rise_and_fall.size / 2
    rise = rise_and_fall[:half_length]
    fall = rise_and_fall[half_length:]

    peak_samples = int(sampleRate*(pulseWidth-riseTime*2)/1000)
    peak = np.ones((peak_samples,))

    pulse = np.concatenate((rise,
                            peak,
                            fall))

    interval = int(pulseInterval*sampleRate/1000.)

    for i in range(0, numRepeats):
        data[i*interval:i*interval+pulse.size] = pulse

    return data


# %% create waveforms

def optotagging(mouseID, operation_mode='experiment', level_list=[1.15, 1.28, 1.345], genotype=None):

    sampleRate = 10000

    # 1 s cosine ramp:
    data_cosine = (((1 - np.cos(np.arange(sampleRate, dtype=np.float64)
                                * 2*np.pi/sampleRate)) + 1) - 1)/2  # create raised cosine waveform

    # 1 ms cosine ramp:
    rise_and_fall = (
        ((1 - np.cos(np.arange(sampleRate*0.001, dtype=np.float64)*2*np.pi/10))+1)-1)/2
    half_length = rise_and_fall.size / 2

    # pulses with cosine ramp:
    pulse_2ms = np.concatenate((rise_and_fall[:half_length], np.ones(
        (int(sampleRate*0.001),)), rise_and_fall[half_length:]))
    pulse_5ms = np.concatenate((rise_and_fall[:half_length], np.ones(
        (int(sampleRate*0.004),)), rise_and_fall[half_length:]))
    pulse_10ms = np.concatenate((rise_and_fall[:half_length], np.ones(
        (int(sampleRate*0.009),)), rise_and_fall[half_length:]))

    data_2ms_10Hz = np.zeros((sampleRate,), dtype=np.float64)

    for i in range(0, 10):
        interval = sampleRate / 10
        data_2ms_10Hz[i*interval:i*interval+pulse_2ms.size] = pulse_2ms

    data_5ms = np.zeros((sampleRate,), dtype=np.float64)
    data_5ms[:pulse_5ms.size] = pulse_5ms

    data_10ms = np.zeros((sampleRate,), dtype=np.float64)
    data_10ms[:pulse_10ms.size] = pulse_10ms

    data_10s = np.zeros((sampleRate*10,), dtype=np.float64)
    data_10s[:-2] = 1

    # %% for experiment

    isi = 1.5
    isi_rand = 0.5
    numRepeats = 50

    condition_list = [2, 3]
    waveforms = [data_2ms_10Hz, data_5ms, data_10ms, data_cosine]

    opto_levels = np.array(level_list*numRepeats*len(condition_list))  # BLUE
    opto_conditions = condition_list*numRepeats*len(level_list)
    opto_conditions = np.sort(opto_conditions)
    opto_isis = np.random.random(opto_levels.shape) * isi_rand + isi

    p = np.random.permutation(len(opto_levels))

    # implement shuffle?
    opto_levels = opto_levels[p]
    opto_conditions = opto_conditions[p]

    # %% for testing

    if operation_mode == 'test_levels':
        isi = 2.0
        isi_rand = 0.0

        numRepeats = 2

        condition_list = [0]
        waveforms = [data_10s, data_10s]

        opto_levels = np.array(level_list*numRepeats *
                               len(condition_list))  # BLUE
        opto_conditions = condition_list*numRepeats*len(level_list)
        opto_conditions = np.sort(opto_conditions)
        opto_isis = np.random.random(opto_levels.shape) * isi_rand + isi

    elif operation_mode == 'pretest':
        numRepeats = 1

        condition_list = [0]
        data_2s = data_10s[-sampleRate*2:]
        waveforms = [data_2s]

        opto_levels = np.array(level_list*numRepeats *
                               len(condition_list))  # BLUE
        opto_conditions = condition_list*numRepeats*len(level_list)
        opto_conditions = np.sort(opto_conditions)
        opto_isis = [1]*len(opto_conditions)
    # %%

    outputDirectory = agent.OUTPUT_DIR
    fileDate = str(datetime.datetime.now()).replace(':', '').replace(
        '.', '').replace('-', '').replace(' ', '')[2:14]
    fileName = outputDirectory  + "/" + fileDate + '_'+mouseID + '.opto.pkl'

    print('saving info to: ' + fileName)
    fl = open(fileName, 'wb')
    output = {}

    output['opto_levels'] = opto_levels
    output['opto_conditions'] = opto_conditions
    output['opto_ISIs'] = opto_isis
    output['opto_waveforms'] = waveforms

    pkl.dump(output, fl)
    fl.close()
    print('saved.')

    # %%
    run_optotagging(opto_levels, opto_conditions,
                    waveforms, opto_isis, float(sampleRate))


# %%
if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('json_params', type=str, )
    args, _ = parser.parse_known_args()

    with open(args.json_params, 'r', ) as f:
        json_params = json.load(f)

    logging.info('Optotagging with params: %s' % json_params)
    optotagging(**json_params)
