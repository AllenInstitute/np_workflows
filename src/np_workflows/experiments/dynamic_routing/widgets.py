import shutil
import time
from typing import Literal

import IPython.display
import ipywidgets as ipw
import np_config
import np_services
import np_session
from pydantic import BaseModel, Field, field_validator



import np_workflows.shared.npxc as npxc
from np_workflows.shared.base_experiments import DynamicRoutingExperiment

# for widget, before creating a experiment --------------------------------------------- #


class SelectedWorkflow:
    def __init__(
        self,
        workflow: str | DynamicRoutingExperiment.Workflow,
        mouse: str | int | np_session.Mouse,
    ):
        if isinstance(workflow, str):
            workflow = DynamicRoutingExperiment.Workflow[
                workflow
            ]  # uses enum name (not value)
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
        value=None,
    )
    console = ipw.Output()
    with console:
        if last_workflow := np_session.Mouse(selection.mouse).state.get(
            "last_workflow"
        ):
            print(
                f"{mouse} last workflow: {last_workflow}\t({np_session.Mouse(selection.mouse).state.get('last_session')})"
            )
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
        selection.__init__(
            str(workflow_dropdown.value),
            mouse.id if isinstance(mouse, np_session.Mouse) else str(mouse),
        )
        with console:
            print(f"Selected: {selection.workflow}")

    workflow_dropdown.observe(update, names="value")

    IPython.display.display(
        ipw.VBox([ipw.HBox([workflow_dropdown, workflow_descriptions]), console])
    )

    return selection


def photodoc_widget(experiment: DynamicRoutingExperiment, reminder: str) -> None:
    vimba_dir = np_config.local_to_unc(
        experiment.rig.mon, np_services.config_from_zk()["ImageVimba"]["data"]
    )

    def get_file_stats():
        return {p: p.stat().st_mtime for p in vimba_dir.iterdir() if p.is_file()}

    original_file_stats = get_file_stats()

    timeout_s = 300
    print(
        f"Take an image in Vimba Viewer and save it in {vimba_dir} with any name and .png suffix."
        f"\n\nThis cell will wait for a new file or an existing file to be modified ({timeout_s = })\n"
    )
    t0 = time.time()
    while True:
        time.sleep(1)
        new_files = len(new_file_stats := get_file_stats()) != len(original_file_stats)
        modified_files = any(
            p
            for p in new_file_stats
            if new_file_stats[p] > original_file_stats.get(p, 0)
        )
        if new_files or modified_files:
            break

        if time.time() - t0 > timeout_s:
            raise TimeoutError(
                f"No new image file detected in Vimba folder after {timeout_s} seconds - aborting"
            )
    latest_image = max(new_file_stats, key=lambda k: new_file_stats[k])
    dest = (
        experiment.session.npexp_path
        / f"{experiment.session.npexp_path.name}_{reminder}{latest_image.suffix}"
    )
    print(f"New file detected:\n\t{latest_image.name}\nCopying to:\n\t{dest}")
    shutil.copy2(latest_image, dest)
    npxc.validate_or_overwrite(dest, latest_image)
    print("Done!")



class SessionConfigMetadata(BaseModel):
    """Container for session configuration metadata."""

    is_split_recording: bool = Field(
        default=False, description="If True, do not upload this recording"
    )
    project: Literal["dynamic_routing", "templeton"] = Field(
        default="dynamic_routing", description="Project name"
    )
    ephys_day: int = Field(default=1, ge=1, le=9, description="Ephys day number (1-9)")
    context_naive: bool = Field(
        default=False, description="Whether the session is context naive"
    )
    probes_to_skip: str = Field(
        default="", description="Probe letters to skip (all caps, e.g., ABCDEF)"
    )
    deep_insertion: bool = Field(
        default=False, description="Whether this is a deep insertion"
    )
    surface_recording_probe_letters_to_skip: str = Field(
        default="", description="Probe letters to skip for surface recording (all caps)"
    )
    prod_dev: Literal["prod", "dev"] = Field(
        default="prod",
        description="prod: quality data for datasets; dev: experimental/one-off data",
    )

    model_config = {"validate_assignment": True}

    @field_validator("probes_to_skip", "surface_recording_probe_letters_to_skip")
    @classmethod
    def validate_uppercase(cls, v: str) -> str:
        """Ensure probe letters are uppercase."""
        return v.upper()


