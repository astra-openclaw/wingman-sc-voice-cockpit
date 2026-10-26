import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from fixtures import mini_index,commands
from sc_voice_cockpit.keybindings import keybinds_core as kc
ROOT=Path(__file__).resolve().parents[2]

class StubSkill:
    def __init__(self,*a,**k):
        self.setting='auto';self.generated=None;self.endearments=''
        self.log=types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None)
        self.config=types.SimpleNamespace(prompt='base')
        self.wingman=types.SimpleNamespace(name='Pilot',commands=types.SimpleNamespace(all=lambda:tuple(commands())),
            audio=types.SimpleNamespace(on_playback_started=lambda cb:types.SimpleNamespace(unsubscribe=lambda:None),on_playback_finished=lambda cb:types.SimpleNamespace(unsubscribe=lambda:None)))
    async def validate(self):return []
    async def unload(self):pass
    async def update_config(self,new_config):self.config=new_config
    def retrieve_custom_property_value(self,name,*a):return self.setting if name=='game_folder' else self.endearments
    def get_generated_files_dir(self):return str(self.generated)

def adapter():
    fake=types.ModuleType('skills.skill_base');fake.Skill=StubSkill;fake.tool=lambda **kwargs:lambda f:f;fake.command_action=lambda **kwargs:lambda f:f
    with patch.dict(sys.modules,{'skills.skill_base':fake}):
        spec=importlib.util.spec_from_file_location('public_adapter',ROOT/'sc_voice_cockpit/keybindings/main.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

class BehaviorTests(unittest.TestCase):
    def setUp(self):self.raw=mini_index();self.index=kc.KeybindingIndex(self.raw)
    def test_vocabulary_and_stale(self):
        self.assertEqual(self.index.stale_vocabulary,[])
        self.assertEqual(self.index.search('flaring transponder'),[])
        for phrase in ['flares','chaff','qt','cruise control','nearest enemy','fire missiles','get out of the seat','power up','scan for targets','free look','strafe up','salvage mode']:
            with self.subTest(phrase=phrase):self.assertTrue(self.index.search(phrase))
        raw=copy.deepcopy(self.raw);raw['actions']=[a for a in raw['actions'] if a['name']!='v_weapon_countermeasure_noise_launch']
        changed=kc.KeybindingIndex(raw)
        self.assertIn('spaceship_defensive.v_weapon_countermeasure_noise_launch',changed.stale_vocabulary)
        self.assertEqual(changed.search('chaff'),[])
    def test_spoken_search_reverse_close_unbound(self):
        self.assertIn('Hold F6',self.index.spoken('full power to engines'))
        self.assertIn('quantum mode',self.index.spoken('quantum drive'))
        self.assertIn('tap',self.index.spoken(key='f6').lower());self.assertIn('hold',self.index.spoken(key='f6').lower())
        self.assertEqual(self.index.spoken(key='ralt+k'),'Right Alt plus K is Port Lock Toggle All.')
        self.assertIn("isn't bound",self.index.spoken('lock all doors'))
        self.assertIn("won't guess",self.index.spoken('transponder'))
        self.assertTrue(self.index.spoken('shields').endswith('?'))
        self.assertIn('F7',self.index.spoken('shields'));self.assertIn('O',self.index.spoken('shields'))
        for phrase in ('lights','shields','flares','lock all doors','quantum drive'):
            self.assertLessEqual(len(self.index.spoken(phrase).split()),45)
            self.assertLessEqual(len(self.index.answer(phrase)),1200)
    def test_subjects_and_cleaning(self):
        for q,excluded in [('lock the doors','Port'),('lock the ports','Door')]:
            result=self.index.answer(q);self.assertNotIn(excluded,result)
        self.assertNotEqual(self.index.search('power up the shields')[0]['name'],'v_flightready')
        self.assertEqual(self.index.search('scan for targets')[0]['name'],'v_scanning_trigger_scan')
        self.assertEqual(self.index.search('strafe up')[0]['map'],'spaceship_movement')
        for row in self.raw['actions']:
            label=kc.speakable_label(row['label']);self.assertTrue(label);self.assertNotIn('_',label)
        for q in ['hold fire','hold position','press the landing gear','tap the boost']:self.assertFalse(kc.is_key_question(q))
        for q in ['what should I hold?','which key is boost','what does F6 do','how do I jump']:self.assertTrue(kc.is_key_question(q))
    def test_override_semantics_and_last_good(self):
        with tempfile.TemporaryDirectory(prefix='sc-kb-public-override-') as d:
            path=Path(d)/'actionmaps.xml';live=kc.LiveBindings(self.raw,path);live.refresh()
            self.assertIn('Right Alt plus R',live.answer('flight ready'))
            path.write_text('<ActionMaps><actionmap name="spaceship_general"><action name="v_flightready"><rebind input="kb1_x"/></action></actionmap></ActionMaps>', encoding="utf-8");live.refresh()
            self.assertIn('X',live.answer('flight ready'))
            path.write_text('<broken', encoding="utf-8");live.refresh();self.assertIn('last good',live.warning);self.assertIn('X',live.answer('flight ready'))

class DriftTests(unittest.TestCase):
    def setUp(self):self.raw=mini_index();self.index=kc.KeybindingIndex(self.raw);self.commands=commands()
    def test_baseline_missing_moved_and_reset(self):
        report,baseline=kc.check_baseline(self.index,self.commands)
        self.assertEqual(kc.drift_prompt(report),'');self.assertEqual(len(baseline['commands']),2)
        self.assertEqual(baseline['commands'][0]['resolved'],['spaceship_general.v_flightready'])
        # On-foot inventory is generic baseline relevance, unlike curated ship rules.
        self.assertIn('player.inventory',baseline['commands'][1]['resolved'])
        for mode in ('moved','removed'):
            raw=copy.deepcopy(self.raw)
            if mode=='removed':raw['actions']=[a for a in raw['actions'] if a['name']!='v_flightready']
            else:next(a for a in raw['actions'] if a['name']=='v_flightready')['keyboard'][0]['input']='x'
            changed,retained=kc.check_baseline(kc.KeybindingIndex(raw),self.commands,baseline)
            self.assertEqual(changed['commands'][0]['status'],'DRIFT');self.assertEqual(retained,baseline)
        xml='<ActionMaps><actionmap name="spaceship_general"><action name="v_flightready"><rebind input="kb1_x"/></action></actionmap></ActionMaps>'
        changed,_=kc.check_baseline(kc.KeybindingIndex(self.raw,xml),self.commands,baseline)
        self.assertEqual(changed['commands'][0]['status'],'DRIFT')
        bound=kc.KeybindingIndex(self.raw,xml);_,custom=kc.check_baseline(bound,[dict(name='Custom Start',actions=[dict(keyboard=dict(hotkey='x'))])])
        reset,_=kc.check_baseline(self.index,[dict(name='Custom Start',actions=[dict(keyboard=dict(hotkey='x'))])],custom)
        self.assertEqual(reset['commands'][0]['status'],'DRIFT')
    def test_rebaseline_edits_new_removed_and_extras(self):
        _,baseline=kc.check_baseline(self.index,self.commands)
        cmds=copy.deepcopy(self.commands);cmds[0]['actions'][0]['keyboard']['hotkey']='x'
        report,updated=kc.check_baseline(self.index,cmds,baseline)
        self.assertEqual(report['commands'][0]['status'],'OK');self.assertTrue(report['commands'][0]['edited'])
        self.assertEqual(updated['commands'][0]['resolved'],['player.prone'])
        raw=copy.deepcopy(self.raw);extra=copy.deepcopy(raw['actions'][0]);extra['map']='new_vehicle_map';raw['actions'].append(extra)
        report,retained=kc.check_baseline(kc.KeybindingIndex(raw),self.commands,baseline)
        self.assertEqual(report['commands'][0]['status'],'OK');self.assertEqual(retained,baseline)
        cmds=[dict(name='New Command',actions=[dict(keyboard=dict(hotkey='x'))])]
        report,updated=kc.check_baseline(self.index,cmds,baseline)
        self.assertEqual([x['name'] for x in updated['commands']],['New Command']);self.assertEqual(report['commands'][0]['status'],'OK')
    def test_curated_and_nullable_shapes_and_numbers(self):
        cmd=self.commands[0];reached=kc.resolved(cmd,self.index,curated=True)
        manifest={'commands':[dict(name=cmd['name'],fingerprint=kc.command_fingerprint(cmd),expectedActions=reached,baselineActions=reached,verifiable=True)]}
        self.assertEqual(kc.check_commands(self.index,self.commands,manifest)['commands'][0]['status'],'OK')
        cmd2=copy.deepcopy(cmd);cmd2['actions'][0]['keyboard']['hotkey']='x'
        self.assertEqual(kc.check_commands(self.index,[cmd2],manifest)['commands'][0]['status'],'DRIFT')
        self.assertEqual(kc.command_fingerprint({'actions':[{'keyboard':{'hold':2}}]}),kc.command_fingerprint({'actions':[{'keyboard':{'hold':2.0}}]}))
        nullable=[dict(name=None,actions=None),dict(name=None,actions=[None,{'keyboard':None,'mouse':None}]),dict(name=None,actions=[{'keyboard':{'hotkey':None,'hold':None}}])]
        kc.check_commands(self.index,nullable,manifest);kc.check_baseline(self.index,nullable)
    def test_prompt_cap(self):
        report={'commands':[dict(name='Example '+str(i),status='DRIFT') for i in range(7)]}
        line=kc.drift_prompt(report);self.assertIn('and 4 more',line);self.assertNotIn('Example 3',line)
        long={'commands':[dict(name='界'*500,status='DRIFT') for _ in range(3)]}
        self.assertLess(len(kc.drift_prompt(long).encode('utf-8')),180)
        self.assertNotIn('\n',kc.drift_prompt(long))

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.m=adapter();self.s=self.m.SC_Keybindings();self.temp=tempfile.TemporaryDirectory(prefix='sc-kb-public-adapter-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.s.generated=self.root/'generated';self.s._setting='auto';self.s._core=kc
        self.raw=mini_index();self.s._bindings=kc.LiveBindings(self.raw,self.root/'override.xml')
    def call(self,action='',key=''):return asyncio.run(self.s.look_up_keybinding(action=action,key=key))
    def test_user_words_pairs_and_doors_ports(self):
        pairs=[('which key fires flares?','Launch Countermeasure Decoy','H'),('which key is for shields?','Toggle Shields','F7'),
               ('which key locks the doors?','Toggle All Port Locks','bound'),('which key is the quantum drive?','Initiate Quantum Jump','mouse'),
               ('what does Right Alt K do?','unrelated','Port Lock'),('which key turns off the transponder?','transponder',"won't guess"),
               ('which key do I hold for full power to the engines?','power engines','F6')]
        for transcript,model,expected in pairs:
            asyncio.run(self.s.on_add_user_message('Pilot, '+transcript));result=self.call(model)
            self.assertIn(expected,result[1]);self.assertTrue(result[0].startswith('Already said aloud to the user:'))
            if 'doors' in transcript:self.assertNotIn('Right Alt',result[1])
        asyncio.run(self.s.on_add_user_message('power up the shields'));self.assertIn('O',self.call('Toggle Shields')[1])
    def test_ordinal_fresh_stale_and_hooks(self):
        asyncio.run(self.s.on_add_user_message('which key for shields?'));first=self.call('shields');self.assertTrue(first[1].endswith('?'))
        asyncio.run(self.s.on_add_user_message('which key was the first option?'));self.assertIn('F7',self.call('transponder')[1]);self.assertIsNone(self.s._last_choice)
        asyncio.run(self.s.on_add_user_message('which key for shields?'));self.call('shields')
        options,stamp=self.s._last_choice;self.s._last_choice=(options,stamp-121)
        asyncio.run(self.s.on_add_user_message('the first one'));self.assertIn("won't guess",self.call('transponder')[1])
        asyncio.run(self.s.on_add_user_message('which key for shields?'));self.assertIn('asking about a key',asyncio.run(self.s.get_prompt()))
        asyncio.run(self.s.on_add_assistant_message('planning',[{}]));self.assertIsNotNone(self.s._user_message)
        asyncio.run(self.s.on_add_assistant_message('answer',[]));self.assertEqual(asyncio.run(self.s.get_prompt()),'base')
    def test_filler_guard_cross_thread_timeline(self):
        clock=[0.05];s=self.s
        with patch.object(self.m.time,'monotonic',side_effect=lambda:clock[0]):
            line='Hold F6 for engine power.';s._arm(line)
            self.assertEqual(asyncio.run(s.on_play_to_user(line,None)),line);asyncio.run(s._playback_started('Pilot'))
            clock[0]=1.6;result=[]
            thread=threading.Thread(target=lambda:result.append(asyncio.run(s.on_play_to_user('Filler',None))));thread.start();thread.join(2)
            self.assertEqual(result,['{SKIP-TTS}']);asyncio.run(s._playback_finished('Other'));self.assertIsNotNone(s._guard)
            clock[0]=3.05;asyncio.run(s._playback_finished('Pilot'));self.assertIsNone(s._guard)
            self.assertEqual(asyncio.run(s.on_play_to_user('Normal next answer',None)),'Normal next answer')
            s._arm(line);clock[0]+=21;self.assertEqual(asyncio.run(s.on_play_to_user('Normal',None)),'Normal')
            s._arm(line);asyncio.run(s.on_add_user_message('next'));self.assertIsNone(s._guard)
    def test_endearments_and_empty(self):
        self.s.endearments='friend,,'
        self.assertIn(', friend',self.call('full power to engines')[1]);self.assertNotIn(', friend',self.call('full power to engines')[1])
        self.assertIsInstance(self.call(),str)
    def test_background_and_persisted_modes(self):
        self.s._check_drift(self.s._bindings)
        baseline=self.s.generated/'command_baseline.json';self.assertTrue(baseline.exists())
        self.assertEqual(json.loads((self.s.generated/'drift-report.json').read_text(encoding="utf-8"))['mode'],'baseline')
        cmd=commands()[0];reached=kc.resolved(cmd,self.s._bindings.engine,curated=True)
        (self.s.generated/'command_manifest.json').write_text(json.dumps({'commands':[dict(name=cmd['name'],fingerprint=kc.command_fingerprint(cmd),expectedActions=reached,baselineActions=reached,verifiable=True)]}), encoding="utf-8")
        self.s._check_drift(self.s._bindings);self.assertEqual(json.loads((self.s.generated/'drift-report.json').read_text(encoding="utf-8"))['mode'],'curated')
        started,finish=threading.Event(),threading.Event()
        def refresh(setting):started.set();finish.wait(2)
        with patch.object(self.s,'_refresh',side_effect=refresh):
            asyncio.run(self.s.prepare());self.assertTrue(started.wait(1));self.assertIn('refreshing',self.call('shields')[1]);finish.set();self.s._thread.join(2)
        asyncio.run(self.s.unload());self.assertEqual(self.s._subscriptions,[])
    def test_extractor_selection_and_failure(self):
        game=self.root/'LIVE';game.mkdir();(game/'Data.p4k').write_bytes(b'fixture');(game/'build_manifest.id').write_text('{"BuildId":"synthetic"}', encoding="utf-8")
        from sc_voice_cockpit.keybindings import p4k_reader
        self.raw['metadata'].update(p4kSize=7,p4kMtime=p4k_reader._iso((game/'Data.p4k').stat().st_mtime))
        def write_index(*a,**k):
            target=Path(a[0][-1] if isinstance(a[0],list) else a[1])
            target.parent.mkdir(exist_ok=True);target.write_text(json.dumps(self.raw), encoding="utf-8")
        reader=types.SimpleNamespace(extract=write_index,atomic_json=lambda *a:None,_iso=p4k_reader._iso)
        original=self.m._sibling_module
        def sibling(name):return reader if name=='p4k_reader' else original(name)
        for node,failed in [(None,False),('node',False),('node',True)]:
            with self.subTest(node=node,failed=failed):
                cache=self.s.generated/'defaults.json'
                if cache.exists():cache.unlink()
                with patch.object(self.m,'locate_game',return_value=game),patch.object(self.m.shutil,'which',return_value=node),patch.object(self.m,'_sibling_module',side_effect=sibling),patch.object(self.m.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['node']) if failed else write_index) as run,patch.object(reader,'extract',side_effect=write_index) as py:
                    self.s._refresh('auto');self.assertFalse(self.s._error)
                    self.assertEqual(py.call_count,int(node is None or failed));self.assertEqual(run.call_count,int(node is not None))
                    if node and not failed:self.assertNotIn('shell',run.call_args.kwargs);self.assertEqual(run.call_args.kwargs['timeout'],180)
        cache.unlink()
        with patch.object(self.m,'locate_game',return_value=game),patch.object(self.m.shutil,'which',return_value=None),patch.object(self.m,'_sibling_module',side_effect=sibling),patch.object(reader,'extract',side_effect=ValueError('synthetic decoder failure')):
            self.s._refresh('auto');self.assertIn('synthetic decoder failure',self.s._error);self.assertTrue(self.s._stale)
