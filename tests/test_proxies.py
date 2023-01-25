import os
import pathlib
import tempfile

os.environ['USE_TEST_RIG'] = '0'
os.environ['AIBS_RIG_ID'] = 'NP.0'

from np_workflows.services import proxies, utils
from np_workflows.services import open_ephys as OpenEphys
from np_workflows.services import zro



with utils.debug_logging():
    if False:
        proxies.NewScaleCoordinateRecorder.log_root = pathlib.Path(tempfile.mkdtemp())
        proxies.NewScaleCoordinateRecorder.pretest()
    
    if False:
        with utils.stop_on_error(proxies.ImageMVR, reraise=False):
            proxies.ImageMVR.pretest()
            
    if False:
        with utils.stop_on_error(proxies.VideoMVR, reraise=False):
            proxies.VideoMVR.pretest()
        
    if True:
        OpenEphys.folder = 'test'
        with utils.stop_on_error(OpenEphys, reraise=False):
            OpenEphys.pretest()
        
    if False:
        with utils.stop_on_error(proxies.Sync, reraise=False):
            proxies.Sync.pretest()    
    
    if False:
        # proxies.NoCamstim.password  # careful not to commit this to github!
        proxies.NoCamstim.data_root = pathlib.Path('C:/ProgramData/camstim/output')
        proxies.NoCamstim.remote_file = pathlib.Path('C:/Users/svc_neuropix/Desktop/run_blue_opto.bat')
        proxies.NoCamstim.initialize() # will prompt for password if not entered
        proxies.NoCamstim.start()
    
    if False:
        proxies.ScriptCamstim.data_root = pathlib.Path('C:/ProgramData/camstim/output')
        proxies.ScriptCamstim.script =  'C:/Users/svc_neuropix/Desktop/optotagging/optotagging_sro.py'
        proxies.ScriptCamstim.params =  'C:/Users/svc_neuropix/Desktop/optotagging/experiment_params_blue.json'
        proxies.ScriptCamstim.initialize()
        proxies.ScriptCamstim.start()
        
    if False:
        proxies.SessionCamstim.initialize()
        proxies.SessionCamstim.lims_user_id = 'ben.hardcastle'
        proxies.SessionCamstim.labtracks_mouse_id = 598796
        proxies.SessionCamstim.start()
        
    quit()
    
    proxies.ImageMVR.host = proxies.VideoMVR.host = proxies.NewScaleCoordinateRecorder.host = 'w10dtsm18280'
    proxies.NewScaleCoordinateRecorder.initialize()
    proxies.NewScaleCoordinateRecorder.start()
    proxies.NewScaleCoordinateRecorder.start()
    # proxies.VideoMVR.initialize()
    # proxies.VideoMVR.test()
    # with utils.stop_on_error(VideoMVR):
    #     proxies.VideoMVR.start()
    #     time.sleep(VideoMVR.pretest_duration_sec)
    #     proxies.VideoMVR.verify()
    # proxies.VideoMVR.finalize()
    
    
    proxies.Camstim.get_proxy().session_output_path
    proxies.Camstim.get_proxy().start_session(mouse_id, user_id)
    proxies.Camstim.get_proxy().status['running']