def session_config_metadata_widget() -> SessionConfigMetadata:
    """Create a widget for configuring session metadata.

    Returns an object with mutable attributes that update along with the GUI selections.
    """
    # Initialize with defaults
    config = SessionConfigMetadata()

    # Create widgets
    is_split_recording_checkbox = ipw.Checkbox(
        value=False,
        description="Is split recording",
        tooltip="Check this if the recording should NOT be uploaded",
        style={"description_width": "initial"},
    )
    split_recording_info = ipw.Label(
        value="(do not upload)", layout=ipw.Layout(margin="0 0 0 20px")
    )

    project_dropdown = ipw.Dropdown(
        options=["dynamic_routing", "templeton"],
        value="dynamic_routing",
        description="Project:",
        style={"description_width": "initial"},
    )

    ephys_day_slider = ipw.IntSlider(
        value=1,
        min=1,
        max=9,
        step=1,
        description="Ephys day:",
        style={"description_width": "initial"},
    )

    context_naive_checkbox = ipw.Checkbox(
        value=False, description="Context naive", style={"description_width": "initial"}
    )

    probes_to_skip_text = ipw.Text(
        value="",
        description="Probes to skip:",
        placeholder="ABCDEF (all caps)",
        tooltip="Enter probe letters to skip in all caps (e.g., ABCDEF)",
        style={"description_width": "initial"},
    )

    deep_insertion_checkbox = ipw.Checkbox(
        value=False,
        description="Deep insertion",
        style={"description_width": "initial"},
    )

    surface_recording_text = ipw.Text(
        value="",
        description="Surface recording probe letters to skip:",
        placeholder="ABCDEF (all caps)",
        tooltip="Enter probe letters to skip for surface recording in all caps",
        style={"description_width": "initial"},
        layout=ipw.Layout(width="500px"),
    )

    prod_dev_dropdown = ipw.Dropdown(
        options=["prod", "dev"],
        value="prod",
        description="Prod/Dev:",
        tooltip="prod: quality data for datasets | dev: experimental/one-off data",
        style={"description_width": "initial"},
    )
    prod_dev_info = ipw.Label(
        value="(prod: quality data for datasets; dev: experimental/one-off)",
        layout=ipw.Layout(margin="0 0 0 20px"),
    )

    console = ipw.Output()

    with console:
        print(f"Current config: {config}")

    # Update functions
    def update_config(*args):
        config.is_split_recording = is_split_recording_checkbox.value
        config.project = project_dropdown.value
        config.ephys_day = ephys_day_slider.value
        config.context_naive = context_naive_checkbox.value
        config.probes_to_skip = probes_to_skip_text.value.upper()
        config.deep_insertion = deep_insertion_checkbox.value
        config.surface_recording_probe_letters_to_skip = (
            surface_recording_text.value.upper()
        )
        config.prod_dev = prod_dev_dropdown.value

        # Update text fields to show uppercase
        if probes_to_skip_text.value != probes_to_skip_text.value.upper():
            probes_to_skip_text.value = probes_to_skip_text.value.upper()
        if surface_recording_text.value != surface_recording_text.value.upper():
            surface_recording_text.value = surface_recording_text.value.upper()

        with console:
            console.clear_output()
            print(f"Updated config: {config}")

    # Attach observers
    is_split_recording_checkbox.observe(update_config, names="value")
    project_dropdown.observe(update_config, names="value")
    ephys_day_slider.observe(update_config, names="value")
    context_naive_checkbox.observe(update_config, names="value")
    probes_to_skip_text.observe(update_config, names="value")
    deep_insertion_checkbox.observe(update_config, names="value")
    surface_recording_text.observe(update_config, names="value")
    prod_dev_dropdown.observe(update_config, names="value")

    # Layout
    widget = ipw.VBox(
        [
            ipw.HBox([is_split_recording_checkbox, split_recording_info]),
            project_dropdown,
            ephys_day_slider,
            context_naive_checkbox,
            probes_to_skip_text,
            deep_insertion_checkbox,
            surface_recording_text,
            ipw.HBox([prod_dev_dropdown, prod_dev_info]),
            console,
        ]
    )

    IPython.display.display(widget)

    return config
