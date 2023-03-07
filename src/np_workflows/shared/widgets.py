import contextlib
import datetime
import logging
import pathlib
import threading
import time
from typing import NoReturn, Optional

import IPython
import IPython.display
import ipywidgets as ipw
import np_config
import np_logging
import np_session
import np_services
import PIL.Image

import np_workflows.shared.npxc as npxc

logger = np_logging.getLogger(__name__)

np_logging.getLogger('Comm').propagate = False
np_logging.getLogger('PIL').propagate = False


def elapsed_time_widget() -> IPython.display.DisplayHandle | None:
    """Displays a clock showing the elapsed time since the cell was first run."""

    clock_widget = ipw.Label("")
    reminder_widget = ipw.Label("Remember to restart the kernel for every experiment!")
    global start_time
    if "start_time" not in globals():
        start_time = time.time()

    if isinstance(start_time, datetime.datetime):
        start_time = start_time.timestamp()

    def update_timer() -> NoReturn:
        while True:
            elapsed_sec = time.time() - start_time
            hours, remainder = divmod(elapsed_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            clock_widget.value = "Elapsed time: {:02}h {:02}m {:02}s".format(
                int(hours), int(minutes), int(seconds)
            )
            if hours > 4:  # ipywidgets >= 8.0
                clock_widget.style = dict(text_color="red",)
            time.sleep(0.2)

    thread = threading.Thread(target=update_timer, args=())
    thread.start()
    return IPython.display.display(ipw.VBox([clock_widget, reminder_widget]))


def user_and_mouse_widget() -> tuple[np_session.User, np_session.Mouse]:
    user_description = "User:"
    mouse_description = "Mouse:"
    user_widget = ipw.Select(options=npxc.lims_user_ids, description=user_description)
    mouse_widget = ipw.Text(value=str(npxc.default_mouse_id), description=mouse_description)
    user = np_session.User(str(user_widget.value))
    mouse = np_session.Mouse(str(mouse_widget.value))
    console = ipw.Output()

    def update_user(new_user: str):
        if str(user) == (new := str(new_user).strip()):
            return
        user.__init__(new)
        with console:
            print(f"User updated: {user}")

    def update_mouse(new_mouse: str):
        if str(mouse) == (new := str(new_mouse).strip()):
            return
        if len(new) < 6:
            return
        mouse.__init__(new)
        with console:
            print(f"Mouse updated: {mouse}")
        
    def new_value(change) -> None:
        if change['name'] != 'value':
            return
        if (options := getattr(change['owner'], 'options', None)) and change['new'] not in options:
            return
        if change['new'] == change['old']:
            return
        if (desc := getattr(change['owner'], 'description')) == user_description:
                update_user(change['new'])
        elif desc == mouse_description:
                update_mouse(change['new'])
            
    user_widget.observe(new_value)
    mouse_widget.observe(new_value)
    
    IPython.display.display(ipw.VBox([user_widget, mouse_widget, console]))
    return user, mouse


def mtrain_widget(
    labtracks_mouse_id: str | int | np_session.Mouse,
) -> IPython.display.DisplayHandle | None:
    """Displays a widget to view and edit MTrain regimen/stage for a mouse.
    """
    if not isinstance(labtracks_mouse_id, np_session.Mouse):
        mtrain = np_session.MTrain(labtracks_mouse_id)
    else:
        mtrain = labtracks_mouse_id.mtrain

    all_regimens = mtrain.get_all("regimens")
    regimen_names = sorted(_["name"] for _ in all_regimens)

    widget = ipw.GridspecLayout(n_rows=4, n_columns=2)

    # labels
    widget[0, 0] = ipw.Label(f"Mouse: {mtrain.mouse_id}")
    widget[1, 0] = regimen_label = ipw.Label("Regimen:")
    widget[2, 0] = stage_label = ipw.Label("Stage:")

    # dropdowns
    widget[1, 1] = regimen_dropdown = ipw.Dropdown(options=regimen_names)
    widget[2, 1] = stage_dropdown = ipw.Dropdown(
        options=sorted([_["name"] for _ in mtrain.stages])
    )
    stage_dropdown.stages: list[dict] = mtrain.stages

    widget[3, 1] = update_button = ipw.Button(description="Update", disabled=True)

    console = ipw.Output()

    display = ipw.VBox([widget, console])

    def on_regimen_change(change: dict):
        update_button.disabled = True
        new_regimen_dict = [
            regimen
            for regimen in all_regimens
            if regimen["name"] == regimen_dropdown.value
        ][0]
        stage_dropdown.options = sorted([_["name"] for _ in new_regimen_dict["stages"]])
        stage_dropdown.value = None
        stage_dropdown.stages = new_regimen_dict["stages"]

    regimen_dropdown.observe(on_regimen_change, names="value")

    def reset_update_button():
        update_button.description = "Update"
        update_button.disabled = True
        update_button.button_style = ""

    def on_stage_change(change: dict):
        reset_update_button()
        if change["new"] is None:
            return
        if change["new"] != stage_label.value or str(
            regimen_dropdown.value
        ) != str(regimen_label.value):
            # enable button if stage name changed, or regimen name changed (some
            # regimens have the same stage names as other regimens)
            update_button.disabled = False
            update_button.button_style = "warning"

    stage_dropdown.observe(on_stage_change, names="value")

    def update_label_values() -> None:
        regimen_label.value = f'Regimen: {mtrain.regimen["name"]}'
        stage_label.value = f'Stage: {mtrain.stage["name"]}'

    def update_dropdown_values() -> None:
        regimen_dropdown.value = mtrain.regimen["name"]
        stage_dropdown.value = mtrain.stage["name"]

    def update_regimen_and_stage_in_mtrain(b):
        update_button.description = "Updating..."
        update_button.disabled = True

        old_regimen_name = regimen_label.value
        old_stage_name = stage_label.value

        new_regimen_dict = [
            _ for _ in all_regimens if _["name"] == regimen_dropdown.value
        ][0]
        new_stage_dict = [
            _ for _ in stage_dropdown.stages if _["name"] == stage_dropdown.value
        ][0]

        mtrain.set_regimen_and_stage(regimen=new_regimen_dict, stage=new_stage_dict)
        update_all()

        regimen_name_changed: bool = new_regimen_dict["name"] not in old_regimen_name
        stage_name_changed: bool = new_stage_dict["name"] not in old_stage_name
        with console:
            if regimen_name_changed:
                print(f'{old_regimen_name} changed to {mtrain.regimen["name"]}\n')
            if stage_name_changed or regimen_name_changed:
                print(f'{old_stage_name} changed to {mtrain.stage["name"]}\n')

    update_button.on_click(update_regimen_and_stage_in_mtrain)

    def update_all():
        update_label_values()
        update_dropdown_values()
        reset_update_button()
        update_label_values()
        update_dropdown_values()

    update_all()

    return IPython.display.display(display)

def check_widget(check: str, *checks: str) -> ipw.Widget:
    layout = ipw.Layout(min_width="600px")
    widget = ipw.VBox([
        ipw.Label(check, layout=layout), 
        *(ipw.Checkbox(description=_, layout=layout) for _ in checks),
        # ipw.Button(description="Continue", disabled=True)
        ])
    return widget

def await_all_checkboxes(widget: ipw.Box) -> None:
    while any(_.value == False for _ in widget.children if isinstance(_, ipw.Checkbox)):
        time.sleep(0.1)
    
    
def check_openephys_widget() -> None:
    check = "OpenEphys checks:"
    checks = (
        "Record Node paths are set to two different drives (A: & B: or E: & G:)",
        "Each Record Node recording only ABC or DEF probes",
        "Tip-reference on all probes",
        "Barcodes visible",
    )
    IPython.display.display(widget := check_widget(check, *checks))

def check_hardware_widget() ->  None:
    check = "Stage checks:"
    checks = (
        "Cartridge raised (fully retract probes before raising!)",
        "Water lines flushed",
    )
    IPython.display.display(widget := check_widget(check, *checks))

def check_mouse_widget() -> None:
    check = "Mouse checks before lowering cartridge:"
    checks = (
        "Stabilization screw",
        "Quickcast, agarose, silicon oil",
        "Tail cone down",
        "Continuity/Resistance check",
        "Eye-tracking mirror in place",
    )
    IPython.display.display(widget := check_widget(check, *checks))

def pre_stim_check_widget() -> None:
    check = "Before running stim:"
    checks = (
        "Windows minimized on Stim computer (Win+D)",
        "Eye-tracking mirror clean", 
        "Monitor closed",
        "Photodoc light off",
        "Curtain down",     
    )
    IPython.display.display(widget := check_widget(check, *checks))
    
def finishing_checks_widget() -> None:
    check = "Finishing checks:"
    checks = (
        "Add quickcast etc.",
        "Remove and water mouse", 
        "Dip probes",     
    )
    IPython.display.display(widget := check_widget(check, *checks))
    
    
def wheel_height_widget(platform_json: pathlib.Path | np_services.PlatformJsonWriter) -> IPython.display.DisplayHandle | None:
    "Supply a path or a platform json instance. Saves a JSON file with the wheel height recorded."

    if isinstance(platform_json, pathlib.Path):
        platform_json = np_services.PlatformJsonWriter(path=platform_json)
    platform_json.load_from_existing()
    
    if platform_json.mouseID:
        mouse = np_session.Mouse(platform_json.mouseID)
        
    layout = ipw.Layout(max_width='130px')
    
    try:
        prev_height = mouse.state.get('wheel_height', 0)
    except:
        prev_height = 0
    height_counter = ipw.BoundedFloatText(value=prev_height, min=0, max=10, step=0.1, description="Wheel height", layout=layout)
    save_button = ipw.Button(description='Save', button_style='warning', layout=layout)

    def on_click(b):
        platform_json.wheel_height = height_counter.value
        with contextlib.suppress(Exception):
            mouse.state['wheel_height'] = height_counter.value
        save_button.button_style = 'success'
        save_button.description = 'Saved'
    save_button.on_click(on_click)
    return IPython.display.display(ipw.VBox([height_counter,save_button]))
    
    
def di_widget(platform_json: pathlib.Path | np_services.PlatformJsonWriter) -> IPython.display.DisplayHandle | None:
    "Supply a path or a platform json instance. Saves a JSON file with the dye used in the session and a timestamp."

    di_info: dict[str, int | str] = dict(
        EndTime=0, StartTime=str(npxc.now()), dii_description="", times_dipped=0,
    )
    if isinstance(platform_json, pathlib.Path):
        platform_json = np_services.PlatformJsonWriter(path=platform_json)
    layout = ipw.Layout(max_width='130px')
    dipped_counter = ipw.IntText(value=0, min=0, max=99, description="Dipped count", layout=layout)
    usage_counter = ipw.IntText(value=0, min=0, max=99, description="Previous uses", layout=layout)
    dye_dropdown = ipw.Dropdown(options=['CM-DiI 100%', 'DiO'], layout=layout)
    save_button = ipw.Button(description='Save', button_style='warning', layout=layout)
    
    def update_di_info():
        di_info['EndTime'] = str(npxc.now())
        di_info['times_dipped'] = str(dipped_counter.value)
        di_info['dii_description'] = str(dye_dropdown.value)
        di_info['previous_uses'] = str(usage_counter.value)
        
    def on_click(b):
        update_di_info()
        platform_json.DiINotes = di_info
        save_button.button_style = 'success'
        save_button.description = 'Saved'
    save_button.on_click(on_click)
    return IPython.display.display(ipw.VBox([
        dipped_counter, dye_dropdown, 
        usage_counter, save_button]))

    
def dye_widget(session_folder: pathlib.Path) -> IPython.display.DisplayHandle | None:
    "Supply a path - saves a JSON file with the dye used in the session and a timestamp."

    di_info: dict[str, int | str] = dict(
        EndTime=0, StartTime=0, dii_description="DiI", times_dipped=0,
    )
        
    class DyeRecorder(np_services.JsonRecorder):
        log_name = f'{session_folder.name}_dye.json'
        log_root = session_folder

    dye_dropdown = ipw.Dropdown(options=['DiI', 'DiO'])
    save_button = ipw.Button(description='Save', button_style='warning')
    def on_click(b):
        DyeRecorder.write(dict(dye=dye_dropdown.value, datetime=datetime.datetime.now(), time=time.time()))
        save_button.button_style = 'success'
        save_button.description = 'Saved'
    save_button.on_click(on_click)
    return IPython.display.display(ipw.VBox([dye_dropdown, save_button]))

def isi_widget(
    labtracks_mouse_id: str | int | np_session.LIMS2MouseInfo, colormap: bool = False,
) -> IPython.display.DisplayHandle | None:
    """Displays ISI target map from lims (contours only), or colormap overlay if
    `show_colormap = True`."""
    if not isinstance(labtracks_mouse_id, np_session.LIMS2MouseInfo):
        mouse_info = np_session.LIMS2MouseInfo(labtracks_mouse_id)
    else:
        mouse_info = labtracks_mouse_id

    if colormap:
        key = "isi_image_overlay_path"
    else:
        key = "target_map_image_path"
    
    try:
        lims_path = mouse_info.isi_info[key]
    except ValueError:
        print("Mouse is not in lims.")
        return
    except (AttributeError, TypeError):
        print("No ISI map found for this mouse.")
        return
    except KeyError:
        print(f"ISI info found for this mouse, but {key=!r} is missing.")
        return IPython.display.display(IPython.display.JSON(mouse_info.isi_info))
    else:
        path: pathlib.Path = np_config.normalize_path(lims_path)
        print(f"ISI map found for {mouse_info.np_id}:\n{path}")
        img = PIL.Image.open(path)
        if coords := mouse_info.isi_targets:
            draw = PIL.ImageDraw.Draw(img)
            draw.line([(_['x'], _['y']) for _ in coords], fill=True, width=5)
        else: 
            logger.debug("No ISI targets found for %r in lims, ISI experiment id %s", mouse_info, mouse_info.isi_id)
        return IPython.display.display(img)
    
    # alternative to use ipw with toggle button -------------------------------------------- #
    # import io
    # membuf = io.BytesIO()
    # img.save(membuf, format="png") 
    # return IPython.display.display(ipw.VBox([ipw.Image(value=membuf.getvalue())]))


def photodoc_widget(img_name: str) -> IPython.display.DisplayHandle | None:
    "Captures and displays snapshot from image camera, appending `img_name` to the filename."
    image = ipw.Image(value=b'', format='png', width='80%', layout=ipw.Layout(visibility='hidden'))
    widget = ipw.VBox([
        image,
        button := ipw.Button(description="Capture", button_style='warning'),
        console := ipw.Output(),
    ])
    def capture() -> pathlib.Path:
        image.value = b''
        image.layout.visibility = 'hidden'
        button.button_style = ''
        button.description = 'Capturing image...'
        button.disabled = True
        return npxc.photodoc(img_name)
    def capture_and_display(b):
        img_path = capture()
        # image.value = PIL.Image.open(img_path).tobytes()
        image.value = img_path.read_bytes()
        image.layout.visibility = 'visible'
        button.button_style = 'warning'
        button.description = 'Capture'
        button.disabled = False
        with console:
            print(img_path)
    button.on_click(capture_and_display)
    return IPython.display.display(widget)

def probe_targeting_widget(session_folder) -> IPython.display.DisplayHandle | None:
    from np_probe_targets.implant_drawing import CurrentWeek, DRWeeklyTargets
    CurrentWeek.display()
    IPython.display.display(DRWeeklyTargets())
    
def quiet_mode_widget() -> IPython.display.DisplayHandle | None:
    """Displays a toggle button that switches logging level INFO <-> DEBUG and
    hides/shows tracebacks.
    """
    debug_mode_toggle = ipw.ToggleButton(
            value=True,
            description='Quiet mode is on',
            disabled=False,
            button_style='info', # 'success', 'info', 'warning', 'danger' or ''
            icon='check',
            tooltip='Quiet mode: tracebacks hidden, logging level set to INFO.',
        )
    
    def set_debug_mode(value: bool) -> None:
        if value:
            npxc.show_tracebacks()
            for handler in np_logging.getLogger().handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel('DEBUG')
        else:
            npxc.hide_tracebacks()
            for handler in np_logging.getLogger().handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel('INFO')
                
    def on_click(b) -> None:
        if not debug_mode_toggle.value:
            set_debug_mode(True)
            debug_mode_toggle.description = 'Quiet mode is off'
            debug_mode_toggle.button_style = ''
            debug_mode_toggle.icon = 'times'
        else:
            set_debug_mode(False)
            debug_mode_toggle.description = 'Quiet mode is on'
            debug_mode_toggle.button_style = 'info'
            debug_mode_toggle.icon = 'check'
    
    debug_mode_toggle.observe(on_click)
     
    return IPython.display.display(debug_mode_toggle)

