import datetime
import pathlib
import threading
import time
from typing import NoReturn

import IPython
import IPython.display
import ipywidgets as ipw
import PIL.Image

import np_config
import np_session

import np_workflows.npxc

def elapsed_time_widget() -> IPython.display.DisplayHandle | None:
    """Displays a clock showing the elapsed time since the cell was first run."""
    
    clock_widget = ipw.Label('')
    reminder_widget = ipw.Label('Remember to restart the kernel for every experiment!')
    global start_time
    if 'start_time' not in globals():
        start_time = time.time()
        
    if isinstance(start_time, datetime.datetime):
        start_time = start_time.timestamp()
    def update_timer() -> NoReturn:
        while True:
            elapsed_sec = time.time() - start_time
            hours, remainder = divmod(elapsed_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            clock_widget.value = 'Elapsed time: {:02}h {:02}m {:02}s'.format(int(hours), int(minutes), int(seconds))
            if hours > 4: # ipywidgets >= 8.0 
                clock_widget.style = dict(
                    text_color='red',
                )
            time.sleep(0.2)
    thread = threading.Thread(target=update_timer, args=())
    thread.start()
    return IPython.display.display(ipw.VBox([clock_widget, reminder_widget]))


def user_and_mouse_widget() -> tuple[np_session.User, np_session.Mouse]:
    user_widget = ipw.Select(options=np_workflows.npxc.lims_user_ids, description='User:')
    mouse_widget = ipw.Text(value='366122', description='Mouse:')
    user = np_session.User(str(user_widget.value))
    mouse = np_session.Mouse(str(mouse_widget.value))
    def new_user(change) -> None:
        user.__init__(str(user_widget.value))
    def new_mouse(change) -> None:
        mouse.__init__(str(mouse_widget.value))
    user_widget.observe(new_user)
    mouse_widget.observe(new_mouse)
    IPython.display.display(ipw.VBox([user_widget, mouse_widget]))
    return user, mouse
    
    
def mtrain_widget(labtracks_mouse_id: str | int | np_session.Mouse) -> IPython.display.DisplayHandle | None:
    """Displays a widget to view and edit MTrain regimen/stage for a mouse.
    """
    if not isinstance(labtracks_mouse_id, np_session.Mouse):
        mtrain = np_session.MTrain(labtracks_mouse_id)
    else:
        mtrain = labtracks_mouse_id.mtrain
        
    all_regimens = mtrain.get_all('regimens')
    regimen_names = sorted(_['name'] for _ in all_regimens)
    
    widget = ipw.GridspecLayout(n_rows=4, n_columns=2)
    
    # labels
    widget[0, 0] = ipw.Label(f'Mouse: {mtrain.mouse_id}')
    widget[1, 0] = regimen_label = ipw.Label('Regimen:')
    widget[2, 0] = stage_label = ipw.Label('Stage:')
    
    # dropdowns
    widget[1, 1] = regimen_dropdown = ipw.Dropdown(options=regimen_names)
    widget[2, 1] = stage_dropdown = ipw.Dropdown(options=sorted([_['name'] for _ in mtrain.stages]))
    stage_dropdown.stages: list[dict] = mtrain.stages
    
    widget[3, 1] = update_button = ipw.Button(description='Update', disabled=True)
    
    console = ipw.Output()
    
    display = ipw.VBox([widget, console])
    
    def on_regimen_change(change: dict):
        update_button.disabled = True
        new_regimen_dict = [
            regimen for regimen in all_regimens
            if regimen['name'] == regimen_dropdown.value
        ][0]
        stage_dropdown.options = sorted([_['name'] for _ in new_regimen_dict['stages']])
        stage_dropdown.value = None
        stage_dropdown.stages = new_regimen_dict['stages']
        
    regimen_dropdown.observe(on_regimen_change, names='value')
    
    def reset_update_button():
        update_button.description = 'Update'
        update_button.disabled = True
        update_button.button_style = ''
        
    def on_stage_change(change: dict):
        reset_update_button()
        if change['new'] is None:
            return
        if change['new'] not in stage_label.value or str(regimen_dropdown.value) not in str(regimen_label.value):
            # enable button if stage name changed, or regimen name changed (some
            # regimens have the same stage names as other regimens)
            update_button.disabled = False
            update_button.button_style = 'warning'
            
    stage_dropdown.observe(on_stage_change, names='value')
    
    def update_label_values() -> None:
        regimen_label.value = f'Regimen: {mtrain.regimen["name"]}'
        stage_label.value = f'Stage: {mtrain.stage["name"]}'
    
    def update_dropdown_values() -> None:
        regimen_dropdown.value = mtrain.regimen['name']
        stage_dropdown.value = mtrain.stage['name']
        
    def update_regimen_and_stage_in_mtrain(b):
        update_button.description = 'Updating...'
        update_button.disabled = True
        
        old_regimen_name = regimen_label.value
        old_stage_name = stage_label.value
        
        new_regimen_dict = [_ for _ in all_regimens if _['name'] == regimen_dropdown.value][0]
        new_stage_dict = [_ for _ in stage_dropdown.stages if _['name'] == stage_dropdown.value][0]
        
        mtrain.set_regimen_and_stage(regimen=new_regimen_dict, stage=new_stage_dict)
        update_all()
        
        regimen_name_changed: bool = new_regimen_dict['name'] not in old_regimen_name
        stage_name_changed: bool = new_stage_dict['name'] not in old_stage_name
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


def isi_widget(
        labtracks_mouse_id: str | int | np_session.LIMS2MouseInfo,
        colormap: bool = False,
    ) -> IPython.display.DisplayHandle | None:
    """Displays ISI target map from lims (contours only), or colormap overlay if
    `show_colormap = True`."""
    if not isinstance(labtracks_mouse_id, np_session.LIMS2MouseInfo):
        lims_info = np_session.LIMS2MouseInfo(labtracks_mouse_id)
    else:
        lims_info = labtracks_mouse_id
    
    if colormap:
        key = 'isi_image_overlay_path'
    else:
        key = 'target_map_image_path'
    
    try:
        lims_path = lims_info.isi_info[key]
    except ValueError:
        print('Mouse is not in lims.')
        return
    except (AttributeError, TypeError):
        print('No ISI map found for this mouse.')
        return
    except KeyError:
        print(f'ISI info found for this mouse, but {key=!r} is missing.')
        return IPython.display.display(IPython.display.JSON(lims_info.isi_info))
    else:
        path: pathlib.Path = np_config.normalize_path(
            lims_path
        )
        print(f'ISI map found for {lims_info.np_id}:\n{path}')
        img = PIL.Image.open(path)
        return IPython.display.display(img)
    