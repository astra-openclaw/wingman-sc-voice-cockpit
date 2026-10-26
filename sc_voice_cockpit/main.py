"""Wingman 3.2.11 public Skill API integration. Catalog import is inert."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
import threading
import time
import types
from typing import Literal

# Core's custom loader executes main.py as a bare module. Publish only OUR
# package path, not a global sys.path entry or aliases to installed skills.
if "sc_voice_cockpit" not in sys.modules:
    package=types.ModuleType("sc_voice_cockpit")
    package.__path__=[str(Path(__file__).parent)]
    sys.modules["sc_voice_cockpit"]=package

from skills.skill_base import Skill, command_action, tool
from sc_voice_cockpit.keybindings.main import SC_Keybindings
from sc_voice_cockpit.navpoint.main import SC_NavPoint
from sc_voice_cockpit.deck.runtime import DeckRuntime, TABLET_COMMAND, DECK_SPEECH

class SC_VoiceCockpit(SC_Keybindings, SC_NavPoint):
    VERSION = "1.2.1"

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._deck=None
        self._deck_subscriptions=[]
        self._pack_lock=threading.RLock()
        self._announcement=None
        self._pack_pending_notified=False
        self._setup_card_shown=False
        self._setup_override=None

    def retrieve_custom_property_value(self,name,errors=None):
        defaults={"game_folder":"auto","endearments":"","auto_update_commands":True,
            "restore_custom_binds":False,"fresh_start":False,"display":1,
            "arrival_alert_distance":1000,"arrival_stop_distance":250,"overlay_size":220,
            "deck_setup":False,"deck_enabled":True,"deck_port":7869,"deck_feedback":"ai","deck_show_in_hud":True,"deck_tablet_mic":True,"deck_emotes":True}
        if name in defaults and not any(p.id==name for p in (getattr(self.config,"custom_properties",None) or [])):
            return defaults[name]
        return super().retrieve_custom_property_value(name,errors if errors is not None else [])

    def deck_setting(self,name,default):
        try:
            props=getattr(self.config,"custom_properties",None) or []
            prop=next((p for p in props if p.id==name),None)
            return prop.value if prop is not None and prop.value is not None else default
        except (AttributeError,KeyError,ValueError):return default

    async def on_play_to_user(self, text, sound_config):
        text = await super().on_play_to_user(text, sound_config)
        if self._deck is not None and (TABLET_COMMAND.get() or DECK_SPEECH.get()):
            await self._deck.show_on_hud(text)
        return text

    def deck_setup_enabled(self):
        """One-time legacy migration, then durable choices through update_config.

        Skill API v3 receives merged defaults, not raw saved-property provenance.
        A pre-1.2 manifest is the migration receipt; current-version previews are not.
        """
        if self._setup_override is not None:return self._setup_override
        directory=Path(self.get_generated_files_dir())
        state=directory/"deck_setup.json"
        try:
            saved=json.loads(state.read_text(encoding="utf-8"))
            if type(saved.get("enabled")) is bool:
                self._setup_override=bool(self.deck_setting("deck_setup",False)) or (saved.get("migrated") is True and saved["enabled"])
                return self._setup_override
        except (OSError,ValueError,TypeError,AttributeError):pass
        migrated=False
        enabled=bool(self.deck_setting("deck_setup",False))
        if not enabled:
            try:
                old=json.loads((directory/"deck_manifest.json").read_text(encoding="utf-8"))
                enabled=(old.get("version") in ("1.0.0","1.1.0","1.1.1") and
                         bool(old.get("commands")) and any(
                             b.get("kind")=="command" and b.get("command") in old["commands"]
                             for b in old.get("buttons",{}).values()))
                migrated=enabled
            except (OSError,ValueError,TypeError,AttributeError):pass
        self._setup_override=enabled
        # Retain this decision across restart; never turn a 1.2 preview into consent.
        from sc_voice_cockpit.keybindings.self_heal import atomic_json
        directory.mkdir(parents=True,exist_ok=True)
        atomic_json(state,{"enabled":enabled,"migrated":migrated})
        return enabled

    def _publish_deck_preview(self,live):
        from sc_voice_cockpit.deck_pack import plan_pack
        from sc_voice_cockpit.keybindings.self_heal import atomic_json
        commands=[self._core.command_dict(c) for c in self.wingman.commands.all()]
        plan=plan_pack(live.raw,live.path.read_text(encoding="utf-8-sig") if live.path.exists() else None,
                       [],commands,{}, {"setup":False,"emotes":bool(self.deck_setting("deck_emotes",True))})
        deck=plan["deck"]
        atomic_json(Path(self.get_generated_files_dir())/"deck_manifest.json",deck)
        if self._deck:
            with self._deck.lock:self._deck.manifest=deck
            self._deck.broadcast({"t":"reload"})

    async def validate(self):
        # Leaf validation traverses both base classes; absent override IDs use defaults.
        errors=await super().validate()
        return errors

    async def prepare(self):
        if self._deck is not None and not self._closed:await self.unload()
        self._closed=False
        self._deck=DeckRuntime(self)
        self._deck.start()
        await SC_NavPoint.prepare(self)
        await SC_Keybindings.prepare(self)
        try:
            self._deck_subscriptions.append(self.wingman.audio.on_mic_status_changed(self._deck_mic_status))
            snapshot=getattr(self.wingman.audio,"mic_status",None)
            if snapshot is not None:await self._deck_mic_status(snapshot)
        except (AttributeError,TypeError):
            self.log.warning("Tablet mic status subscription unavailable",server_only=True)
        # GameLog event names not verified in the available 3.2.11 source:
        # deliberately no guessed subscriptions or broad '*' state resets.

    def _dashboard_url(self):
        return self._ui_server.url+"/nav" if self._ui_server else ""

    async def _start_cockpit_web(self):
        from sc_voice_cockpit.web.server import CockpitServer
        from sc_voice_cockpit.navpoint.navpoint_ui.sharing import dashboard_token
        from sc_voice_cockpit.navpoint.navpoint_ui.window import NavPointWindow
        directory=Path(self.get_generated_files_dir())
        directory.mkdir(parents=True,exist_ok=True)
        manifest=directory/"deck_manifest.json"
        if self.deck_setting("deck_enabled",True) and manifest.exists():
            try:self._deck.manifest=json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError,ValueError):pass
        self.deck_setup_enabled()
        # Keep navigation available even with the deck disabled.
        self._ui_server=CockpitServer(
            self._db,self._deck,directory,port=int(self.deck_setting("deck_port",7869)),
            arrival_alert_m=self._get_arrival_alert_m(),arrival_stop_m=self._get_arrival_stop_m(),
            on_target_change=self._on_hud_target_change,share_on_lan=True,
            access_token=dashboard_token(directory))
        if not await asyncio.to_thread(self._ui_server.start):
            self.log.warning("SC Voice-Cockpit listener unavailable (port busy or startup failed; old NavPoint may still be enabled); navigation and keybinding tools remain available.",server_only=True)
            self._ui_server=None
            return
        self._ui_window=NavPointWindow(self._ui_server.url+"/nav")
        if self._announcement:self._announcement.cancel()
        self._announcement=asyncio.create_task(self._announce_ui_startup(self._ui_server))

    async def _announce_ui_startup(self,server):
        await asyncio.sleep(15)
        if self._ui_server is not server:return
        from sc_voice_cockpit.navpoint.navpoint_ui.sharing import qr_png_base64
        url=server.pairing_url
        qr=qr_png_base64(url)
        await self.wingman.ui.show_dialog("SC Voice-Cockpit",f"[Open tablet deck]({url})\n\nPairing code: **{server.pairing_code}**" + self._setup_card_text(),
                                         image="data:image/png;base64,"+qr if qr else None)

    def _setup_card_text(self):
        if self.deck_setup_enabled() or self._setup_card_shown:return ""
        self._setup_card_shown=True
        from sc_voice_cockpit.deck.setup import SETUP_CARD
        return "\n\n"+SETUP_CARD

    async def _deck_mic_status(self,status):
        # 3.2.11 callback receives one MicStatusResponse, not a wingman name.
        recording=bool(getattr(status,"recording",False))
        owner=getattr(status,"recording_wingman",None)
        listening=bool(getattr(status,"listening",False))
        on=listening or recording and owner in (None,self.wingman.name)
        with self._deck.lock:self._deck.listening=bool(on)
        self._deck.broadcast({"t":"listening","on":bool(on)})

    @command_action(label="Show tablet deck pairing",respond="speak")
    async def show_tablet_deck_pairing(self):
        if not self._ui_server:return "The tablet server is unavailable."
        from sc_voice_cockpit.navpoint.navpoint_ui.sharing import qr_png_base64
        url = self._ui_server.pairing_url
        qr = qr_png_base64(url)
        await self.wingman.ui.show_dialog("SC Voice-Cockpit pairing",
            f"[Open tablet deck]({url})\n\nPairing code: **{self._ui_server.pairing_code}**",
            image="data:image/png;base64,"+qr if qr else None)
        return "The tablet deck link and pairing code are on screen."

    @command_action(respond="ai")
    async def deck_sync(self,command:str=""):
        try:
            if self._deck:
                self._deck.sync(command)
                from sc_voice_cockpit.deck.runtime import TABLET_COMMAND
                if self._deck.manifest.get("commands",{}).get(command,{}).get("emote") and not TABLET_COMMAND.get():
                    await self._deck.emote_feedback(command,self._deck.emote_bound(command))
        except Exception:pass
        return ""

    @tool(name="correct_tablet_light", description="Only when the player says the tablet deck shows the wrong state for a ship system. Correct only its remembered tablet light; never press a key or change the ship.", summarize=True, wait_response=False)
    async def correct_tablet_light(self, group: Literal["door-lock","doors","esp","flight-ready","g-safety","gear","lights","power","shield-power","speed-limiter","thruster-power","vtol","weapons-power"], on: bool):
        """Correct a remembered light without touching the game.

        Args:
            group: door-lock = Door locks; doors = Doors; esp = ESP; flight-ready = Flight ready; g-safety = G-force safety; gear = Landing gear; lights = Headlights; power = Main power; shield-power = Shields; speed-limiter = Speed limiter; thruster-power = Engines; vtol = VTOL; weapons-power = Weapons.
            on: True for the light on, False for off; this does not change the ship.
        """
        if not self._deck or not self._deck.set_state(group,on):
            return "That tablet light is unavailable or the state is invalid. No key was pressed."
        return "Tablet light corrected. No key was pressed and the ship was not changed."

    def emote_is_bound(self, command, target):
        from sc_voice_cockpit.deck_pack.emotes import verified
        with self._work_lock:
            if not self._bindings:return False
            self._bindings.refresh()
            if self._bindings.warning:return False
            obj=self.wingman.commands.get(command)
            return bool(obj and verified(self._core.command_dict(obj),target,self._bindings.engine))

    async def on_add_user_message(self,message):
        if self._deck:
            with self._deck.lock: self._deck.last_utterance=(message,time.monotonic()) if isinstance(message,str) else None
            await self._deck.flush_notes()
        await SC_Keybindings.on_add_user_message(self,message)

    async def deck_nav(self,action):
        if action in ("mark_location","mark"):return await self.mark_location()
        if action in ("show_navpoint_hud","show_hud","hud"):return self.show_navpoint_hud()
        if action in ("clear_target","clear"):
            if self._ui_server:
                self._ui_server.set_active_target(None)
                self._ui_server._notify_target_change(None)
            else:self._on_hud_target_change(None)
            return ""
        if action in ("open_dashboard","dashboard","open_nav"):
            return "/nav"
        raise ValueError("Unknown NAV action")

    def _startup_heal(self,live):
        # The existing background refresh owns this call. Keep pack work after heal
        # under the same work lock so refresh and fresh-start cannot interleave.
        with self._work_lock:
            super()._startup_heal(live)
            if not self._closed and not self._stale and not self._error:
                try:self._integrate_pack(live)
                except Exception as exc:self.log.warning("Deck pack deferred: "+str(exc)[:180],server_only=True)

    def _deck_reserved_keys(self):
        values=[]
        for owner,field in ((self.wingman.config,"record_key"),(self.wingman.config,"record_mouse_button"),
                            (self.settings,"cancel_tts_key"),
                            (getattr(self.settings,"voice_activation",None),"mute_toggle_key")):
            value=getattr(owner,field,None)
            if isinstance(value,str) and value:values.append(value)
        return values

    def _integrate_pack(self,live):
        if not self.deck_setting("deck_enabled",True):return
        if not self.deck_setup_enabled():
            self._publish_deck_preview(live)
            return
        try:
            from sc_voice_cockpit.deck_pack import plan_pack
        except ImportError:return  # Empty plan until the independently-owned pack lands.
        from sc_voice_cockpit.keybindings import self_heal as healer, p4k_reader
        directory=Path(self.get_generated_files_dir())
        def read(name,default):
            p=directory/name
            return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else default
        with self._pack_lock:
            commands=[self._core.command_dict(c) for c in self.wingman.commands.all()]
            custom=read("custom_binds.json",[])
            plan=plan_pack(live.raw,live.path.read_text(encoding="utf-8-sig") if live.path.exists() else None,
                custom,commands,read("command_steps.json",{}),
                {"emotes":bool(self.deck_setting("deck_emotes",True)),"skill_name":"SC_VoiceCockpit","reserved_keys":self._deck_reserved_keys()})
            if self._closed or not self.deck_setup_enabled():return
            additions=plan.get("binds_to_add",[])
            replacements=plan.get("binds_to_replace",[])
            restore={}; rollback=None
            if replacements:
                from sc_voice_cockpit.deck_pack.emotes import replace_profile
                restore,rollback=replace_profile(live,directory,replacements,custom,
                    self._process_check or healer.game_running,lambda:self._closed)
            if additions:
                # The startup restore's global "some entries removed" detection
                # is correct for existing user binds, not a newly added pack.
                # Reuse its guarded restore with only this new, collision-checked set.
                import tempfile
                with tempfile.TemporaryDirectory(prefix="deck-restore-",dir=directory) as temp:
                    staged=Path(temp)
                    healer.atomic_json(staged/"custom_binds.json",additions)
                    healer.backup(directory,"actionmaps.xml",live.path.read_bytes(),lambda:self._closed)
                    restore=healer.restore(live,staged,self._core,p4k_reader,True,
                        self._process_check or healer.game_running,lambda:self._closed,fresh_start=True)
                if not (restore.get("deferred") or restore.get("attention")):
                    self._write_heal_json(directory/"custom_binds.json",custom+additions)
            pending=restore.get("deferred",[]) or restore.get("attention",[])
            self._write_heal_json(directory/"deck_pending.json",{"pending":pending,"warnings":plan.get("warnings",[])})
            if pending:
                # Keep unrepairable emotes visibly unavailable while SC is open.
                # Planned replacements are not live yet: validate the ORIGINAL commands.
                from sc_voice_cockpit.deck_pack.emotes import verified
                from sc_voice_cockpit.deck_pack.planner import walk
                deck=plan.get("deck",{})
                originals={c["name"]:c for c in commands}
                for name,meta in deck.get("commands",{}).items():
                    if meta.get("emote"):
                        meta["no_key"]=not verified(originals.get(name,{}),meta["emote"],live.engine)
                for b in walk(deck):
                    if b.get("emote"):
                        b["no_key"]=deck["commands"][b["command"]]["no_key"]
                self._write_heal_json(directory/"deck_manifest.json",deck)
                if self._deck:
                    with self._deck.lock:self._deck.manifest=deck
                    self._deck.broadcast({"t":"reload"})
                if not self._pack_pending_notified:
                    self.log.warning("Deck custom keys pending; close Star Citizen then reload SC Voice-Cockpit.",server_only=True)
                    self._pack_pending_notified=True
                return
            self._pack_pending_notified=False
            live.refresh()
            needs_save=bool(plan.get("commands_to_add") or plan.get("commands_to_update"))
            if needs_save:
                if not self._loop or not self._loop.is_running():raise ValueError("Wingman loop unavailable")
                future=asyncio.run_coroutine_threadsafe(self._save_pack(plan,commands),self._loop)
                # Await real settlement; no false failure while startup retry can land.
                try:future.result()
                except BaseException:
                    if rollback:
                        before,after,custom_before,custom_after=rollback
                        check=self._process_check or healer.game_running
                        healer.atomic_bytes(live.path,before,after,check,lambda:self._closed)
                        healer.atomic_bytes(directory/"custom_binds.json",custom_before,custom_after,closed=lambda:self._closed)
                        live.refresh()
                    raise
            manifest=read("command_manifest.json",{"commands":[]})
            entries=plan.get("manifest_entries",[])
            if isinstance(entries,dict):entries=entries.get("commands",[])
            from sc_voice_cockpit.deck.manifest import merge_manifest
            manifest=merge_manifest(manifest,entries,commands,plan.get("commands_to_update",[]),self._core)
            self._write_heal_json(directory/"command_manifest.json",manifest)
            self._check_drift(live)
            deck=plan.get("deck",{})
            old=read("deck_manifest.json",{})
            self._write_heal_json(directory/"deck_manifest.json",deck)
            if self._deck:
                with self._deck.lock:self._deck.manifest=deck
                if old!=deck:self._deck.broadcast({"t":"reload"})

    async def _save_pack(self,plan,expected):
        from api.interface import CommandConfig, CommandActionConfig
        if not self.deck_setup_enabled():raise ValueError("Deck setup was switched off")
        previous=[self._core.command_dict(c) for c in self.wingman.commands.all()]
        if previous!=expected:raise ValueError("Commands changed while planning; pack deferred")
        # Validate all new shapes before mutating any command.
        additions=[CommandConfig.model_validate(row) for row in plan.get("commands_to_add",[])]
        updates=[(self.wingman.commands.get(row["name"]),
                  [CommandActionConfig.model_validate(a) for a in row["actions"]])
                 for row in plan.get("commands_to_update",[])]
        from sc_voice_cockpit.keybindings.self_heal import backup
        backup(Path(self.get_generated_files_dir()),"commands.json",
               json.dumps(previous,ensure_ascii=False,indent=2).encode("utf-8"),lambda:self._closed)
        originals=[]
        try:
            for obj,actions in updates:
                if obj is None:raise ValueError("Command disappeared during planning")
                originals.append((obj,obj.actions));obj.actions=actions
            for obj in additions:self.wingman.commands.add(obj)
            deadline=time.monotonic()+self._save_timeout_seconds
            while not self._closed:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise ValueError("Pack save startup retry exhausted")
                if await asyncio.wait_for(self.wingman.commands.save(),timeout=remaining):return
                await asyncio.sleep(min(self._save_retry_seconds,max(.001,deadline-time.monotonic())))
            raise ValueError("Skill unloaded during pack save")
        except BaseException:
            for obj,actions in originals:obj.actions=actions
            for obj in additions:self.wingman.commands.remove(obj.name)
            raise

    async def update_config(self,new_config):
        self.deck_setup_enabled()
        incoming=next((p.value for p in (new_config.custom_properties or []) if p.id=="deck_setup"),None)
        if type(incoming) is bool and incoming != self.deck_setting("deck_setup",False):
            self._setup_override=incoming
            from sc_voice_cockpit.keybindings.self_heal import atomic_json
            atomic_json(Path(self.get_generated_files_dir())/"deck_setup.json",{"enabled":incoming})
        previous_port=self.deck_setting("deck_port",7869)
        previous_ring=(self._get_display(),self._get_overlay_size())
        def non_ring(config):
            values=config.model_dump(mode="json")
            values["custom_properties"]=[p for p in values.get("custom_properties") or []
                                          if p["id"] not in ("display","overlay_size")]
            return values
        ring_only=non_ring(self.config)==non_ring(new_config)
        # Ring-only settings use the documented base hook: no replan, server,
        # capture, calibration, fresh-start state or tablet reload side effects.
        if ring_only:await Skill.update_config(self,new_config)
        else:await SC_Keybindings.update_config(self,new_config)
        if not self._deck or self._closed:return
        if previous_ring!=(self._get_display(),self._get_overlay_size()):
            try:
                await asyncio.to_thread(self._restart_ring_overlay)
                self._sync_ring()
            except Exception as exc:
                self.log.warning("Ring settings could not be applied: "+type(exc).__name__,server_only=True)
        if ring_only:return
        enabled=bool(self.deck_setting("deck_enabled",True))
        mic=enabled and bool(self.deck_setting("deck_tablet_mic",True))
        self._deck.broadcast({"t":"voice-status","state":"ready" if mic else "off"})
        if not enabled:
            for client in tuple(self._deck.clients):await asyncio.to_thread(self._deck.disconnect,client)
        elif not mic and self._deck.mic_owner:
            owner=self._deck.mic_owner
            await asyncio.to_thread(self._deck.receive,owner,{"t":"voice","on":False})
        if self._ui_server and previous_port!=self.deck_setting("deck_port",7869):
            await asyncio.to_thread(self._ui_server.stop)
            self._ui_server=None
            await self._start_cockpit_web()
        self._deck.broadcast({"t":"reload"})
        if enabled and self._bindings and not self._refreshing:
            self._start_refresh(self._property())

    async def unload(self):
        self._closed=True
        if self._announcement:
            task=self._announcement;self._announcement=None
            if not task.done() and task.get_loop().is_running():task.get_loop().call_soon_threadsafe(task.cancel)
        # Close input admission and release holds before stopping listeners.
        if self._deck:
            stopped=await asyncio.to_thread(self._deck.stop)
            if not stopped:self.log.warning("A previously started Wingman macro is still finishing; no new tablet work admitted.",server_only=True)
        for subscription in self._deck_subscriptions:subscription.unsubscribe()
        self._deck_subscriptions=[]
        await SC_Keybindings.unload(self)  # super chain includes NavPoint exactly once
