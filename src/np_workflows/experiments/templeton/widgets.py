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

from np_workflows.experiments.templeton.main import Ephys, Hab
from np_workflows.shared.base_experiments import DynamicRoutingExperiment

# for widget, before creating a experiment --------------------------------------------- #


class SelectedWorkflow:
    def __init__(self, workflow: str | DynamicRoutingExperiment.Workflow, mouse: str | int | np_session.Mouse):
        if isinstance(workflow, str):
            workflow = DynamicRoutingExperiment.Workflow[workflow] # uses enum name (not value)
        self.workflow = workflow
        self.mouse = str(mouse)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.workflow}, {self.mouse})"


def workflow_select_widget(
    mouse: str | int | np_session.Mouse,
) -> SelectedWorkflow:
    """Select a session type to run (hab, pretest, ephys).

    An object with mutable attributes is returned, so the selected session can be
    updated along with the GUI selection. (Preference would be to return an enum
    directly, and change it's value, but that doesn't seem possible.)

    """
    # set default
    selection = SelectedWorkflow(DynamicRoutingExperiment.Workflow.PRETEST, mouse)

    workflow_dropdown = ipw.Select(
        options=tuple(_.name for _ in DynamicRoutingExperiment.Workflow),
        description="Workflow",
    )
    workflow_descriptions = ipw.Select(
        options=tuple(_.value for _ in DynamicRoutingExperiment.Workflow),
        disabled=True,
        value=None
    )
    console = ipw.Output()
    with console:
        if last_workflow := np_session.Mouse(selection.mouse).state.get('last_workflow'):
            print(f"{mouse} last workflow: {last_workflow}")
        print(f"Selected: {selection.workflow.name}")

    def update(change):
        if change["name"] != "value":
            return
        if (options := getattr(change["owner"], "options", None)) and change[
            "new"
        ] not in options:
            return
        if change["new"] == change["old"]:
            return
        selection.__init__(str(workflow_dropdown.value), mouse.id if isinstance(mouse, np_session.Mouse) else str(mouse))
        with console:
            print(f"Selected: {selection.workflow}")
    workflow_dropdown.observe(update, names='value')

    IPython.display.display(ipw.VBox([ipw.HBox([workflow_dropdown, workflow_descriptions]), console]))

    return selection
    
    

def photodoc_widget(session: np_session.Session, reminder: str) -> None: 
    print(f'Take an image in Vimba Viewer and save as:\n{session.npexp_path.name}_{reminder}.png')