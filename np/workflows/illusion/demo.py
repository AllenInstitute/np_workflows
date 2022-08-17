# pdb.set_trace()

import io

try:
    #
    # limstk functions are auto-generated so the ide might warn it can't see them.

    # import mpetk
    # from np.models import model

    # import np.workflows.npxcommon as npxc
    import np.workflows.npxc as npxc

except Exception as e:
    # import errors aren't printed to console by default
    print(e)



def initialize_enter(state):
    # pdb.set_trace()
    state['external']["logo"] = R"C:\progra~1\AIBS_MPE\workflow_launcher\np\images\logo_np_vis.png"
    state["external"]["user_id"] = "ben.hardcastle"
    state["external"]["mouse_id"] = "598796"
    npxc.connect_to_services(state)


def initialize_input(state):
    npxc.set_user_id(state)
    npxc.set_mouse_id(state)
    state["external"]["available_stages"] = sorted([stage['name'].title() for stage in npxc.mtrain.stages])


def initialize_exit(state):
    pass


def mtrain_stage_enter(state):

    state['external']['alert'] = True
    state["external"]["transition_result"] = True
    state["external"]["msg_text"] = "msg_text_enter"
    # state['external']['transition_result'] = True
    state['external']['status_message'] = 'status_message_enter'

    state["external"]["current_regimen"] = npxc.mtrain.regimen['name']
    state["external"]["current_stage"] = npxc.mtrain.stage['name'].title()
    # state["external"]["new_stage"] = npxc.mtrain.stage['name'].title()
    # TODO sort with current stage in first entry
    # npxc.io.write(npxc.messages.state_ready(message="ready"))
    # # pdb.set_trace()
    # global io
    # io.write(npxc.messages.state_ready(message="ready"))
    # # state['external']['retry_state'] = None
    # # state['external']['override_state'] = override_state
    # state['resources']['io'].write(npxc.messages.state_ready(message="ready"))


def mtrain_stage_input(state):

    print(state["external"]["next_state"])
    new_stage = state["external"]["new_stage"]
    confirm_stage = state['external']['confirm_stage']
    change_regimen = state['external']['change_regimen']

    # pdb.set_trace()
    # state['external']['confirm_stage_or_change_regimen']
    #TODO get the next state(s) from brb in wfl/state if possible

    if confirm_stage and new_stage.lower() != npxc.mtrain.stage['name'].lower():
        npxc.mtrain.stage = new_stage
        state["external"]["next_state"] = 'mtrain_stage'

    #! there's no way to advance without selecting from the dropdown
    # elif confirm_stage and not new_stage:
    #     # state["external"]["next_state"] = 'run_stimulus'
    #     # pass should be equivalent to the above
    #     pass

    elif change_regimen:
        state["external"]["next_state"] = 'mtrain_regimen_1'


def mtrain_regimen_1_enter(state):
    # current regimen is already set in prev screen, but we set it again here anyway
    state["external"]["current_regimen"] = npxc.mtrain.regimen['name']
    state["external"]["available_regimens"] = sorted([regimen for regimen in npxc.mtrain.all_regimens().values()])


def mtrain_regimen_1_input(state):
    new_regimen = state["external"]["new_regimen"]
    confirm_regimen = state['external']['confirm_regimen']
    cancel_regimen_select = state['external']['cancel_regimen_select']

    if cancel_regimen_select:
        # cancel
        state["external"]["next_state"] = 'mtrain_stage'
    elif confirm_regimen and new_regimen:
        # don't set anything yet - we need a corresponding stage for the new regimen
        state["external"]["next_state"] = 'mtrain_regimen_2'
    elif confirm_regimen and not new_regimen:
        # apparently no change is requested - go back to the stage selection
        state["external"]["next_state"] = 'mtrain_stage'


def mtrain_regimen_2_enter(state):
    # get the stages available for the new regimen:
    new_regimen_dict = [
        regimen for regimen in npxc.mtrain.get_all("regimens")
        if regimen['name'].lower() == state["external"]["new_regimen"].lower()
    ][0]
    new_stages = new_regimen_dict['stages']
    state['external']['available_stages_new_regimen'] = sorted([stage['name'].title() for stage in new_stages])
    state["external"]["new_regimen"] = new_regimen_dict['name']


def mtrain_regimen_2_input(state):
    # copy verbatim this line from mtrain_regimen_2_enter
    new_regimen_dict = [
        regimen for regimen in npxc.mtrain.get_all("regimens")
        if regimen['name'].lower() == state["external"]["new_regimen"].lower()
    ][0]

    selected_stage_new_regimen = state["external"]["selected_stage_new_regimen"]
    confirm_regimen_and_stage = state['external']['confirm_regimen_and_stage']
    cancel_regimen_select = state['external']['cancel_regimen_select']

    # pdb.set_trace()
    # state['external']['confirm_stage_or_change_regimen']
    #TODO get the next state(s) from brb in wfl/state if possible

    if cancel_regimen_select:
        state["external"]["next_state"] = 'mtrain_stage'

    elif confirm_regimen_and_stage and selected_stage_new_regimen:

        new_stage_dict = [
            stage for stage in new_regimen_dict['stages']
            if stage['name'].lower() == selected_stage_new_regimen.lower()
        ][0]
        npxc.mtrain.set_regimen_and_stage(new_regimen_dict, new_stage_dict)
        state["external"]["next_state"] = 'mtrain_stage'

    elif confirm_regimen_and_stage and not selected_stage_new_regimen:
        # state["external"]["next_state"] = 'run_stimulus'
        # not sure if it's even possible to continue (green arrow) without selecting a stage
        state["external"]["next_state"] = 'mtrain_stage'


def run_stimulus_input(state):
    npxc.camstim_agent.start_session(state["external"]["mouse_id"], state["external"]["user_id"])


def wrap_up_input(state):
    pass
