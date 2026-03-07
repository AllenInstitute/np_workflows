from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import time
from typing import Any, Literal, Optional, Union, get_args, get_origin

import IPython.display
import ipywidgets as ipw
import np_config
import np_services
import np_session
import pydantic
import yaml

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


# session config ----------------------------------------------------------------------- #

_DR = "//allen/programs/mindscope/workgroups/dynamicrouting/PilotEphys/Task 2 pilot"
_TEMPLETON = "//allen/programs/mindscope/workgroups/templeton/TTOC/pilot recordings"
PROJECT_PATHS: dict[str, str] = {
    "DynamicRouting": _DR,
    "TempletonPilotSession": _TEMPLETON,
}

EPHYS = pathlib.Path(PROJECT_PATHS["DynamicRouting"])


def get_session_config_path(folder: str, project: str = "DynamicRouting") -> pathlib.Path:
    return pathlib.Path(PROJECT_PATHS[project]) / folder / "session_config.json"


class Config(pydantic.BaseModel):
    folder: str
    project: Literal["DynamicRouting", "TempletonPilotSession"] = pydantic.Field(
        default="DynamicRouting",
        description="Project name: DynamicRouting or TempletonPilotSession",
    )
    session_type: Literal["ephys", "behavior_with_sync"] = pydantic.Field(
        default="ephys", description="Type of session: ephys or behavior_with_sync"
    )
    ephys_day: Optional[int] = pydantic.Field(
        default=None, description="Day of ephys recording (starting at 1)", gt=0
    )
    perturbation_day: Optional[int] = pydantic.Field(
        default=None,
        description="Day of opto or injection perturbation (starting at 1)",
        gt=0,
    )
    is_production: bool = pydantic.Field(
        default=True,
        description="Production quality data; experimental variants are ok (False: dev testing, training operators)",
    )
    is_split_recording: bool = pydantic.Field(
        default=False,
        description="Split recording session: will not be uploaded yet (recordings to be concatenated later)",
    )
    is_context_naive: bool = pydantic.Field(
        default=False,
        description="Subject was not trained on stage 3 before first experiment",
    )
    is_injection_perturbation: bool = pydantic.Field(
        default=False, description="Injection perturbation or control session"
    )
    is_opto_perturbation: bool = pydantic.Field(
        default=False, description="Optogenetic perturbation or control session"
    )
    is_deep_insertion: bool = pydantic.Field(
        default=False,
        description="At least one probe has a surface channel recording",
    )
    probe_letters_to_skip: Optional[str] = pydantic.Field(
        default="",
        description="Probe letters to skip from upload/processing (e.g. 'ABC', [A-F], max 6 chars). Not necessary to list probes that were disabled in Open Ephys",
    )
    surface_recording_probe_letters_to_skip: Optional[str] = pydantic.Field(
        default="",
        description="Probe letters to skip from surface channel processing (e.g. 'ABC', [A-F], max 6 chars). Not necessary to list probes that were disabled in Open Ephys",
    )

    @pydantic.field_validator(
        "probe_letters_to_skip",
        "surface_recording_probe_letters_to_skip",
        mode="before",
    )
    def cast_to_upper_case(cls, v):
        return v.upper() if isinstance(v, str) else v

    @pydantic.field_validator(
        "probe_letters_to_skip",
        "surface_recording_probe_letters_to_skip",
        mode="after",
    )
    def validate_probe_letters(cls, v):
        if v and not re.fullmatch(r"[A-F]{0,6}", v):
            raise ValueError("Probe letters must be A-F only, up to 6 characters")
        return v

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        session_type = data.pop("session_type")
        project = data.pop("project")
        folder = data.pop("folder")
        return {
            session_type: {
                project: [
                    {
                        f"{PROJECT_PATHS[project]}/{folder}": {
                            "ephys_day": self.ephys_day,
                            "session_kwargs": {
                                k: v
                                for k, v in data.items()
                                if v is not None
                                and v != self.model_fields[k].default
                                and k not in ("ephys_day", "perturbation_day")
                            },
                        }
                    }
                ]
            }
        }

    def to_yaml_text_snippet(self) -> str:
        d = self.to_dict()
        indent = " " * 4
        session_dir_parent = PROJECT_PATHS[self.project] + "/"
        s = f"\n{indent}- {session_dir_parent}{self.folder}:"
        for attr in (
            "ephys_day",
            "perturbation_day",
        ):
            if value := getattr(self, attr, None):
                s = s + "\n" + indent * 2 + f"{attr}: {value}"
        session_kwargs = next(
            iter(next(iter(d[self.session_type][self.project])).values())
        )["session_kwargs"]
        if session_kwargs:
            s = s + "\n" + indent * 2 + "session_kwargs:"
            for k, v in session_kwargs.items():
                s = s + "\n" + indent * 3 + f"{k}: {v}"
        if s.endswith(":"):
            s = s[:-1]
        s = s.replace("\n\n", "\n")
        return (
            s
            + "\n"
            + (
                indent
                if (self.project == "DynamicRouting" and self.session_type == "ephys")
                else ""
            )
        )



