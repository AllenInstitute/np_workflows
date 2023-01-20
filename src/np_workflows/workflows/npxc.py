import inspect

import np_logging

logger = np_logging.getLogger(__name__)

def current_state() -> tuple[str, str]:
    "State name (before last underscore) and transition type (enter, input, exit)"
    current_frame = inspect.currentframe()
    calling_frame = inspect.getouterframes(current_frame, 2)[1][3] #?
    return calling_frame[: calling_frame.rfind("_")], calling_frame[calling_frame.rfind("_") + 1 :]