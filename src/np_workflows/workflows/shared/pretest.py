try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    from np_workflows.workflows.shared import npxc

    import np_logging
    
except Exception as exc:
    print(repr(exc))
    import pdb; pdb.set_trace()
    quit()

logger = np_logging.getLogger(__name__)

def run_pretest_input(state_globals) -> None:
    "All services should be initialized already."
    npxc.experiment.pretest_services()
    
