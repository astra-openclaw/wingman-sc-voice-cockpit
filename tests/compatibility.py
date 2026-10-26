"""Genuine 3.2.11 schema/base/catalog/loader/facade qualification, no external effects."""
import argparse
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
from unittest.mock import AsyncMock, Mock, patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module
def stub(name,**values):
    m=types.ModuleType(name);m.__dict__.update(values);sys.modules[name]=m;return m
class Events:
    def subscribe(self,*args):pass
    def unsubscribe(self,*args):pass
class SecretKeeper:
    def __init__(self):self.secret_events=Events()
class Printr:
    def print(self,*args,**kwargs):pass
    async def print_async(self,*args,**kwargs):pass
class Benchmark:
    def __init__(self,*args):pass
    def finish(self):return None

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--reference",type=Path,required=True)
    parser.add_argument("--loader",type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.reference));sys.dont_write_bytecode=True
    stub("services.benchmark",Benchmark=Benchmark);stub("services.printr",Printr=Printr)
    stub("services.secret_keeper",SecretKeeper=SecretKeeper)
    from api.interface import SkillConfig,CommandConfig,MicStatusResponse
    from api.enums import ConversationProvider
    with tempfile.TemporaryDirectory(prefix="cockpit-compat-") as temp:
        directory=Path(temp);custom=directory/"custom"
        shutil.copytree(ROOT/"sc_voice_cockpit",custom/"sc_voice_cockpit",ignore=shutil.ignore_patterns("__pycache__"))
        stub("services.file",get_custom_skills_dir=lambda:str(custom),get_generated_files_dir=lambda name:str(directory/"generated"/name))
        facade=load("wingmen.facade",args.reference/"wingmen/facade.py")
        base=load("skills.skill_base",args.reference/"skills/skill_base.py")
        manager=load("services.module_manager",args.loader)
        stub("services.platform_utils",normalize_platform=lambda *args:"windows")
        catalog=load("services.skill_catalog",args.reference/"services/skill_catalog.py")
        config=SkillConfig(**yaml.safe_load((custom/"sc_voice_cockpit/default_config.yaml").read_text(encoding="utf-8")))
        # Official custom-folder import cannot start work or read user-data files.
        with patch.object(threading.Thread,"start",side_effect=AssertionError("thread at catalog import")), patch.object(Path,"open",side_effect=AssertionError("data I/O at catalog import")):
            manager.ModuleManager.probe_import(config)
            module=manager.ModuleManager.resolve_skill_module(config)
        entry=catalog.SkillCatalog()._evaluate("sc_voice_cockpit",str(custom/"sc_voice_cockpit/default_config.yaml"),True)
        assert entry.outcome=="ok",entry
        wing=types.SimpleNamespace(name="Pilot")
        obj=module.SC_VoiceCockpit(config=config,settings=types.SimpleNamespace(),wingman=wing)
        assert asyncio.run(obj.validate())==[]
        assert obj._thread is None and obj._deck is None and obj._db is None
        assert "correct_tablet_light" in obj._decorated_tools
        assert "look_up_keybinding" in obj._decorated_tools and "mark_location" in obj._decorated_tools
        assert {"deck_sync","show_tablet_deck_pairing","fresh_start_keybindings"}<=obj._command_actions.keys()
        assert obj._command_actions["deck_sync"].respond=="ai"
        prompt=asyncio.run(obj.get_prompt())
        assert config.prompt==prompt
        assert "remembered, not read from the game" in prompt and "do not toggle the ship" in prompt
        assert "Emotes only play on foot" in prompt
        light_schema=next(schema for name,schema in obj.get_tools() if name=="correct_tablet_light")
        parameters=light_schema["function"]["parameters"]
        assert parameters["properties"]["on"]["type"]=="boolean"
        assert set(parameters["required"])=={"group","on"}
        assert len(parameters["properties"]["group"]["enum"])==13
        assert "Planetary coordinates take priority" in prompt
        setup_prop=next(p for p in config.custom_properties if p.id=="deck_setup")
        assert setup_prop.value is False and "Star Citizen must be closed" in setup_prop.hint
        # Older override containing none of the new properties still uses defaults.
        obj.config=config.model_copy(update={"custom_properties":[]})
        assert asyncio.run(obj.validate())==[]
        assert obj._get_display()==1 and obj.deck_setting("deck_port",7869)==7869
        obj.config=config
        from sc_voice_cockpit.deck.runtime import DeckRuntime
        obj._deck=DeckRuntime(obj)
        obj._deck.manifest["groups"]=["vtol"]
        light_messages=[];obj._deck.broadcast=light_messages.append
        assert "corrected" in asyncio.run(obj.correct_tablet_light("vtol",True))
        assert obj._deck.states=={"vtol":True} and light_messages==[{"t":"state","group":"vtol","on":True}]
        assert "invalid" in asyncio.run(obj.correct_tablet_light("vtol",1))
        assert "invalid" in asyncio.run(obj.correct_tablet_light("unknown",True))
        notes=[]
        wing.conversation=types.SimpleNamespace(add_note=AsyncMock(side_effect=lambda line:notes.append(line)))
        obj._deck.add_note("Tablet press")
        assert not notes
        asyncio.run(obj.on_add_user_message("next turn"))
        assert notes==["Tablet press"]
        # Cross-loop sync action: no text or instant response is emitted.
        obj._deck.manifest={"commands":{"Gear":{"effects":[{"group":"gear","set":True}]}}}
        assert asyncio.run(obj.deck_sync("Gear"))==""
        obj._deck.sync=lambda _:(_ for _ in ()).throw(RuntimeError())
        assert asyncio.run(obj.deck_sync("Gear"))==""
        obj._arm("Confirmed.")
        assert asyncio.run(obj.on_play_to_user("Confirmed.",None))=="Confirmed."
        assert asyncio.run(obj.on_play_to_user("Filler",None))=="{SKIP-TTS}"
        messages=[];obj._deck.broadcast=messages.append
        status=MicStatusResponse(state="held",listening=False,recording=True,recording_wingman="Pilot",voice_activation_enabled=False,playing=False)
        asyncio.run(obj._deck_mic_status(status));assert messages[-1]["on"]
        status.recording_wingman="Other"
        asyncio.run(obj._deck_mic_status(status));assert not messages[-1]["on"]
        # Both pairing cards use the durable code URL, same QR, unchanged 15s delay.
        pairing=types.SimpleNamespace(pairing_url="http://127.0.0.1:7869/?t=synthetic",pairing_code="synthetic")
        obj._ui_server=pairing
        obj.wingman.ui=types.SimpleNamespace(show_dialog=AsyncMock())
        with patch("sc_voice_cockpit.navpoint.navpoint_ui.sharing.qr_png_base64",return_value="synthetic-qr") as qr:
            asyncio.run(obj.show_tablet_deck_pairing())
            qr.assert_called_once_with(pairing.pairing_url)
            assert pairing.pairing_url in obj.wingman.ui.show_dialog.call_args.args[1]
            assert obj.wingman.ui.show_dialog.call_args.kwargs["image"]=="data:image/png;base64,synthetic-qr"
            with patch.object(module.asyncio,"sleep",new_callable=AsyncMock) as delay:
                asyncio.run(obj._announce_ui_startup(pairing))
                delay.assert_awaited_once_with(15)
            assert pairing.pairing_url in obj.wingman.ui.show_dialog.call_args.args[1]
        # Exercise the combined prepare/unload lifecycle, stubbing only capture,
        # game-index work and Wingman effects; the one loopback server is real.
        subscriptions=[]
        def subscribe(callback):
            row=types.SimpleNamespace(unsubscribed=False)
            row.unsubscribe=lambda:setattr(row,"unsubscribed",True)
            subscriptions.append(row);return row
        pilot=types.SimpleNamespace(name="Test Pilot",audio=types.SimpleNamespace(
            on_playback_started=subscribe,on_playback_finished=subscribe,on_mic_status_changed=subscribe))
        prepared=module.SC_VoiceCockpit(config=config,settings=types.SimpleNamespace(),wingman=pilot)
        prepared.get_generated_files_dir=lambda:str(directory/"lifecycle")
        prepared._trace_build_identity=lambda:None
        prepared._get_overlay_enabled=lambda:False
        prepared._start_background_calibration=lambda:None
        prepared._start_refresh=lambda _:None
        original_setting=prepared.deck_setting
        prepared.deck_setting=lambda n,d:0 if n=="deck_port" else original_setting(n,d)
        from sc_voice_cockpit.navpoint.navpoint_ui import sharing
        async def lifecycle():
            with patch.object(sharing,"get_lan_ip",return_value=None):
                await prepared.prepare()
                assert prepared._ui_server and prepared._ui_server._thread.is_alive()
                prepared._ui_window.open=lambda:None
                assert json.loads(prepared.show_navpoint_hud())["url"].endswith("/nav")
                first=prepared._ui_server
                await prepared.prepare()  # repeated prepare must not leave the first listener
                assert not first.is_running
                second=prepared._ui_server
                worker=prepared._deck.thread;feedback=prepared._deck.feedback.thread
                await prepared.unload()
                assert not second.is_running and not worker.is_alive() and not feedback.is_alive()
                assert all(s.unsubscribed for s in subscriptions)
        asyncio.run(lifecycle())
        # Ring-only config updates reuse native stop/start and do not refresh
        # bindings, restart capture/calibration/server, or reload tablets.
        ring_obj=module.SC_VoiceCockpit(config=config.model_copy(deep=True),settings=types.SimpleNamespace(),wingman=pilot)
        ring_obj._closed=False;ring_obj._deck=DeckRuntime(ring_obj)
        ring_obj._deck.broadcast=Mock();ring_obj._bindings=object()
        ring_obj._start_refresh=Mock();ring_obj._start_background_calibration=Mock()
        ring_obj._start_cockpit_web=AsyncMock();ring_obj._ui_server=types.SimpleNamespace(stop=Mock())
        rings=[]
        class FakeRing:
            def __init__(self,size,display):
                self.size=size;self.display=display;self.stop=Mock();self.clear=Mock();self.start=Mock(return_value=True)
                rings.append(self)
        ring_module=types.ModuleType("sc_voice_cockpit.navpoint.ring_overlay");ring_module.RingOverlay=FakeRing
        def ring_config(display,size):
            updated=config.model_copy(deep=True)
            for prop in updated.custom_properties:
                if prop.id=="display":prop.value=display
                if prop.id=="overlay_size":prop.value=size
            return updated
        with patch.dict(sys.modules,{"sc_voice_cockpit.navpoint.ring_overlay":ring_module}):
            asyncio.run(ring_obj.update_config(ring_config(2,220)))
            assert [(r.display,r.size) for r in rings]==[(2,220)]
            asyncio.run(ring_obj.update_config(ring_config(2,220)))
            assert len(rings)==1
            asyncio.run(ring_obj.update_config(ring_config(2,320)))
            assert [(r.display,r.size) for r in rings]==[(2,220),(2,320)]
            rings[0].stop.assert_called_once();rings[1].start.assert_called_once();rings[1].clear.assert_called_once()
        for check in (ring_obj._start_refresh,ring_obj._start_background_calibration,
                      ring_obj._start_cockpit_web,ring_obj._ui_server.stop,ring_obj._deck.broadcast):
            check.assert_not_called()
        display_prop=next(p for p in config.custom_properties if p.id=="display")
        assert display_prop.name=="Ring overlay monitor if the game window isn't found (1 = primary)"
        assert ring_obj._get_display()==2
        # Actual facade AI path cannot expose tools.
        stub("services.token_utils",count_tokens=lambda s:len(s.split()),truncate_to_tokens=lambda s,n:s)
        stub("services.context_budget",ContextOverflowError=type("Overflow",(Exception,),{}),SUBSCRIPTION_MAX_REQUEST_CHARS=100000,DEFAULT_WINDOW=32000,ContextBudget=type("Budget",(),{}))
        stub("services.conversation_manager",ConversationManager=types.SimpleNamespace(message_chars=lambda m:len(str(m))),NOTE_PREFIX="[note:")
        calls=[]
        async def llm(messages,tools=None,tool_choice="auto"):
            calls.append(tools)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="Acknowledged."))])
        raw=types.SimpleNamespace(config=types.SimpleNamespace(features=types.SimpleNamespace(conversation_provider=ConversationProvider.OPENAI)),
                                  context_budget=types.SimpleNamespace(tool_cap=32000),actual_llm_call=llm)
        assert asyncio.run(facade.SkillAi(raw).generate("completed",system="pilot"))=="Acknowledged."
        assert calls==[None]
        tree=ast.parse((args.reference/"wingmen/wingman.py").read_text(encoding="utf-8"))
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=="actual_llm_call")
        assert isinstance(fn.args.defaults[0],ast.Constant) and fn.args.defaults[0].value is None
        # Prove named force-instant dispatch through the genuine ToolExecutor class.
        stub("services.persistent_memory",MEMORY_TOOL_NAMES=set())
        stub("services.tool_response_limiter",ToolResponseLimiter=type("Limiter",(),{}))
        executor=load("services.tool_executor",args.reference/"services/tool_executor.py")
        command=CommandConfig(name="Emote Wave",force_instant_activation=True,actions=[{"keyboard":{"hotkey":"f1","hold":.1}}])
        dispatched=[]
        async def execute(c,*args):
            dispatched.append(c);return None,"OK"
        # Every earlier meta-tool gate is inert, not the command dispatcher.
        registry=types.SimpleNamespace(is_meta_tool=lambda n:False,is_mcp_tool=lambda n:False)
        tool_executor=executor.ToolExecutor.__new__(executor.ToolExecutor)
        tool_executor.hud_tool_runner=None
        async def route(name,arguments):
            return await tool_executor.execute_by_function_call(name,arguments,tool_skills={},skill_registry=registry,mcp_registry=registry,
                capability_registry=registry,persistent_memory_service=None,get_command_fn=lambda n:command if n==command.name else None,
                execute_command_fn=execute,play_to_user_fn=AsyncMock())
        raw_wing=types.SimpleNamespace(execute_command_by_function_call=route)
        result=asyncio.run(facade.SkillTools(raw_wing).invoke("execute_command",{"command_name":command.name}))
        assert dispatched==[command] and result.response=="OK"
        # The real executor must retain Right Ctrl scan 29 + extended. The
        # keyboard package and sleeps are recording fakes, never desktop input.
        keycalls=[]
        keyboard_package=stub("keyboard")
        keyboard_package.keyboard=stub("keyboard.keyboard",
            direct_event=lambda code,flags:keycalls.append(("direct",code,flags)),
            press=lambda key:keycalls.append(("press",key)),release=lambda key:keycalls.append(("release",key)),
            send=lambda *args:keycalls.append(("send",*args)))
        mouse_package=stub("mouse");mouse_package.mouse=stub("mouse.mouse")
        stub("services.audio_library",AudioLibrary=type("AudioLibrary",(),{}))
        stub("services.name_match",without_name=lambda text,*args:text,words_of=lambda text:text.split())
        command_executor=load("services.command_executor",args.reference/"services/command_executor.py")
        from sc_voice_cockpit.keybindings import self_heal as healer,keybinds_core as core
        action_command=CommandConfig(name="Synthetic Right Ctrl",actions=healer.render("rctrl+w",.1,core))
        keyrunner=command_executor.CommandExecutor.__new__(command_executor.CommandExecutor)
        keyrunner.on_execute_skill_action=None
        with patch.object(command_executor.time,"sleep"):
            asyncio.run(keyrunner.execute_action(action_command))
        assert keycalls==[("direct",29,1),("press","w"),("release","w"),("direct",29,3)],keycalls
        result={"live_ring_settings_without_other_side_effects":"PASS","light_tool_schema_and_behavior":"PASS","genuine_loader":"PASS","catalog":"ok","api_version":3,"tool_count":len(obj._decorated_tools),
                "command_actions":sorted(obj._command_actions),"durable_pairing_cards_and_15s_delay":"PASS","merged_prompt_contract":"PASS","partial_override_defaults":"PASS",
                "notes_at_next_turn_only":"PASS","deck_sync_silent_exception_safe":"PASS","mic_callback_schema":"PASS",
                "filler_guard":"PASS","inert_catalog_import":"PASS","prepare_reload_unload":"PASS (real single listener; fake capture/Core effects)","ai_no_tools":"PASS (genuine facade; actual_llm_call tools defaults None)",
                "force_instant_named_invoke":"PASS (genuine facade and ToolExecutor)",
                "right_ctrl_extended_execution":"PASS (genuine CommandExecutor; recorded direct_event 29/1 and 29/3)"}
        from public_setup_checks import qualify as qualify_setup
        result["fresh_player"]=qualify_setup(module,facade,config)
        print(json.dumps(result,indent=2))
        (ROOT/"tests/cockpit-compatibility-results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
if __name__=="__main__":main()
