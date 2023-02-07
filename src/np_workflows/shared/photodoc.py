from __future__ import annotations

try:
    #! the wse allows import errors to pass silently!
    #* put all imports in this try block so that we can see the error before exiting
    
    import datetime
    import time
    from typing import Type

    from np_workflows.services.protocols import Startable, Stoppable, Finalizable
    from np_workflows.experiments import baseclasses, classes
    from np_workflows.experiments.baseclasses import Experiment
    from np_workflows.workflows.shared import npxc

    # we need all experiment classes imported into this namespace
    from np_workflows.experiments.classes import *

    import yaml
    import np_session
    import np_logging

except Exception as exc:
    print(repr(exc))
    import pdb; pdb.set_trace()
    quit()

logger = np_logging.getLogger(__name__)

# def capture_photodoc_enter(state_globals) -> None:
#     if not npxc.get(state_globals, "photodoc_labels"):
#         npxc.set(state_globals, photodoc_labels=[""] + list(npxc.experiment.photodoc_labels))
#     if (selected_label := npxc.get(state_globals, "photodoc_label")) and selected_label not in (dropdown_labels := npxc.get(state_globals, "photodoc_labels")):
#         npxc.set(state_globals, photodoc_labels=[selected_label] + dropdown_labels)
        
def capture_photodoc_input(state_globals) -> None:
    current_label = npxc.get(state_globals, "photodoc_label")
    for service in npxc.experiment.photodoc_services:
        service.label = current_label
        if isinstance(service, Startable):
            service.start()
        if isinstance(service, Stoppable):
            service.stop()
        if isinstance(service, Finalizable):
            service.finalize()
                
def review_photodoc_enter(state_globals) -> None:
    # default to re-taking the photodoc

    for service in npxc.experiment.photodoc_services:
        img = None
        with contextlib.suppress(AttributeError, IndexError):
            img = service.data_files[-1]
        if not img:
            continue
        if img.suffix in ('.jpg', '.jpeg', '.bmp', '.png'):
            npxc.set(state_globals, current_image=str(img))
            break
    else:
        import pdb; pdb.set_trace()
        raise ValueError(f"No photodoc image found from services {[_.__name__ for _ in npxc.experiment.photodoc_services]}")
    