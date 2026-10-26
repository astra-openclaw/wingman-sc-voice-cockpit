import copy
import pytest
from sc_voice_cockpit.keybindings import keybinds_core as core, self_heal as heal

def index(key):
    return core.KeybindingIndex({"actions":[{"map":"spaceship_general","name":"doors","label":"Doors","mapLabel":"Flight","description":"","keyboard":[{"input":key}],"mouse":[],"joystick":[],"gamepad":[]}]})

def test_heal_preserves_only_recognized_trailing_sync_and_full_fingerprint():
    tail={"skill_action":{"skill_name":"SC_VoiceCockpit","function_name":"deck_sync","parameters":{"command":"Doors"}}}
    original={"name":"Doors","actions":heal.render("ralt+3",.1,core)+[tail]}
    entry=heal.steps(original,index("ralt+3"),core)
    repaired,changes=heal.repair(original,entry,index("ralt+f3"),core)
    assert changes and repaired["actions"][-1]==tail
    assert core.events(repaired)==[("ralt+f3",.1,False)]
    assert original["actions"][-1] is tail
    # Added non-tail actions or a changed sync target are still user edits.
    edited=copy.deepcopy(original);edited["actions"][-1]["skill_action"]["parameters"]["command"]="Other"
    with pytest.raises(ValueError,match="unchanged"):
        heal.repair(edited,entry,index("ralt+f3"),core)
    with pytest.raises(ValueError):
        heal.segments(edited,core)


def test_pack_manifest_never_rebaselines_drift_or_user_edits():
    from sc_voice_cockpit.deck.manifest import merge_manifest
    command={"name":"Doors","actions":heal.render("ralt+3",.1,core)}
    old={"name":"Doors","fingerprint":core.command_fingerprint(command),
         "expectedActions":["spaceship_general.doors"],"baselineActions":["spaceship_general.doors"],"verifiable":True}
    prior={"commands":[old]}
    planned={**old,"fingerprint":"sync-fingerprint","expectedActions":["spaceship_general.wrong"]}
    merged=merge_manifest(prior,[planned],[command],[{"name":"Doors"}],core)
    assert merged["commands"][0]["fingerprint"]=="sync-fingerprint"
    assert merged["commands"][0]["expectedActions"]==old["expectedActions"]
    edited=copy.deepcopy(command);edited["actions"].insert(0,{"wait":1})
    assert merge_manifest(prior,[planned],[edited],[{"name":"Doors"}],core)==prior
    assert merge_manifest(prior,[planned],[command],[],core)==prior
    assert prior["commands"][0]==old


def test_right_ctrl_renderer_and_repair_keep_recorded_extended_shape():
    rendered=heal.render('rctrl+w',.1,core)
    assert rendered[0]['keyboard']=={'hotkey':'right ctrl','press':True,'release':False,'hotkey_codes':[29],'hotkey_extended':True}
    assert rendered[-1]['keyboard']=={'hotkey':'right ctrl','press':False,'release':True,'hotkey_codes':[29],'hotkey_extended':True}
    command={'name':'Doors','actions':rendered}
    assert len(heal.segments(command,core))==1
    entry=heal.steps(command,index('rctrl+w'),core)
    repaired,_=heal.repair(command,entry,index('rctrl+s'),core)
    assert repaired['actions'][0]==rendered[0] and repaired['actions'][-1]==rendered[-1]
    assert core.events(repaired)==[('rctrl+s',.1,False)]
    assert heal.render('rctrl',.1,core)[0]['keyboard']['hotkey_codes']==[29]