class SessionConfigRow:
    """Widget row for a single session's configuration."""

    placeholders = {
        "probe_letters_to_skip": "e.g. AF",
        "surface_recording_probe_letters_to_skip": "e.g. AF",
        "ephys_day": "starting at 1, or empty if no ephys",
        "perturbation_day": "starting at 1, or empty if no perturbation",
    }

    @staticmethod
    def _is_bool_field(field) -> bool:
        """Check if a pydantic field is a boolean type."""
        if field.annotation is bool:
            return True
        if get_origin(field.annotation) is Union:
            return bool in get_args(field.annotation)
        return False

    @staticmethod
    def _is_literal_field(field) -> bool:
        """Check if a pydantic field is a Literal type."""
        return get_origin(field.annotation) is Literal

    @staticmethod
    def _make_description_label(description: str | None) -> ipw.HTML:
        """Create a small italic caption from a field description."""
        text = description or ""
        return ipw.HTML(
            value=f'<span style="color: #888; font-size: 0.85em; font-style: italic;">{text}</span>',
            layout=ipw.Layout(margin="0 0 4px 160px"),
        )

    def __init__(self, data: dict[str, Any]):
        self.session_folder = data["folder"]
        config_path = get_session_config_path(self.session_folder)
        if config_path.exists():
            saved = json.loads(config_path.read_text())
            data = {**data, **saved}
        self.config = Config(**data)
        self.widgets = {}  # field_name -> (input_widget, description_label) or HTML for folder

        for name, field in self.config.model_fields.items():
            if name == "folder":
                self.widgets[name] = (
                    ipw.HTML(
                        value=f"<b>{getattr(self.config, name)}</b>",
                        layout=ipw.Layout(width="400px"),
                    ),
                    None,
                )
            elif self._is_bool_field(field):
                current_value = getattr(self.config, name)
                if current_value is None:
                    current_value = True
                self.widgets[name] = (
                    ipw.Dropdown(
                        description=name,
                        options=[("True", True), ("False", False)],
                        value=current_value,
                        tooltip=field.description or name,
                        layout=ipw.Layout(width="500px"),
                        style={"description_width": "initial"},
                    ),
                    self._make_description_label(field.description),
                )
            elif self._is_literal_field(field):
                options = list(getattr(field.annotation, "__args__", []))
                current_value = getattr(self.config, name)
                self.widgets[name] = (
                    ipw.Dropdown(
                        description=name,
                        options=options,
                        value=current_value,
                        tooltip=field.description or name,
                        layout=ipw.Layout(width="500px"),
                        style={"description_width": "initial"},
                    ),
                    self._make_description_label(field.description),
                )
            else:
                self.widgets[name] = (
                    ipw.Text(
                        description=name,
                        placeholder=self.placeholders.get(name, ""),
                        tooltip=field.description or name,
                        continuous_update=True,
                        layout=ipw.Layout(width="500px"),
                        value=(
                            str(getattr(self.config, name))
                            if getattr(self.config, name) is not None
                            else ""
                        ),
                        style={"description_width": "initial"},
                    ),
                    self._make_description_label(field.description),
                )

        self._setup_autosave()

    def get_config(self) -> Config:
        """Get Config object from current widget values."""
        data = {}
        for name, (widget, _label) in self.widgets.items():
            if isinstance(widget, ipw.HTML):
                data[name] = self.session_folder
            elif isinstance(widget, ipw.Dropdown):
                data[name] = widget.value
            else:
                data[name] = widget.value if widget.value != "" else None
        return Config(**data)

    def save_to_session_folder(self) -> pathlib.Path:
        """Save current config as JSON to the session folder."""
        config = self.get_config()
        path = get_session_config_path(self.session_folder)
        path.write_text(json.dumps(config.model_dump(), indent=2))
        return path

    def _setup_autosave(self) -> None:
        """Observe all input widgets and autosave on any change."""
        self.status_label = ipw.HTML(value="")

        def _autosave(change):
            try:
                self.save_to_session_folder()
                self.status_label.value = ""
            except pydantic.ValidationError as e:
                msgs = "; ".join(err["msg"] for err in e.errors())
                self.status_label.value = f'<span style="color: red;">{msgs}</span>'
            except FileNotFoundError:
                self.status_label.value = '<span style="color: orange;">Session folder not found on network drive — config not saved</span>'

        for widget, _label in self.widgets.values():
            if not isinstance(widget, ipw.HTML):
                widget.observe(_autosave, names="value")

    def iter_display_widgets(self):
        """Yield flat sequence of (input_widget, description_label) for display."""
        for widget, label in self.widgets.values():
            yield widget
            if label is not None:
                yield label
        yield self.status_label


