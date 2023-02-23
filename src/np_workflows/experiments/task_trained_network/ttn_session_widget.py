import configparser
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

from .ttn_stim_config import TTNSession

# for widget, before creating a experiment --------------------------------------------- #


class TTNSelectedSession:
    def __init__(self, session: str | TTNSession, mouse: str | int | np_session.Mouse):
        if isinstance(session, str):
            session = TTNSession(session)
        self.session = session
        self.mouse = str(mouse)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.session}, {self.mouse})"


def stim_session_select_widget(
    mouse: str | int | np_session.Mouse,
) -> TTNSelectedSession:
    """Select a stimulus session (hab, pretest, ecephys) to run.

    An object with mutable attributes is returned, so the selected session can be
    updated along with the GUI selection. (Preference would be to return an enum
    directly, and change it's value, but that doesn't seem possible.)

    """

    selection = TTNSelectedSession(TTNSession.PRETEST, mouse)

    session_dropdown = ipw.Select(
        options=tuple(_.value for _ in TTNSession),
        description="Session",
    )
    console = ipw.Output()
    with console:
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
        selection.__init__(str(session_dropdown.value), mouse)
        with console:
            print(f"Selected: {selection.session}")

    session_dropdown.observe(update)

    IPython.display.display(ipw.VBox([session_dropdown, console]))

    return selection
