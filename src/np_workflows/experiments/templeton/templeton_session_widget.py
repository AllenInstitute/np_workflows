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

from .templeton_stim_config import TempletonSession

# for widget, before creating a experiment --------------------------------------------- #


class TempletonSelectedSession:
    def __init__(self, session: str | TempletonSession, mouse: str | int | np_session.Mouse):
        if isinstance(session, str):
            session = TempletonSession(session)
        self.session = session
        self.mouse = str(mouse)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.session}, {self.mouse})"


def stim_session_select_widget(
    mouse: str | int | np_session.Mouse,
) -> TempletonSelectedSession:
    """Select a stimulus session (hab, pretest, ephys) to run.

    An object with mutable attributes is returned, so the selected session can be
    updated along with the GUI selection. (Preference would be to return an enum
    directly, and change it's value, but that doesn't seem possible.)

    """

    selection = TempletonSelectedSession(TempletonSession.PRETEST, mouse)

    session_dropdown = ipw.Select(
        options=tuple(_.value for _ in TempletonSession),
        description="Session",
    )
    console = ipw.Output()
    with console:
        if last_session := np_session.Mouse(selection.mouse).state.get('last_ttn_session'):
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
        selection.__init__(str(session_dropdown.value), mouse.id if isinstance(mouse, np_session.Mouse) else str(mouse))
        with console:
            print(f"Selected: {selection.session}")
    session_dropdown.observe(update, names='value')

    IPython.display.display(ipw.VBox([session_dropdown, console]))

    return selection
