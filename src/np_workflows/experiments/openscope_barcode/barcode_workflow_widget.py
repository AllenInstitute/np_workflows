import configparser
import contextlib
import copy
import enum
import functools
from typing import ClassVar, Literal, NamedTuple, NoReturn, Optional, TypedDict

import IPython.display
import ipywidgets as ipw
import np_config
import np_logging
import np_session
import np_workflows
from pyparsing import Any

from np_workflows.experiments.openscope_barcode.main_barcode_pilot import BarcodeSession

global_state = {}
"""Global variable for persisting widget states."""

# for widget, before creating a experiment --------------------------------------------- #

class SelectedSession:
    def __init__(self, session: str | BarcodeSession, mouse: str | int | np_session.Mouse):
        if isinstance(session, str):
            session = BarcodeSession(session)
        self.session = session
        self.mouse = str(mouse)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.session}, {self.mouse})"


def barcode_workflow_widget(
    mouse: str | int | np_session.Mouse,
) -> SelectedSession:
    """Select a stimulus session (hab, pretest, ephys) to run.

    An object with mutable attributes is returned, so the selected session can be
    updated along with the GUI selection. (Preference would be to return an enum
    directly, and change it's value, but that doesn't seem possible.)

    """

    selection = SelectedSession(BarcodeSession.PRETEST, mouse)

    session_dropdown = ipw.Select(
        options=tuple(_.value for _ in BarcodeSession),
        description="Session",
    )
    
    def update_selection():
        selection.__init__(str(session_dropdown.value), str(mouse))
        
    if (previously_selected_value := global_state.get('selected_session')):
        session_dropdown.value = previously_selected_value
        update_selection()
        
    console = ipw.Output()
    with console:
        if last_session := np_session.Mouse(selection.mouse).state.get('last_barcode_session'):
            print(f"{mouse} last session: {last_session}")
        print(f"Selected: {selection.session}")

    def update(change):
        if change["name"] != "value":
            return
        if (options := getattr(change["owner"], "options", None)) and change[
            "new"
        ] not in options:
            return
        if change["new"] == change["old"]:
            return
        update_selection()
        with console:
            print(f"Selected: {selection.session}")
        global_state['selected_session'] = selection.session.value
        
    session_dropdown.observe(update, names='value')

    IPython.display.display(ipw.VBox([session_dropdown, console]))

    return selection
