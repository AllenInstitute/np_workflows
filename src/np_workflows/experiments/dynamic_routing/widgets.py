import shutil
import time

import IPython.display
import ipywidgets as ipw
import np_config
import np_services
import np_session

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


def photodoc_widget(session: np_session.Session, reminder: str) -> None:
    vimba_dir = np_config.local_to_unc(
        session.rig.mon, np_services.config_from_zk()["ImageVimba"]["data"]
    )
    def get_file_stats():
        return {p: p.stat().st_mtime for p in vimba_dir.iterdir() if p.is_file()}
    
    original_file_stats = get_file_stats()
    
    print(
        f"Take an image in Vimba Viewer and save it {vimba_dir} with any name + .png suffix."
        f"\n\nThis cell will wait for a new file or an existing file to be modified, then copy it as {reminder!r}*"
    )
    t0 = time.time()
    timeout_s = 120
    while True:
        time.sleep(1)
        new_files = len(new_file_stats := get_file_stats()) != len(original_file_stats)
        modified_files = any(
            p for p in new_file_stats if new_file_stats[p] > original_file_stats.get(p, 0)
        )
        if new_files or modified_files:
            break
        
        if time.time() - t0 > timeout_s:
            raise TimeoutError(
                f"No new image file detected in Vimba folder after {timeout_s} seconds - aborting"
            )
    latest_image = max(new_file_stats, key=lambda k: new_file_stats[k])
    print(f"New file detected:\n\t{latest_image.name}\nCopying to session folder")
    new_name = f"{session.npexp_path.name}_{reminder}.png"
    shutil.copy2(latest_image, session.npexp_path / new_name)
    npxc.validate_or_overwrite(session.npexp_path / new_name, latest_image)
    print("Done!")
