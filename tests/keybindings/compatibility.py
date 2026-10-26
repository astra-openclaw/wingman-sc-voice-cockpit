"""Actual Wingman 3.2.11 schema/base/decorator/loader; inert desktop peripherals.

Run with the existing probe venv and --reference plus --loader (official source).
No reference implementation is included in the release ZIP.
"""
import argparse
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True  # The version-matched reference tree is read-only.
import tempfile
import types
from unittest.mock import patch
import tiktoken
import yaml
from fixtures import mini_index,commands
ROOT=Path(__file__).resolve().parents[2]

def stub(name,**attrs):
    m=types.ModuleType(name);m.__dict__.update(attrs);sys.modules[name]=m;return m

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

class Events:
    def subscribe(self,*a):pass
    def unsubscribe(self,*a):pass
class SecretKeeper:
    def __init__(self):self.secret_events=Events()
class Printr:
    def print(self,*a,**k):pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--reference',type=Path,required=True);ap.add_argument('--loader',type=Path,required=True);ap.add_argument('--speech',type=Path);args=ap.parse_args()
    sys.path.insert(0,str(args.reference))
    from api.interface import SkillConfig,CommandConfig
    import pydantic
    config=SkillConfig(**yaml.safe_load((ROOT/'sc_voice_cockpit/keybindings/default_config.yaml').read_text(encoding="utf-8")))
    with tempfile.TemporaryDirectory(prefix='sc-kb-public-compat-') as d:
        folder=Path(d);custom=folder/'custom';shutil.copytree(ROOT/'sc_voice_cockpit/keybindings',custom/'sc_keybindings',ignore=shutil.ignore_patterns('__pycache__'))
        stub('services.benchmark',Benchmark=type('Benchmark',(),{}));stub('services.printr',Printr=Printr);stub('services.secret_keeper',SecretKeeper=SecretKeeper)
        stub('services.file',get_custom_skills_dir=lambda:str(custom),get_generated_files_dir=lambda name:str(folder/'generated'/name))
        facade=load('wingmen.facade',args.reference/'wingmen/facade.py')
        load('skills.skill_base',args.reference/'skills/skill_base.py');manager=load('services.module_manager',args.loader)
        manager.ModuleManager.probe_import(config);module=manager.ModuleManager.resolve_skill_module(config)
        tools=[v for v in vars(module.SC_Keybindings).values() if hasattr(v,'_tool_definition')];assert len(tools)==1
        schema=tools[0]._tool_definition.tool_schema;compact=json.dumps(schema,separators=(',',':'),ensure_ascii=False);enc=tiktoken.get_encoding('o200k_base')
        obj=module.SC_Keybindings(config=config,settings=types.SimpleNamespace(),wingman=types.SimpleNamespace())
        assert asyncio.run(obj.validate())==[];assert obj._thread is None
        core=load('_public_compat_core',ROOT/'sc_voice_cockpit/keybindings/keybinds_core.py')
        for name in ('main','keybinds_core','p4k_reader','self_heal'):
            spec=importlib.util.spec_from_file_location('_no_work_'+name,ROOT/'sc_voice_cockpit/keybindings'/f'{name}.py')
            with patch.object(Path,'open',side_effect=AssertionError('file work at import')),patch.object(module.subprocess,'run',side_effect=AssertionError('process at import')),patch.object(module.threading.Thread,'start',side_effect=AssertionError('thread at import')):
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
        # Older per-wingman overrides omit 1.3 properties wholesale; no validation errors.
        partial_config=config.model_copy(deep=True)
        partial_config.custom_properties=[p for p in config.custom_properties if p.id in ('game_folder','endearments')]
        partial=module.SC_Keybindings(config=partial_config,settings=types.SimpleNamespace(),wingman=types.SimpleNamespace())
        assert asyncio.run(partial.validate())==[]
        assert partial._optional('auto_update_commands',True) is True
        assert partial._optional('restore_custom_binds',False) is False
        raw=mini_index();index=core.KeybindingIndex(raw)
        # Genuine Pydantic commands include system null actions and numeric normalization.
        configured=[dict(name='Prepare Ship',actions=[dict(keyboard=dict(hotkey='ralt+r',hold=0.1))]),dict(name='No Actions',actions=None)]
        live=[CommandConfig(**c) for c in configured]
        report,baseline=core.check_baseline(index,live);assert len(report['commands'])==1;assert report['commands'][0]['status']=='OK'
        # Fingerprints compare the model's normalized serialization in both modes.
        c=core.command_dict(live[0]);reached=core.resolved(c,index,curated=True)
        manifest={'commands':[dict(name=c['name'],fingerprint=core.command_fingerprint(c),expectedActions=reached,baselineActions=reached,verifiable=True)]}
        assert core.check_commands(index,live,manifest)['commands'][0]['status']=='OK'
        moved=mini_index();next(a for a in moved['actions'] if a['name']=='v_flightready')['keyboard'][0]['input']='x';changed=core.KeybindingIndex(moved)
        assert core.check_baseline(changed,live,baseline)[0]['commands'][0]['status']=='DRIFT'
        assert core.check_commands(changed,live,manifest)['commands'][0]['status']=='DRIFT'
        # Actual facade and CommandActionConfig.model_validate on the existing model class.
        healer=load('_public_compat_heal',ROOT/'sc_voice_cockpit/keybindings/self_heal.py')
        import threading
        saved=[]
        old=core.command_dict(live[0])
        intent=healer.steps(old,index,core)
        repaired,changes=healer.repair(old,intent,changed,core)
        save_attempts=[]
        def tower_save(name):
            save_attempts.append((name,threading.get_ident()))
            if len(save_attempts)<=2:return False  # prepare precedes tower registration
            saved.append((name,threading.get_ident()));return True
        tower=types.SimpleNamespace(save_wingman_commands=tower_save)
        obj._save_retry_seconds=.001
        holder=types.SimpleNamespace(name='Pilot',config=types.SimpleNamespace(commands=live),tower=tower)
        obj.wingman.name='Pilot';obj.wingman.commands=facade.SkillCommands(holder);obj._core=core;obj._setting='auto'
        original_metadata={k:v for k,v in old.items() if k!='actions'}
        async def apply():
            obj._loop=asyncio.get_running_loop()
            # Exercise the worker->captured-loop handoff, not only a direct await.
            def worker():
                asyncio.run_coroutine_threadsafe(obj._apply_repairs([(old,repaired)]),obj._loop).result(timeout=5)
            await asyncio.to_thread(worker)
            assert saved==[('Pilot',threading.get_ident())]
            assert save_attempts==[('Pilot',threading.get_ident())]*3
        asyncio.run(apply())
        assert live[0].actions[0].keyboard.hotkey=='x'
        assert isinstance(live[0].actions[0],type(CommandConfig(**configured[0]).actions[0]))
        assert {k:v for k,v in core.command_dict(live[0]).items() if k!='actions'}==original_metadata
        assert core.check_baseline(changed,live)[0]['commands'][0]['status']=='OK'
        # Real nullable system command remains untouched.
        assert live[1].actions is None

        # Public command action is separate from the one model-visible tool.
        assert set(obj._command_actions)=={'fresh_start_keybindings'}
        assert obj._command_actions['fresh_start_keybindings'].respond=='speak'
        action_runs=[];dialogs=[]
        def fresh_sync(trigger):
            action_runs.append((trigger,threading.get_ident()))
            return dict(spoken='Fresh start done.',dialog='Fresh start details.')
        async def show_dialog(title,text):
            dialogs.append((title,text))
        obj.wingman.ui=types.SimpleNamespace(show_dialog=show_dialog)
        async def fresh_triggers():
            loop_thread=threading.get_ident()
            with patch.object(obj,'_fresh_start_sync',side_effect=fresh_sync):
                result=await obj.execute_command_action('fresh_start_keybindings',{})
                assert result==('Fresh start done.','Fresh start done.')
                on=config.model_copy(deep=True)
                next(p for p in on.custom_properties if p.id=='fresh_start').value=True
                await obj.update_config(on)
                await obj._fresh_task
                await obj.update_config(on.model_copy(deep=True))
                assert [r[0] for r in action_runs]==['command','switch']
                assert all(t!=loop_thread for _,t in action_runs)
                assert len(dialogs)==1
                await obj.update_config(config.model_copy(deep=True))
                assert not obj._optional('fresh_start',False)
        asyncio.run(fresh_triggers())
        obj._bindings=core.LiveBindings(raw,folder/'override.xml')
        asyncio.run(obj.on_add_user_message('Pilot, which key locks the doors?'));result=asyncio.run(obj.look_up_keybinding(action='Toggle All Port Locks'))
        assert 'bound' in result[1] and 'Right Alt' not in result[1]
        asyncio.run(obj.on_add_user_message('which key for shields?'));asyncio.run(obj.look_up_keybinding(action='shields'))
        asyncio.run(obj.on_add_user_message('which key was the first option?'));assert 'F7' in asyncio.run(obj.look_up_keybinding(action='transponder'))[1]
        asyncio.run(obj.on_add_assistant_message('done',[]))
        measure=lambda:len(enc.encode(asyncio.run(obj.get_prompt())+'\n'+compact))
        tokens=dict(prompt=len(enc.encode(config.prompt)),schema=len(enc.encode(compact)),base=measure())
        obj._drift={'commands':[dict(name=n,status='DRIFT') for n in ('Prepare Ship','Toggle Port Locks','Cycle Turret Mouse Mode')]}
        tokens['with_drift']=measure();asyncio.run(obj.on_add_user_message('which key for shields?'));tokens['with_both']=measure()
        obj._drift={};tokens['with_nudge']=measure()
        obj._drift={'commands':[dict(name=n,status='DRIFT') for n in ('界'*500,'xYz@0123456789'*100,'abcdefghij'*500)]}
        tokens['long_name_both_probe']=measure()
        # Worst-case concurrent drift/nudge/change hint with bounded adversarial names.
        worst=dict(changed=['a']*200,cause='game update',restored=['a']*20,
                   custom_updates=['a']*20,deferred=['a']*20,
                   attention=[dict(name='界'*500,reason='')]*31,
                   fixed=[dict(name='界'*500,steps=[dict(**{'from':'ralt+f12','to':'rctrl+f24'})])]*30)
        import time
        obj._change_note=dict(prompt=healer.change_line(worst,core),spoken=healer.spoken_change(worst,core),at=time.monotonic())
        import itertools
        token_cases=[];change_cases=[]
        for bits in itertools.product((False,True),repeat=6):
            case={k:(v if enabled else []) for k,v,enabled in zip(
                ('changed','restored','custom_updates','deferred','attention','fixed'),
                (worst[k] for k in ('changed','restored','custom_updates','deferred','attention','fixed')),bits)}
            case['cause']=worst['cause']
            obj._change_note=dict(prompt=healer.change_line(case,core),spoken=healer.spoken_change(case,core),at=time.monotonic())
            token_cases.append(measure());change_cases.append(len(enc.encode(obj._change_note['prompt'])))
            assert len(obj._change_note['spoken'].split())<=30
        # Word-safe 32-byte names and the longest displayed key chords too;
        # include partial removals alongside all existing report combinations.
        for name in ('Secure All Hatches','xYz@0123456789 xYz@0123456789','界'*500,'Long Command Name With Several Words'):
            for keys in (('ralt+3','ralt+f3'),('rctrl+rshift+np_9','rctrl+rshift+np_enter')):
                for bits in itertools.product((False,True),repeat=6):
                    case={k:(v if enabled else []) for k,v,enabled in zip(
                        ('changed','restored','custom_updates','deferred','attention','fixed'),
                        (worst[k] for k in ('changed','restored','custom_updates','deferred','attention','fixed')),bits)}
                    case['cause']=worst['cause']
                    case['fixed']=[dict(name=name,steps=[{'from':keys[0],'to':keys[1]}])]*30 if bits[-1] else []
                    for updates in (case['custom_updates'],[dict(removed=True)]*20 if bits[2] else []):
                        case['custom_updates']=updates
                        obj._change_note=dict(prompt=healer.change_line(case,core),spoken=healer.spoken_change(case,core),at=time.monotonic())
                        token_cases.append(measure());change_cases.append(len(enc.encode(obj._change_note['prompt'])))
                        assert len(obj._change_note['spoken'].split())<=30
                        assert len(obj._change_note['spoken'])<=361
        assert len(obj._change_note['spoken'].split())<=30
        assert "Mention briefly once." not in healer.log_line(case,core)
        tokens['all_lines_worst_case']=max(token_cases)
        tokens['change_line_worst_case']=max(change_cases)
        assert tokens['change_line_worst_case']<=65,tokens
        assert max(tokens.values())<=250,tokens
        cleaned=0
        if args.speech:
            load('services.spoken_numbers',args.speech/'spoken_numbers.py');speech=load('services.speech_text',args.speech/'speech_text.py');markdown=load('services.markdown',args.speech/'markdown.py')
            from api.enums import SpokenLanguage
            for q in ('quantum drive','shields','transponder','flares','full power to engines','lock all doors','what does Right Alt K do'):
                line=index.spoken(q);actual=markdown.cleanup_text(line)[0];obj._arm(line)
                assert asyncio.run(obj.on_play_to_user(actual,None))==actual
                assert obj._guard['text']==obj._normalized(actual)
                assert speech.prepare_for_speech(actual,SpokenLanguage.EN)==actual
                cleaned+=1
            # Exercise the actual instant-answer path with a pending spoken note,
            # then pass the entire appended text through 3.2.11 speech cleanup.
            spoken_report=dict(changed=['a','b'],cause='your controls',fixed=[dict(name='Secure All Hatches',steps=[{'from':'ralt+3','to':'ralt+f3'}])],restored=[],deferred=[],custom_updates=[],attention=[])
            summary=healer.spoken_change(spoken_report,core)
            obj._change_note=dict(prompt=healer.change_line(spoken_report,core),spoken=summary,at=time.monotonic())
            asyncio.run(obj.on_add_user_message('Pilot, which key gives full power to engines?'))
            response,line=asyncio.run(obj.look_up_keybinding(action='full power to engines'))
            assert line.endswith(summary) and obj._change_note is None
            actual=markdown.cleanup_text(line)[0]
            assert asyncio.run(obj.on_play_to_user(actual,None))==actual
            assert obj._guard['text']==obj._normalized(actual)
            assert asyncio.run(obj.on_play_to_user('Unrelated filler',None))=='{SKIP-TTS}'
            prepared=speech.prepare_for_speech(actual,SpokenLanguage.EN)
            expected=actual.replace('2 keys moved','two keys moved').replace('1 voice command','one voice command')
            assert prepared==expected  # Normal count verbalization, chord F3 unchanged.
            assert len(response)<=1200 and len(line)<=1200
            cleaned+=1
        asyncio.run(obj.unload())
        for p in (ROOT/'sc_voice_cockpit/keybindings').glob('*.py'):ast.parse(p.read_text(encoding="utf-8"),feature_version=(3,11))
        result=dict(python=sys.version.split()[0],pydantic=pydantic.__version__,SkillConfig='PASS',ModuleManager_probe_import='PASS (official source, custom-folder bare-main fallback)',Skill_base_and_decorator='PASS',fresh_start_command_and_config='PASS (real command_action registry, execute tuple, SkillConfig edges, dialog, off-loop worker)',CommandConfig_baseline_and_curated='PASS (OK and DRIFT, nullable system command)',real_CommandActionConfig_mutation='PASS (model_validate, captured-loop handoff, facade.save, tower returns False twice before registration)',partial_property_override='PASS (new ids absent)',no_import_time_work='PASS',actual_cleanup_lines=cleaned,appended_summary_speech='PASS (count verbalization, command/key names preserved, full-line filler guard)',token_budget_o200k_base=tokens,schema=schema)
        (ROOT/'tests/compatibility-results.json').write_text(json.dumps(result,indent=2), encoding="utf-8");print(json.dumps(result,indent=2))

if __name__=='__main__':main()
