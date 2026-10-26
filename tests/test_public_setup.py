"""Portable opt-in contracts; no private data or game extracts."""
import asyncio
import copy
from sc_voice_cockpit.deck_pack import plan_pack
from sample_index import synthetic_index
from test_deck_runtime import runtime

def test_preview_is_read_only_with_unavailable_commands_and_holds():
    raw=synthetic_index();before=copy.deepcopy(raw)
    commands=[{'name':'Pilot command','actions':[{'keyboard':{'hotkey':'k','hold':.1}}]}]
    old=copy.deepcopy(commands)
    plan=plan_pack(raw,None,[],commands,{}, {'setup':False})
    assert raw==before and commands==old
    assert all(not plan[k] for k in ('binds_to_add','binds_to_replace','commands_to_add','commands_to_update','manifest_entries'))
    unavailable=[b for b in plan['deck']['buttons'].values() if b.get('not_set_up')]
    assert {'command','hold'} <= {b['kind'] for b in unavailable}
    assert all(b['command']=='Pilot command' for b in plan['deck']['buttons'].values() if b.get('kind')=='command' and not b.get('not_set_up'))
    assert plan['deck']['version']=='1.2.0'

def test_unavailable_taps_holds_and_stale_queued_requests_press_nothing():
    deck=runtime();deck.clients['a']=None;sent=[];deck.send=lambda c,m:sent.append(m)
    for ident,kind in [('gear','tap'),('look','down'),('eject','tap')]:
        b=deck.manifest['buttons'][ident];b['not_set_up']=True
        deck.receive('a',{'t':kind,'id':ident})
        assert sent[-1]=={'t':'not-set-up','id':ident}
        asyncio.run(deck._execute('a',ident,b))
    assert len(sent)==6 and not deck.held and not deck.keyboard.events and deck.jobs.empty()
    deck.skill.wingman.tools.invoke.assert_not_awaited()
    deck.skill.wingman.tts.speak.assert_not_awaited()
