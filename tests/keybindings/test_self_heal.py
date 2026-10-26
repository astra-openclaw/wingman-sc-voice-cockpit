"""Synthetic startup repair, conservative profile edits, and event-loop handoff."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from sc_voice_cockpit.keybindings import keybinds_core as core, self_heal as heal, p4k_reader as reader
from test_behavior import adapter


def raw(key="ralt+3", build="one", shared=False, **binding):
    maps = ["spaceship_general", "vehicle_general"] if shared else ["spaceship_general"]
    return {"metadata":{"buildId":build}, "actions":[dict(map=m,mapLabel=m,name="doors",
        label="Doors",description="",activationMode="",keyboard=[dict(input=key,**binding)],
        mouse=[],gamepad=[],joystick=[]) for m in maps]}


def command(key="ralt+3", name="Lock Doors", **extra):
    return dict(name=name,description="unchanged",instant_activation=["doors"],responses=["done"],
                category_id="test",actions=heal.render(key,.1,core),**extra)


class MappingTests(unittest.TestCase):
    def test_every_rendered_family_roundtrips(self):
        keys = [*heal.KEYS, *"az09", "f1","f24","space","tab","backspace","delete","insert",
                "home","end","up","down","left","right",*[f"np_{n}" for n in range(10)],
                *heal.MOUSE]
        for key in keys:
            for modifier in ("","lalt+","ralt+","rctrl+","rshift+","lctrl+lshift+"):
                chord = modifier+key
                if key in core.MODIFIERS and modifier:
                    continue
                with self.subTest(chord=chord):
                    c = dict(name="Test",actions=heal.render(chord,.8,core))
                    self.assertEqual(core.events(c),[(core.normalize_key(chord),.8,False)])
                    index = core.KeybindingIndex(raw(chord))
                    self.assertEqual(core.resolved(c,index),["spaceship_general.doors"])

    def test_partial_macro_preserves_waits_and_other_actions(self):
        initial = raw()
        extra = copy.deepcopy(initial["actions"][0]);extra.update(name="engines",keyboard=[dict(input="e")])
        initial["actions"].append(extra)
        c = command()
        c["actions"] = [dict(wait=.7)]+c["actions"]+[dict(wait=2.4),dict(keyboard=dict(hotkey="e",hold=1.2)),dict(wait=.6)]
        entry = heal.steps(c,core.KeybindingIndex(initial),core)
        moved = copy.deepcopy(initial);moved["actions"][0]["keyboard"]=[dict(input="ralt+f3")]
        new, changes = heal.repair(c,entry,core.KeybindingIndex(moved),core)
        self.assertEqual(new["actions"][0],c["actions"][0])
        self.assertEqual(new["actions"][-3:],c["actions"][-3:])
        self.assertEqual([a["wait"] for a in new["actions"] if "wait" in a],[.7,.35,.1,2.4,.6])
        self.assertEqual(len(changes),1)
        self.assertEqual({k:v for k,v in c.items() if k!="actions"},{k:v for k,v in new.items() if k!="actions"})

    def test_three_step_macro_modifier_family_change_preserves_timing(self):
        before=raw("ralt+3")
        for name,key in (("close","c"),("engines","e")):
            row=copy.deepcopy(before["actions"][0]);row.update(name=name,keyboard=[dict(input=key)])
            before["actions"].append(row)
        c=command();c["actions"]=[dict(keyboard=dict(hotkey="c",hold=.2)),dict(wait=.7)]+c["actions"]+[dict(wait=2.4),dict(keyboard=dict(hotkey="e",hold=1.2))]
        entry=heal.steps(c,core.KeybindingIndex(before),core)
        after=copy.deepcopy(before);after["actions"][0]["keyboard"]=[dict(input="lalt+x")]
        new,_=heal.repair(c,entry,core.KeybindingIndex(after),core)
        self.assertEqual([a[0] for a in core.events(new)],["c","lalt+x","e"])
        self.assertEqual([a[1] for a in core.events(new)],[.2,.1,1.2])
        self.assertEqual([a["wait"] for a in new["actions"] if "wait" in a],[.7,.35,.1,2.4])

    def test_shared_intent_requires_one_input_for_all_actions(self):
        c = command();index = core.KeybindingIndex(raw(shared=True));entry = heal.steps(c,index,core,True)
        self.assertEqual(len(entry["steps"][0]["actions"]),2)
        moved = raw("ralt+f3",shared=True)
        new,_ = heal.repair(c,entry,core.KeybindingIndex(moved),core,True)
        self.assertEqual(len(core.resolved(new,core.KeybindingIndex(moved),True)),2)
        moved["actions"][1]["keyboard"][0]["input"]="ralt+f4"
        with self.assertRaisesRegex(ValueError,"every intended"):
            heal.repair(c,entry,core.KeybindingIndex(moved),core,True)

    def test_camera_wrapper_and_hold_threshold(self):
        c = command("np_1",name="Save View 1")
        c["actions"] = [dict(keyboard=dict(hotkey="f4",press=True,release=False)),dict(wait=.3)]+c["actions"]+[dict(wait=.6),dict(keyboard=dict(hotkey="f4",press=False,release=True))]
        before = raw("np_1");before["actions"][0]["map"]="view_director_mode"
        entry = heal.steps(c,core.KeybindingIndex(before),core,True)
        after = copy.deepcopy(before);after["actions"][0]["keyboard"]=[dict(input="np_2",activationMode="delayed_press",pressTriggerThreshold=".8",holdTriggerDelay="1")]
        new,_ = heal.repair(c,entry,core.KeybindingIndex(after),core,True)
        self.assertEqual(new["actions"][:2],c["actions"][:2]);self.assertEqual(new["actions"][-2:],c["actions"][-2:])
        self.assertEqual(core.events(new),[("np_2",1.35,True)])

    def test_ambiguity_multitap_unknown_and_verification_refuse(self):
        c = command();entry = heal.steps(c,core.KeybindingIndex(raw()),core)
        for binds in ([dict(input="x"),dict(input="z")],[dict(input="x",multiTap="2")],
                      [dict(input="x",activationMode="double_tap")],[dict(input="js1_button1")],
                      [dict(input="maxis_x")]):
            moved = raw();moved["actions"][0]["keyboard"]=binds
            with self.subTest(binds=binds),self.assertRaises(ValueError):
                heal.repair(c,entry,core.KeybindingIndex(moved),core)
        moved = raw("ralt+f3")
        collision = copy.deepcopy(moved["actions"][0]);collision["name"]="unwanted";moved["actions"].append(collision)
        with self.assertRaisesRegex(ValueError,"verification"):
            heal.repair(c,entry,core.KeybindingIndex(moved),core)
        with self.assertRaisesRegex(ValueError,"system"):
            heal.repair(dict(c,is_system_command=True),entry,core.KeybindingIndex(raw("x")),core)

    def test_strict_parser_and_nullable_shapes(self):
        for actions in (None,[None],[dict(keyboard=None)],[dict(write="text")],
                        [dict(keyboard=dict(hotkey="x",hotkey_codes=[1]))],
                        [dict(mouse=dict(scroll=1))],[dict(keyboard=dict(hotkey="f4",press=True,release=False))]):
            c = dict(name="nullable",actions=actions)
            entry = heal.steps(c,core.KeybindingIndex(raw()),core)
            with self.subTest(actions=actions),self.assertRaises(ValueError):
                heal.repair(c,entry,core.KeybindingIndex(raw("x")),core)
        c = command();entry = heal.steps(c,core.KeybindingIndex(raw()),core)
        c["actions"].insert(0,dict(wait=1))
        with self.assertRaisesRegex(ValueError,"unchanged"):
            heal.repair(c,entry,core.KeybindingIndex(raw("x")),core)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sc-kb-public-heal-");self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name);self.path = self.root/"actionmaps.xml"
        self.path.write_bytes(b'\xef\xbb\xbf<?xml version="1.0"?>\r\n<ActionProfiles>\r\n  <options untouched="yes"/>\r\n</ActionProfiles>\r\n')
        self.live = core.LiveBindings(raw("x"),self.path);self.live.refresh()
        self.custom = self.root/"custom_binds.json"
        reader.atomic_json(self.custom,[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
        self.before = self.path.read_bytes()

    def restore(self, enabled=True, process=lambda:False):
        return heal.restore(self.live,self.root,core,reader,enabled,process)

    def test_reset_restore_bom_crlf_backups_and_idempotence(self):
        result = self.restore();self.assertEqual(result["restored"],["spaceship_general.doors"])
        data = self.path.read_bytes();self.assertTrue(data.startswith(b"\xef\xbb\xbf"));self.assertNotIn(b"\n",data.replace(b"\r\n",b""))
        self.assertIn(b'<options untouched="yes"/>',data)
        self.assertIn("ralt+3",self.live.engine.command_inputs)
        self.assertEqual(next((self.root/"backups").glob("*actionmaps.xml")).read_bytes(),self.before)
        self.assertEqual(self.restore()["restored"],[]);self.assertEqual(data,self.path.read_bytes())

    def test_merge_existing_action_map_and_self_closing_blocks(self):
        texts = ['<ActionProfiles><actionmap name="spaceship_general"/></ActionProfiles>',
                 '<ActionProfiles><actionmap name="spaceship_general"><action name="doors"/></actionmap></ActionProfiles>',
                 '<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="mo1_mouse1"/></action></actionmap></ActionProfiles>',
                 '<ActionProfiles/>']
        for text in texts:
            with self.subTest(text=text):
                (self.root/"restore-state.json").unlink(missing_ok=True)
                self.path.write_text(text,encoding="utf-8")
                result=self.restore();self.assertEqual(len(result["restored"]),1)
                if "mo1_" in text:self.assertIn("mo1_mouse1",self.path.read_text(encoding="utf-8"))

    def test_tab_and_four_space_indentation_preserved(self):
        for unit in ("\t","    "):
            with self.subTest(unit=unit):
                (self.root/"restore-state.json").unlink(missing_ok=True)
                original='<ActionProfiles>\r\n'+unit+'<options keep="yes"/>\r\n</ActionProfiles>\r\n'
                self.path.write_bytes(original.encode("utf-8"))
                self.restore()
                text=self.path.read_bytes().decode("utf-8")
                self.assertIn('\r\n'+unit+'<actionmap',text)
                self.assertIn('\r\n'+unit*2+'<action name=',text)
                self.assertIn('\r\n'+unit*3+'<rebind',text)
                self.assertIn(unit+'<options keep="yes"/>',text)

    def test_explicit_clears_and_user_moves_are_never_overwritten(self):
        for value in ("kb1_ ","kb1_z"):
            with self.subTest(value=value):
                reader.atomic_json(self.custom,[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
                self.path.write_text('<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="'+value+'"/></action></actionmap></ActionProfiles>',encoding="utf-8")
                before=self.path.read_bytes();result=self.restore()
                row=json.loads(self.custom.read_text(encoding="utf-8"))[0]
                self.assertEqual(row["input"],value);self.assertEqual(row.get("cleared",False),value=="kb1_ ")
                self.assertTrue(result["custom_updates"]);self.assertEqual(self.path.read_bytes(),before)
                self.assertEqual(self.restore()["custom_updates"],[])
        # A remembered clear also survives a later profile wipe.
        reader.atomic_json(self.custom,[dict(map="spaceship_general",action="doors",input="kb1_ ",cleared=True)])
        self.path.write_bytes(self.before);self.assertEqual(self.restore()["restored"],[])

    def test_disabled_running_and_unknown_process_defer(self):
        self.assertEqual(self.restore(False)["restored"],[])
        self.assertTrue(self.restore(process=lambda:True)["deferred"]);self.assertEqual(self.path.read_bytes(),self.before)
        self.assertTrue(heal.game_running())  # Linux is never evidence the game is closed.
        with patch.object(heal.sys,"platform","win32"),patch.object(heal.ctypes,"WinDLL",side_effect=OSError("enumeration failed"),create=True):
            self.assertTrue(heal.game_running())
        calls=iter([False,True])
        with self.assertRaisesRegex(ValueError,"deferred"):self.restore(process=lambda:next(calls))
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_conflicting_slot_unknown_structure_and_concurrent_edit(self):
        self.path.write_text('<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_x"/></action></actionmap></ActionProfiles>',encoding="utf-8")
        before=self.path.read_bytes();self.assertTrue(self.restore()["attention"]);self.assertEqual(before,self.path.read_bytes())
        for text in ('<Other/>','<ActionProfiles><actionmap name="x"/><actionmap name="x"/></ActionProfiles>',
                     '<ActionProfiles><actionmap name="x"><action name="a"><unknown/></action></actionmap></ActionProfiles>'):
            self.path.write_text(text,encoding="utf-8")
            with self.assertRaises(ValueError):self.restore()
        self.path.write_bytes(self.before)
        calls=[0]
        def concurrent():
            calls[0]+=1
            if calls[0]==1:self.path.write_bytes(self.before+b" ")
            return False
        with self.assertRaisesRegex(ValueError,"changed"):self.restore(process=concurrent)

    def test_backup_retention(self):
        for i in range(25):heal.backup(self.root,"commands.json",str(i).encode())
        files=sorted((self.root/"backups").iterdir());self.assertEqual(len(files),20)
        self.assertEqual(files[-1].read_bytes(),b"24")


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="sc-kb-public-startup-");self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.m=adapter();self.s=self.m.SC_Keybindings()
        self.s.generated=self.root;self.s._core=core;self.s._loop=asyncio.get_running_loop()
        self.live_commands=[command(),dict(name="System",actions=None,is_system_command=True)]
        self.logs=[];self.saved=[]
        async def save():
            self.saved.append((threading.get_ident(),asyncio.get_running_loop()))
            return True
        self.s.wingman.commands=types.SimpleNamespace(all=lambda:tuple(self.live_commands),save=save)
        self.s.log.warning=lambda text,**kw:None if kw.get("server_only") else self.logs.append(text)
        self.s.config.custom_properties=[]
        self.s._process_check=lambda:False

    async def startup(self, data, xml=None):
        path=self.root/"actionmaps.xml"
        if xml is not None:path.write_text(xml,encoding="utf-8")
        elif not path.exists():path.write_text("<ActionProfiles/>",encoding="utf-8")
        live=core.LiveBindings(data,path);live.refresh()
        await asyncio.to_thread(self.s._startup_heal,live)
        return json.loads((self.root/"last-changes.json").read_text(encoding="utf-8"))

    def flags(self, **flags):
        self.s.config.custom_properties=[types.SimpleNamespace(id=k,value=v) for k,v in flags.items()]

    async def test_moved_default_handoff_notification_once_and_idempotence(self):
        await self.startup(raw())
        self.assertEqual(self.logs,[])
        self.flags(auto_update_commands=True)
        result=await self.startup(raw("ralt+f3","two"))
        self.assertEqual(result["cause"],"game update");self.assertEqual(len(result["fixed"]),1)
        self.assertEqual(self.saved,[(threading.get_ident(),asyncio.get_running_loop())])
        self.assertIn("updated 1",await self.s.get_prompt())
        await self.s.on_add_assistant_message("planning",[{}]);self.assertIsNotNone(self.s._change_note)
        await self.s.on_add_assistant_message("answer",[]);self.assertIsNone(self.s._change_note)
        self.assertEqual(len(list((self.root/"backups").glob("*commands.json"))),1)
        await self.startup(raw("ralt+f3","two"));self.assertEqual(len(self.saved),1);self.assertEqual(len(self.logs),1)
        self.assertTrue(all(r["status"]=="OK" for r in self.s._drift["commands"]))

    async def test_user_moved_custom_updates_commands_and_clear_notifies(self):
        self.flags(auto_update_commands=True,restore_custom_binds=True)
        reader.atomic_json(self.root/"custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
        await self.startup(raw("ralt+3"),'<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_ralt+3"/></action></actionmap></ActionProfiles>')
        result=await self.startup(raw("ralt+3"),'<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_ralt+f3"/></action></actionmap></ActionProfiles>')
        self.assertEqual(result["cause"],"your controls");self.assertEqual(len(result["fixed"]),1)
        self.assertEqual(json.loads((self.root/"custom_binds.json").read_text(encoding="utf-8"))[0]["input"],"kb1_ralt+f3")
        result=await self.startup(raw("ralt+3"),'<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_ "/></action></actionmap></ActionProfiles>')
        self.assertTrue(result["custom_updates"][0]["cleared"]);self.assertFalse(result["fixed"])
        self.assertTrue(result["attention"])

    async def test_reset_restore_and_running_defer(self):
        self.flags(auto_update_commands=True,restore_custom_binds=True)
        reader.atomic_json(self.root/"custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
        await self.startup(raw("x"),'<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_ralt+3"/></action></actionmap></ActionProfiles>')
        self.s._process_check=lambda:True
        result=await self.startup(raw("x"),"<ActionProfiles/>")
        self.assertTrue(result["deferred"]);self.assertFalse(result["fixed"]);self.assertFalse(self.saved)
        self.s._process_check=lambda:False
        result=await self.startup(raw("x"))
        self.assertTrue(result["restored"]);self.assertFalse(result["fixed"])

    async def test_disabled_updates_stale_failure_and_expiry(self):
        await self.startup(raw())
        self.flags(auto_update_commands=False)
        result=await self.startup(raw("x"));self.assertFalse(result["fixed"]);self.assertTrue(result["attention"])
        before=self.live_commands[0].copy()
        self.s._stale=True
        await asyncio.to_thread(self.s._startup_heal,core.LiveBindings(raw("z"),self.root/"actionmaps.xml"))
        self.assertEqual(before,self.live_commands[0]);self.assertFalse(self.saved)
        self.s._change_note=dict(prompt="old",spoken="old",at=0);self.assertNotIn("old",await self.s.get_prompt())
        self.s._stale=False;self.s._error="failed"
        await asyncio.to_thread(self.s._startup_heal,core.LiveBindings(raw("z"),self.root/"actionmaps.xml"))
        self.assertEqual(before,self.live_commands[0])

    async def test_curated_manifest_updated_and_metadata_unchanged(self):
        c=self.live_commands[0];index=core.KeybindingIndex(raw())
        reader.atomic_json(self.root/"command_manifest.json",dict(commands=[dict(name=c["name"],fingerprint=core.command_fingerprint(c),expectedActions=core.resolved(c,index,True),baselineActions=core.resolved(c,index,True),verifiable=True)]))
        await self.startup(raw());before={k:v for k,v in c.items() if k!="actions"}
        result=await self.startup(raw("ralt+f3"))
        self.assertEqual(len(result["fixed"]),1)
        manifest=json.loads((self.root/"command_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["commands"][0]["fingerprint"],core.command_fingerprint(c))
        self.assertEqual(before,{k:v for k,v in c.items() if k!="actions"})

    async def test_prepare_captures_loop_and_thirty_rewrites_allowed(self):
        with patch.object(self.s,'_start_refresh'):
            await self.s.prepare()
        self.assertIs(self.s._loop,asyncio.get_running_loop())
        self.live_commands[:]=[command(name="Door "+str(i)) for i in range(30)]
        await self.startup(raw())
        result=await self.startup(raw("ralt+f3"))
        self.assertEqual(len(result["fixed"]),30);self.assertEqual(len(self.saved),1)

    async def test_verification_collision_and_profile_failure_never_mutate(self):
        self.flags(auto_update_commands=True,restore_custom_binds=False)
        await self.startup(raw());before=copy.deepcopy(self.live_commands)
        after=raw("ralt+f3");extra=copy.deepcopy(after["actions"][0]);extra["name"]="unwanted";after["actions"].append(extra)
        result=await self.startup(after)
        self.assertFalse(result["fixed"]);self.assertEqual(before,self.live_commands)
        self.assertIn("verification",result["attention"][0]["reason"])
        snapshot=(self.root/"bindings_snapshot.json").read_bytes()
        self.flags(auto_update_commands=True,restore_custom_binds=True)
        reader.atomic_json(self.root/"custom_binds.json",[{"invalid":True}])
        result=await self.startup(raw("z"))
        self.assertEqual(before,self.live_commands);self.assertNotEqual(snapshot,(self.root/"bindings_snapshot.json").read_bytes())
        self.assertIn("invalid custom",result["attention"][0]["reason"])

    async def test_ceiling_and_save_failure_leave_actions_untouched(self):
        self.live_commands[:]=[command(name="Door "+str(i)) for i in range(31)]
        await self.startup(raw());before=copy.deepcopy(self.live_commands)
        result=await self.startup(raw("ralt+f3"))
        self.assertFalse(result["fixed"]);self.assertIn("30",result["attention"][0]["reason"])
        self.assertEqual(before,self.live_commands);self.assertFalse(self.saved)
        self.live_commands[:]=[command()]
        await self.startup(raw())
        self.s._save_timeout_seconds=0
        async def failure():return False
        self.s.wingman.commands.save=failure
        result=await self.startup(raw("ralt+f3"))
        self.assertFalse(result["fixed"]);self.assertEqual(self.live_commands,[command()])
        self.assertTrue(any("save failed" in r["reason"] for r in result["attention"]))

    async def test_ambiguous_unparseable_and_collision_startup_silent_rerun(self):
        await self.startup(raw());before=copy.deepcopy(self.live_commands)
        data=raw("x");data["actions"][0]["keyboard"].append(dict(input="z"))
        first=await self.startup(data);self.assertTrue(first["attention"]);self.assertFalse(first["fixed"])
        count=len(self.logs);await self.startup(data)
        self.assertEqual(len(self.logs),count);self.assertEqual(before,self.live_commands)
