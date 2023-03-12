import os
import pathlib
import tempfile
import time

os.environ["USE_TEST_RIG"] = "0"
os.environ["AIBS_RIG_ID"] = "NP.0"

import np_logging
import np_workflows
import np_services 

logger = np_logging.getLogger()

# careful not to leave this in.. will generate a new lims session every time tests run 
# user, mouse = 'ben.hardcastle', 366122
# experiment = np_workflows.Hab(mouse, user)
# experiment = np_workflows.Ephys(mouse, user)

import np_workflows.experiments.task_trained_network