import csv
import datetime
import glob
import inspect
import itertools
import json
import logging
import os
import pathlib
import pdb
import pickle
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import namedtuple
from datetime import date as date
from datetime import datetime as dt
from datetime import timedelta as timedelta
from functools import partial
from math import floor
from pprint import pprint
from shutil import copyfile, disk_usage

import numpy
import psutil
import requests
import yaml
import zmq
from np.models import model
# sys.path.append("..")
from np.services import \
    ephys_edi_pb2 as \
    ephys_messages  # ! TODO remove this - communicate through API instead
from np.services.config import Rig
from np.services.mvr import MVRConnector
from np.services.ephys_api import EphysHTTP as Ephys
from np.services.mtrain import MTrain
from PIL import Image
from wfltk import middleware_messages_pb2 as wfltk_msgs

messages = wfltk_msgs
import mpetk
from mpetk import limstk, mpeconfig, zro
from mpetk.zro import Proxy


config: dict
config = mpeconfig.source_configuration('neuropixels', version='1.4.0') 
#! #TODO line above is temporary, we want to consolidate config settings into one file 
# config.update(mpeconfig.source_configuration("dynamic_routing"))

config = mpeconfig.source_configuration('neuropixels_passive_experiment_workflow', version='1.4.0+g6c8db37.b73352')

with open('np/config/neuropixels.yml') as f:
    yconfig = yaml.safe_load(f)

config.update(yconfig)  

# global mvr, camstim_agent, sync, mouse_director, io, mtrain
mvr = camstim_agent = sync = mouse_director = io = mtrain = None

def fail_state(message: str, state: dict):
    """
    This is an example of how to create a single failure reporting function.  It inspects the stack to figure out which
    state is reporting the failure and fills out the necessary messaging.  This will post a message to the user and
    allow them to redo the state.

    :param message: the fail reason
    :param state: the state dictionary
    :return:
    """
    current_frame = inspect.currentframe()
    calling_frame = inspect.getouterframes(current_frame, 2)[1][3]
    state_name = calling_frame[:calling_frame.rfind("_")]

    #  These are values expected by the UI
    state['external']['alert'] = True
    state["external"]["transition_result"] = False
    state["external"]["next_state"] = state_name
    state["external"]["msg_text"] = message
    logging.warning(f"{state_name} failed: {message}")


def connect_to_services(state):
    """    The expectation is these services are on (i.e., you have started them with RSC).  If the connection fails you can
    send a message to the user with the fail_state() function above.
    """
    global mvr, camstim_agent, sync, mouse_director, io, mtrain

    component_errors = []
    io = state['resources']['io']

    # TODO Here we either need to test open ephys by trying to record or we get the status message.  awaiting testing.
    try:
        mvr = MVRConnector(args={"host": Rig.Mon.host, "port": 50000})
        if not mvr._mvr_connected:
            raise Exception
    except Exception:
        component_errors.append(f"Failed to connect to MVR.")

    try:
        camstim_agent = zro.Proxy(
            f"{Rig.Stim.host}:{5000}", timeout=1.0, serialization='json')
        logging.info(f'Camstim Agent Uptime: {camstim_agent.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Camstim Agent.")

    try:
        sync = zro.Proxy(
            f"{Rig.Sync.host}:{5000}", timeout=1.0)
        logging.info(f'Sync Uptime: {sync.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to Sync.")

    try:
        mouse_director = zro.Proxy(
            f"{Rig.Mon.host}:{9000}", timeout=1.0)
        logging.info(f'MouseDirector Uptime: {mouse_director.uptime}')
    except Exception:
        component_errors.append(f"Failed to connect to MouseDirector.")
        # TODO MDir currently not working - switch this line back on when fixes
        # logging.info(" ** skipping connection to MouseDirector **")

    # mtrain var isn't created until we have a mouse_id
    if not MTrain.connected():
        component_errors.append(f"Failed to connect to MTrain.")
 
    # TODO
    # if not LIMS.connected():
    #     component_errors.append(f"Failed to connect to LIMS.")
        
    #  At this point, You could send the user to an informative state to repair the remote services.
    if component_errors:
        fail_state('\n'.join(component_errors), state)
        

def set_mouse_id(state):
    # add barcode scanner
    
    # external contains values coming from the UI.  "mouse_id" is a key specified in the wfl file.
    mouse_id = state["external"]["mouse_id"]
    
    try:
        mouse_result = limstk.donor_info_with_parent(mouse_id)
    except:
        # mouse_result not returned if prev limstk query errors
        fail_state(f"Could not find mouse id \"{mouse_id}\" in LIMS", state)
        return
        
    #  You might want to get data about this mouse from MTRain
    try:
        global mtrain
        mtrain = MTrain(mouse_id)
    except ValueError:
        fail_state(f"Could not find mouse id \"{mouse_id}\" in MTrain", state)
        return
    global mouse_director
    mouse_director.set_mouse_id(mouse_id)
    
        
def set_user_id(state):
    user_name = state["external"]["user_id"]
    if not limstk.user_details(user_name):
        fail_state(f"Could not find user \"{user_name}\" in LIMS", state)
    mouse_director.set_user_id(user_name)
    
    