import pdb

# pdb.set_trace()
try:
    import inspect
    import time
    import socket
    import json
    import logging
    import os
    #
    # limstk functions are auto-generated so the ide might warn it can't see them.
    import shutil
    import threading
    from time import sleep

    # import mpetk
    import mpetk.aibsmw.routerio.router as router
    import requests
    import yaml
    from mpetk import limstk, mpeconfig, zro
    # from np.models import model
    from np.models.model import \
        Illusion as Experiment # It can make sense to have a class to store experiment data.
    from np.services.ephys_api import EphysHTTP as ephys
    from np.services.mvr import \
        MVRConnector  # This will eventually get incorporated into the workflow launcher
    from wfltk import middleware_messages_pb2 as messages

    # import np.workflows.npxcommon as npxc
    import np.workflows.npxc as npxc
    
except Exception as e:
    # import errors aren't printed to console by default
    print(e)



def initialize_enter(state):
    # pdb.set_trace()
    state['external']["logo"] = R"C:\progra~1\AIBS_MPE\workflow_launcher\np\images\logo_np_vis.png"
    state["external"]["user_id"] = "ben.hardcastle"
    state["external"]["mouse_id"] = "366122"
    npxc.connect_to_services(state)

    
def initialize_input(state):
    npxc.set_user_id(state)
    npxc.set_mouse_id(state)

def initialize_exit(state):
    pass
    
def mtrain_enter(state):
    state["external"]["current_regimen"] = npxc.mtrain.regimen['name']
    state["external"]["current_stage"] = npxc.mtrain.stage['name'].title()
    state["external"]["available_stages"] = sorted([stage['name'].title() for stage in npxc.mtrain.stages])
    state["external"]["available_regimens"] = sorted([regimen.title() for regimen in npxc.mtrain.all_regimens().values()])
    state["external"]["selected_stage_current_regimen"] = 
    pdb.set_trace()
    
def mtrain_input(state):
    selected_stage_current = state["external"]["selected_stage_current_regimen"]
    selected_stage_any = state["external"]["selected_regimen"]
    illusion_regimens = {key:val for key,val in npxc.MTrain.all_regimens().items() if 'Illusion' in val}
    # npxc.mtrain.stage = state["external"]["selected_stage"]
    pass

def run_stimulus_input(state):
    npxc.camstim_agent.start_session(state["external"]["mouse_id"], state["external"]["user_id"])

def wrap_up_input(state):
    pass