class CombinedConfigWidget(ipw.VBox):
    """Combined widget for all sessions with a single save button."""

    def __init__(self, session_data_list: list[dict[str, Any]], **vbox_kwargs):
        self.session_rows = [SessionConfigRow(data) for data in session_data_list]
        self.console = ipw.Output()

        header = ipw.HTML(value="<h3>Session metadata (auto-saves)</h3>")

        all_widgets = []
        for row in self.session_rows:
            all_widgets.append(ipw.HTML(value="<hr>"))
            all_widgets.extend(row.iter_display_widgets())

        widget_grid = ipw.VBox(all_widgets)

        self.save_to_npc_lims = ipw.Button(
            description="[upload only] Save & Push to GitHub (double-check!)",
            button_style="warning",
            layout=ipw.Layout(width="30%"),
            tooltip="Save yaml config for all sessions and push to GitHub",
        )

        def on_save_to_npc_lims_click(widget):
            widget.disabled = True
            with self.console:
                for row in self.session_rows:
                    path = row.save_to_session_folder()
                    print(f"Saved {path}")
            self.save_and_push()
            widget.button_style = "success"
            widget.disabled = False

        self.save_to_npc_lims.on_click(on_save_to_npc_lims_click)

        bottom = [] if os.environ.get("AIBS_RIG_ID") else [self.save_to_npc_lims]
        super().__init__(
            [header, widget_grid, *bottom, self.console],
            **vbox_kwargs,
        )

    def get_existing_sessions(self, yml_path: pathlib.Path) -> set[str]:
        """Get set of existing session paths from yaml file."""
        if not yml_path.exists():
            return set()

        existing = yaml.safe_load(yml_path.read_text()) or {}
        session_paths = set()

        for session_type, project_data in existing.items():
            if not isinstance(project_data, dict):
                continue
            for project, sessions in project_data.items():
                if not isinstance(sessions, list):
                    continue
                for session in sessions:
                    if isinstance(session, dict):
                        for path in session.keys():
                            session_paths.add(path)

        return session_paths

    def save_and_push(self) -> None:
        """Save all configs to yaml and push to GitHub."""
        with self.console:
            try:
                root = pathlib.Path().resolve().parent.parent
                repo_path = root / "npc_lims"
                yml_path = repo_path / "tracked_sessions.yaml"

                if not yml_path.exists():
                    raise FileNotFoundError(
                        f"git clone npc_lims into {root} before trying to update tracked_sessions.yaml"
                    )

                existing_sessions = self.get_existing_sessions(yml_path)
                new_configs = [row.get_config() for row in self.session_rows]

                duplicates = [
                    config.folder
                    for config in new_configs
                    if f"{PROJECT_PATHS[config.project]}/{config.folder}"
                    in existing_sessions
                ]

                if duplicates:
                    raise ValueError(
                        f"The following sessions are already in tracked_sessions.yaml: {', '.join(duplicates)}. "
                        f"To modify existing sessions, make changes directly in GitHub."
                    )

                txt = yml_path.read_text()

                for config in new_configs:
                    if config.session_type == "ephys":
                        ephys_stop = txt.find("behavior_with_sync:")
                        if config.project == "TempletonPilotSession":
                            stop = ephys_stop
                        else:
                            stop = txt[:ephys_stop].find("TempletonPilotSession:")
                    else:
                        assert config.session_type == "behavior_with_sync"
                        stop = len(txt)

                    txt = (
                        txt[:stop]
                        + "\n"
                        + config.to_yaml_text_snippet()
                        + "\n"
                        + ("  " if config.project == "DynamicRouting" else "")
                        + (txt[stop:] if stop else "\n")
                    )

                print("Updating tracked_sessions.yaml...")
                yml_path.write_text(txt)

                print("Committing changes...")
                subprocess.run(
                    ["git", "add", "tracked_sessions.yaml"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )

                commit_msg = f"Auto add metadata for {len(new_configs)} session(s)"
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )

                print("Pushing to GitHub...")
                subprocess.run(
                    ["git", "push"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )

                print(
                    f"✓ Successfully saved and pushed metadata for {len(new_configs)} session(s)!"
                )

            except subprocess.CalledProcessError as e:
                print(f"Git error: {e}")
                if e.stderr:
                    print(f"Error output: {e.stderr.decode()}")
                raise
            except Exception as e:
                print(f"Error: {e}")
                raise
