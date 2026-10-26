"""1.1.1 action-derived pairs, light-only repair and faithful emote regression."""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest

from sc_voice_cockpit.deck_pack import planner
from sc_voice_cockpit.deck_pack import emotes
from sc_voice_cockpit.keybindings import keybinds_core as core
from test_deck_runtime import runtime

ROOT=Path(__file__).resolve().parents[1]
PAIRS={
 'vtol':('VTOL On','VTOL Off','v_vtol_on','v_vtol_off'),
 'gear':('Landing Gear Down','Landing Gear Up','v_deploy_landing_system','v_retract_landing_system'),
 'lights':('Headlights On','Headlights Off','v_lights_on','v_lights_off'),
 'doors':('Open All Doors','Close All Doors','v_open_all_doors','v_close_all_doors'),
 'shield-power':('Shields On','Shields Off','v_power_set_shields_on','v_power_set_shields_off'),
 'weapons-power':('Weapons On','Weapons Off','v_power_set_weapons_on','v_power_set_weapons_off'),
 'thruster-power':('Engines On','Engines Off','v_power_set_thrusters_on','v_power_set_thrusters_off'),
 'power':('Main Power On','Main Power Off','v_power_set_on','v_power_set_off'),
}




@pytest.mark.parametrize("light,game",[(False,False),(False,True),(True,False),(True,True)])
def test_pair_convergence_dispatch_time(light,game):
    d=runtime();d.clients['a']=None
    b=d.manifest['buttons']['gear'];b.update(pair={'on':'Down','off':'Up'},state={'group':'gear','set':None})
    d.manifest['commands'].update(Down={'effects':[{'group':'gear','set':True}]},Up={'effects':[{'group':'gear','set':False}]})
    # Receive before changing the light: selection must occur at dispatch, not enqueue.
    d.receive('a',{'t':'tap','id':'gear'});d.states['gear']=light
    physical={'on':game};calls=[]
    async def invoke(_,args):
        name=args['command_name'];calls.append(name);physical['on']=name=='Down';d.sync(name)
        return NS(response='OK',instant_response='')
    d.skill.wingman.tools.invoke=invoke
    asyncio.run(d._execute(*d.jobs.get_nowait()))
    assert calls==['Up' if light else 'Down']
    assert d.states['gear']==physical['on']==(not light)

def test_pair_missing_falls_back_once_only():
    d=runtime();d.clients['a']=None
    b=d.manifest['buttons']['gear'];b.update(pair={'on':'Gone','off':'Gone'},state={'group':'gear','set':None})
    calls=[]
    async def invoke(_,args):
        name=args['command_name'];calls.append(name)
        if name=='Gone':return NS(response='Command not found',instant_response='')
        d.sync('Gear');return NS(response='OK',instant_response='')
    d.skill.wingman.tools.invoke=invoke
    asyncio.run(d._execute('a','gear',b))
    assert calls==['Gone','Gear'] and d.states['gear'] is True

def test_set_state_is_strict_focus_free_and_broadcast():
    d=runtime();d.manifest['groups']=['gear'];d.focus=lambda:False;events=[];d.broadcast=events.append
    for group,on in [('no',True),('gear',1),('gear','true'),([],True),('gear',None)]:
        assert not d.set_state(group,on)
    d.receive('a',{'t':'set-state','group':'gear','on':True})
    assert d.states=={'gear':True} and events==[{'t':'state','group':'gear','on':True}]
    assert not d.keyboard.events and d.jobs.empty()
    d.skill.wingman.tools.invoke.assert_not_awaited()




def test_no_key_tap_speaks_and_presses_nothing():
    d=runtime();d.clients['a']=None;d.focus=lambda:False
    d.manifest['commands']['Emote Wave']={'emote':'player_emotes.emote_wave','label':'Wave','no_key':True}
    b={'kind':'command','command':'Emote Wave','emote':'player_emotes.emote_wave','respond':True}
    asyncio.run(d._execute('a','wave',b))
    d.skill.wingman.tools.invoke.assert_not_awaited()
    assert not d.keyboard.events
    assert "no key set for the wave emote" in d.skill.wingman.tts.speak.call_args.args[0]

def test_emote_sent_never_ai_done_and_reminder_once():
    d=runtime()
    d.manifest['commands']['Emote Wave']={'emote':'player_emotes.emote_wave','label':'Wave','no_key':False}
    first=asyncio.run(d.emote_feedback('Emote Wave',True))
    second=asyncio.run(d.emote_feedback('Emote Wave',True))
    assert first=='Wave emote sent. Emotes only play on foot.'
    assert second=='Wave emote sent.'
    d.skill.wingman.ai.generate.assert_not_awaited()


def test_emote_audit_does_not_claim_verified_when_bind_cleared():
    target='player_emotes.emote_wave'
    row={'map':'player_emotes','name':'emote_wave','keyboard':[{'input':'rctrl+w'}],
         'mouse':[],'gamepad':[],'joystick':[],'activationMode':''}
    command={'name':'Emote Wave','actions':planner.key_steps('rctrl+w')+[planner.sync_step('Emote Wave','SC_VoiceCockpit')]}
    entry={'name':'Emote Wave','expectedActions':[target],'baselineActions':[target],
           'fingerprint':core.command_fingerprint(command),'emote':target,'verifiable':True}
    index=planner.Index({'actions':[row]})
    assert core.check_commands(index,[command],{'commands':[entry]})['commands'][0]['verified']
    cleared='<ActionMaps><actionmap name="player_emotes"><action name="emote_wave"><rebind input="kb1_"/></action></actionmap></ActionMaps>'
    index=planner.Index({'actions':[row]},cleared)
    result=core.check_commands(index,[command],{'commands':[entry]})['commands'][0]
    assert result['status']=='DRIFT' and result['missing']==[target] and result['verified'] is False


