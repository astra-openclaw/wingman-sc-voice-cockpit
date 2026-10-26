"""Synthetic fresh-install reset, explicit restoration and public UI triggers."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from test_behavior import adapter
from test_self_heal import raw, command
from sc_voice_cockpit.keybindings import keybinds_core as core, self_heal as heal, p4k_reader as reader


class FreshStartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="sc-kb-public-fresh-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.m=adapter();self.s=self.m.SC_Keybindings()
        self.s.generated=self.root;self.s._core=core;self.s._setting="auto"
        self.s._loop=asyncio.get_running_loop();self.s._process_check=lambda:False
        self.s.config.custom_properties=[]
        self.path=self.root/"actionmaps.xml"
        self.path.write_text("<ActionMaps><ActionProfiles/></ActionMaps>",encoding="utf-8")
        self.data=raw();self.commands=[command()]
        self.saved=[];self.logs=[];self.dialogs=[]
        async def save():self.saved.append(threading.get_ident());return True
        async def dialog(*args):self.dialogs.append(args)
        self.s.wingman.commands=types.SimpleNamespace(all=lambda:self.commands,save=save)
        self.s.wingman.ui=types.SimpleNamespace(show_dialog=dialog)
        self.s.log.warning=lambda text,**kw:self.logs.append(text) if not kw.get("server_only") else None
        self.s._save_retry_seconds=.001
        self.s._bindings=core.LiveBindings(self.data,self.path);self.s._bindings.refresh()
        self.calls=[]
        def refresh(setting):
            self.calls.append(self.s._fresh_phase)
            if self.s._fresh_phase=="import":
                self.assertFalse((self.root/"defaults.json").exists())
                self.assertFalse((self.root/"defaults.json.entries.json").exists())
                self.assertFalse((self.root/"command_steps.json").exists())
                self.assertFalse((self.root/"command_baseline.json").exists())
                stage=Path(self.s._fresh_import_dir)
                self.assertFalse((stage/"defaults.json").exists())
                reader.atomic_json(stage/"defaults.json",self.data)
                reader.atomic_json(stage/"defaults.json.entries.json",dict(new=True))
            self.s._bindings=core.LiveBindings(self.data,self.path);self.s._bindings.refresh()
        self.refresh=refresh

    def flags(self,**values):
        return types.SimpleNamespace(prompt="base",custom_properties=[
            types.SimpleNamespace(id=k,value=v) for k,v in values.items()])

    def write(self,name,value):reader.atomic_json(self.root/name,value)
    def read(self,name):return json.loads((self.root/name).read_text(encoding="utf-8"))

    async def fresh(self,trigger="command"):
        with patch.object(self.s,"_refresh",side_effect=self.refresh):
            return await asyncio.to_thread(self.s._fresh_start_sync,trigger)

    async def test_reset_bundle_rebaseline_keep_markers_and_one_report(self):
        await asyncio.to_thread(self.s._startup_heal,self.s._bindings)
        self.logs.clear()
        originals={}
        for name in ("bindings_snapshot.json","command_steps.json","command_baseline.json",
                     "drift-report.json","last-changes.json","restore-state.json"):
            if not (self.root/name).exists():self.write(name,dict(old=True))
            originals[name]=(self.root/name).read_text(encoding="utf-8")
        self.write("defaults.json",dict(old=True));self.write("defaults.json.entries.json",dict(old=True))
        self.write("custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3",removed=True,missing_in_game=True,cleared=True)])
        before=copy.deepcopy(self.commands);profile=self.path.read_bytes()
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(self.calls,["repair","import"])
        self.assertEqual(self.commands,before);self.assertEqual(self.path.read_bytes(),profile)
        custom=self.read("custom_binds.json")[0]
        self.assertTrue(custom["cleared"]);self.assertNotIn("removed",custom);self.assertNotIn("missing_in_game",custom)
        bundle=self.read("backups/"+report["backup"])["files"]
        self.assertEqual(bundle["restore-state.json"],originals["restore-state.json"])
        self.assertEqual(set(bundle),set(originals)|{"custom_binds.json"})
        self.assertFalse((self.root/"restore-state.json").exists())
        self.assertFalse((self.root/"last-changes.json").exists())
        self.assertTrue(self.read("command_steps.json"));self.assertEqual(self.read("drift-report.json")["mode"],"baseline")
        self.assertEqual(self.read("defaults.json.entries.json"),dict(new=True))
        self.assertEqual(len(self.logs),1);self.assertIsNone(self.s._change_note)
        self.assertLessEqual(len(report["spoken"].split()),40)

    async def test_final_heal_before_reset_and_no_command_edits_after(self):
        await asyncio.to_thread(self.s._startup_heal,self.s._bindings)
        self.data=raw("ralt+f3");self.logs.clear()
        def refresh(setting):
            if self.s._fresh_phase=="import":
                self.assertEqual(core.events(self.commands[0])[0][0],"ralt+f3")
                self.assertEqual(len(self.saved),1)
            self.refresh(setting)
        with patch.object(self.s,"_refresh",side_effect=refresh):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(len(self.saved),1);self.assertEqual(len(self.logs),1);self.assertIsNone(self.s._change_note)
        bundle=self.read("backups/"+report["backup"])["files"]
        self.assertIn("ralt+f3",bundle["command_steps.json"])
        self.assertTrue(all(r["status"]=="OK" for r in self.read("drift-report.json")["commands"]))

    async def test_curated_manifest_kept(self):
        c=self.commands[0];reached=core.resolved(c,self.s._bindings.engine,True)
        self.write("command_manifest.json",dict(commands=[dict(name=c["name"],fingerprint=core.command_fingerprint(c),expectedActions=reached,baselineActions=reached,verifiable=True)]))
        before=(self.root/"command_manifest.json").read_bytes()
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        self.assertEqual((self.root/"command_manifest.json").read_bytes(),before)
        self.assertFalse((self.root/"command_baseline.json").exists())
        self.assertEqual(self.read("drift-report.json")["mode"],"curated")

    async def test_explicit_partial_restore_not_once_per_build_no_state(self):
        self.data=raw()
        base=self.data["actions"][0];base["keyboard"]=[]
        for name in ("present","moved","cleared","default","remembered"):
            row=copy.deepcopy(base);row["name"]=name
            self.data["actions"].append(row)
        self.path.write_text('<ActionMaps><ActionProfiles><actionmap name="spaceship_general">'+
            '<action name="present"><rebind input="kb1_x"/></action>'+
            '<action name="moved"><rebind input="kb1_y"/></action>'+
            '<action name="cleared"><rebind input="kb1_ "/></action>'+
            '<action name="default"><rebind input="kb1_z"/></action>'+
            '</actionmap></ActionProfiles></ActionMaps>',encoding="utf-8")
        custom=[dict(map="spaceship_general",action=name,input=key) for name,key in
                (("doors","kb1_ralt+3"),("present","kb1_x"),("moved","kb1_x"),
                 ("cleared","kb1_x"),("default","kb1_x"),("remembered","kb1_x"))]
        custom[-1]["cleared"]=True
        self.write("custom_binds.json",custom)
        self.write("restore-state.json",dict(buildId="one"))
        self.s.config=self.flags(restore_custom_binds=True)
        before=copy.deepcopy(self.commands)
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(report["restored"],["spaceship_general.doors"])
        self.assertNotIn("remembered",self.path.read_text(encoding="utf-8"))
        self.assertIn('input="kb1_y"',self.path.read_text(encoding="utf-8"))
        self.assertIn('input="kb1_ "',self.path.read_text(encoding="utf-8"))
        self.assertNotIn('action name="moved"><rebind input="kb1_x"',self.path.read_text(encoding="utf-8"))
        custom[3].update(input="kb1_ ",cleared=True)
        self.assertEqual(self.read("custom_binds.json"),custom)
        self.assertGreaterEqual(len(report["attention"]),4)
        self.assertFalse((self.root/"restore-state.json").exists())
        self.assertEqual(self.commands,before)
        # Automatic same-build recovery is still eligible after explicit restore.
        self.path.write_text("<ActionProfiles/>",encoding="utf-8");self.s._bindings.refresh()
        result=heal.restore(self.s._bindings,self.root,core,reader,True,lambda:False)
        self.assertTrue(result["restored"]);self.assertTrue((self.root/"restore-state.json").exists())

    async def test_running_defers_no_profile_writes_or_removed_markers(self):
        self.s.config=self.flags(restore_custom_binds=True);self.s._process_check=lambda:True
        self.write("custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3",removed=True)])
        before=self.path.read_bytes()
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertNotIn("removed",self.read("custom_binds.json")[0])
        self.assertEqual(len(report["deferred"]),1)
        self.assertIn("Close Star Citizen and say fresh start keybindings again",report["spoken"])
        self.assertFalse((self.root/"restore-state.json").exists())
        self.assertFalse(list((self.root/"backups").glob("*actionmaps.xml")))

    async def test_visible_log_and_switch_dialog_share_remaining_details(self):
        self.s.config=self.flags(fresh_start=True,restore_custom_binds=True)
        self.data["actions"][0].update(mapLabel="Ship Controls",label="Lock Doors")
        self.path.write_text('<ActionProfiles><actionmap name="spaceship_general"><action name="doors"><rebind input="kb1_x"/></action></actionmap></ActionProfiles>',encoding="utf-8")
        self.write("custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
        with patch.object(self.s,"_refresh",side_effect=self.refresh):
            self.s._launch_fresh_switch()
            await self.s._fresh_task
        report=self.read("fresh-start-report.json")
        self.assertEqual(len(self.logs),1);self.assertEqual(self.logs[0],report["log"])
        self.assertIn("Ship Controls / Lock Doors: you changed this key",report["log"])
        self.assertEqual(len(self.dialogs),1);self.assertEqual(self.dialogs[0][1],report["dialog"])
        self.assertIn(report["details"],self.dialogs[0][1]);self.assertIsNone(self.s._change_note)

    async def test_off_never_writes_profile_and_missing_actions_left_alone(self):
        self.write("custom_binds.json",[dict(map="unknown",action="absent",input="kb1_x")])
        before=self.path.read_bytes()
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(self.path.read_bytes(),before);self.assertFalse(report["restored"])

    async def test_failure_keeps_healthy_index_and_reports(self):
        old=self.s._bindings
        def refresh(setting):
            if self.s._fresh_phase=="import":
                self.s._stale=True;self.s._error="synthetic extraction failure"
            else:self.refresh(setting)
        with patch.object(self.s,"_refresh",side_effect=refresh):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"failed")
        self.assertIs(self.s._bindings.engine.raw if hasattr(self.s._bindings.engine,"raw") else self.s._bindings.raw,old.raw)
        self.assertFalse(self.s._stale);self.assertFalse(self.s._error)
        self.assertIn("synthetic",report["attention"][0]);self.assertEqual(len(self.logs),1)

    async def test_unowned_lease_not_deleted(self):
        self.write("startup-heal.lock",dict(pid=1,at=time.time()))
        os.utime(self.root/"startup-heal.lock",(0,0))
        before=(self.root/"startup-heal.lock").read_bytes()
        report=await self.fresh()
        self.assertEqual(report["status"],"failed")
        self.assertEqual((self.root/"startup-heal.lock").read_bytes(),before)
        self.assertFalse((self.root/"backups").exists())

    async def test_unload_stops_reset_and_profile_writes(self):
        def refresh(setting):
            self.refresh(setting)
            if self.s._fresh_phase=="import":self.s._closed=True
        self.write("custom_binds.json",[dict(map="spaceship_general",action="doors",input="kb1_ralt+3")])
        self.s.config=self.flags(restore_custom_binds=True)
        before=self.path.read_bytes()
        with patch.object(self.s,"_refresh",side_effect=refresh):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"failed");self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse((self.root/"defaults.json").exists())
        self.assertFalse((self.root/"fresh-start-report.json").exists())
        self.assertFalse((self.root/"fresh-start-state.json").exists())

    async def test_switch_edges_prepare_record_and_older_override(self):
        runs=[]
        def sync(trigger):
            runs.append(trigger);self.write("fresh-start-state.json",dict(done=True,at=0))
            return dict(spoken="Done.",dialog="Done.")
        with patch.object(self.s,"_fresh_start_sync",side_effect=sync),patch.object(self.s,"_start_refresh") as refresh:
            self.assertEqual(await self.s.validate(),[])
            self.assertFalse(runs)
            await self.s.update_config(self.flags(fresh_start=True))
            await self.s._fresh_task
            self.assertEqual(runs,["switch"]);self.assertEqual(len(self.dialogs),1)
            await self.s.update_config(self.flags(fresh_start=True));await self.s.prepare()
            self.assertEqual(len(runs),1);refresh.assert_called_once()
            await self.s.update_config(self.flags(fresh_start=False))
            self.assertFalse((self.root/"fresh-start-state.json").exists())
            await self.s.update_config(self.flags(fresh_start=True));await self.s._fresh_task
            self.assertEqual(len(runs),2)
            (self.root/"fresh-start-state.json").unlink()
            await self.s.prepare();await self.s._fresh_task
            self.assertEqual(len(runs),3)
            self.s.config=self.flags()
            await self.s.prepare()
            self.assertFalse((self.root/"fresh-start-state.json").exists())

    async def test_real_switch_done_record_stays_on_no_repeat(self):
        self.s.config=self.flags(fresh_start=True)
        report=await self.fresh("switch")
        self.assertEqual(report["status"],"done",report)
        self.assertTrue(self.read("fresh-start-state.json")["done"])
        self.assertFalse(await self.s._fresh_switch_ready())
        self.s.config=self.flags()
        self.assertFalse(await self.s._fresh_switch_ready())
        self.assertFalse((self.root/"fresh-start-state.json").exists())

    async def test_command_summary_timeout_busy_and_responsive_loop(self):
        begun=threading.Event();finish=threading.Event();thread=[]
        def work(trigger):
            thread.append(threading.get_ident());begun.set();finish.wait(2)
            return dict(spoken="Fresh start done.",dialog="Fresh start done.")
        with patch.object(self.s,"_fresh_start_sync",side_effect=work):
            task=asyncio.create_task(self.s.fresh_start_keybindings())
            await asyncio.to_thread(begun.wait,1)
            self.assertNotEqual(thread[0],threading.get_ident())
            await asyncio.sleep(.001)  # Returns while worker remains blocked.
            self.assertFalse(task.done());finish.set()
            self.assertEqual(await task,"Fresh start done.")
        begun.clear();finish.clear();self.s._fresh_command_seconds=.01
        with patch.object(self.s,"_fresh_start_sync",side_effect=work):
            text=await self.s.fresh_start_keybindings()
            self.assertIn("still running",text);finish.set();await self.s._fresh_task
        self.s._fresh_lock.acquire()
        try:self.assertIn("already running",await self.s.fresh_start_keybindings())
        finally:self.s._fresh_lock.release()

    async def test_concurrent_refresh_and_heal_wait_then_busy(self):
        # The actual shared work lock serializes both entry points.
        for method in ("_refresh","_startup_heal"):
            begun=threading.Event();finish=threading.Event()
            def hold(*args):begun.set();finish.wait(2)
            inner="_refresh_locked" if method=="_refresh" else "_startup_heal_locked"
            with patch.object(self.s,inner,side_effect=hold):
                thread=threading.Thread(target=getattr(self.s,method),args=("auto",) if method=="_refresh" else (self.s._bindings,))
                thread.start();await asyncio.to_thread(begun.wait,1)
                self.s._fresh_wait_seconds=.01
                report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
                self.assertEqual(report["status"],"busy")
                self.assertFalse((self.root/"fresh-start-state.json").exists())
                finish.set();await asyncio.to_thread(thread.join,1)
        self.s._fresh_wait_seconds=1
        with patch.object(self.s,"_refresh",side_effect=self.refresh):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"done",report)

    async def test_fresh_start_waits_for_existing_work_to_finish(self):
        begun=threading.Event();finish=threading.Event()
        def hold():
            with self.s._work_lock:begun.set();finish.wait(2)
        thread=threading.Thread(target=hold);thread.start();await asyncio.to_thread(begun.wait,1)
        with patch.object(self.s,"_refresh",side_effect=self.refresh):
            task=asyncio.create_task(asyncio.to_thread(self.s._fresh_start_sync,"command"))
            await asyncio.sleep(.01);self.assertFalse(task.done())
            finish.set();self.assertEqual((await task)["status"],"done")
        await asyncio.to_thread(thread.join,1)

    async def test_bundle_keeps_exact_utf8_bom_and_crlf(self):
        original=b"\xef\xbb\xbf[\r\n]\r\n"
        (self.root/"custom_binds.json").write_bytes(original)
        report=await self.fresh()
        self.assertEqual(report["status"],"done",report)
        bundle=self.read("backups/"+report["backup"])
        self.assertEqual(bundle["files"]["custom_binds.json"].encode("utf-8"),original)

    async def test_report_write_failure_returns_clear_error_and_releases_lock(self):
        with patch.object(self.s,"_refresh",side_effect=self.refresh):
            original=self.s._write_heal_json
            def write(path,value):
                if path.name=="fresh-start-report.json":raise OSError("synthetic read-only report")
                original(path,value)
            with patch.object(self.s,"_write_heal_json",side_effect=write):
                report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"failed");self.assertIn("could not save",report["spoken"])
        self.assertFalse(self.s._fresh_lock.locked());self.assertFalse(self.s._stale)

    async def test_unload_during_final_normal_extraction_cannot_publish(self):
        game=self.root/"LIVE";game.mkdir();(game/"Data.p4k").write_bytes(b"synthetic")
        self.write("LIVE/build_manifest.id",dict(BuildId="one"))
        self.data["metadata"].update(p4kSize=9,p4kMtime=reader._iso((game/"Data.p4k").stat().st_mtime),extractorVersion=2)
        self.write("defaults.json",raw())
        before=(self.root/"defaults.json").read_bytes()
        def extract(game,out):
            reader.atomic_json(out,self.data);self.s._closed=True
        actual=self.m._sibling_module
        proxy=types.SimpleNamespace(extract=extract,_iso=reader._iso)
        with patch.object(self.m,"locate_game",return_value=game),patch.object(self.m.shutil,"which",return_value=None),patch.object(self.m,"_sibling_module",side_effect=lambda name:proxy if name=="p4k_reader" else actual(name)):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"failed")
        self.assertEqual((self.root/"defaults.json").read_bytes(),before)
        self.assertFalse((self.root/"fresh-start-report.json").exists())
        self.assertFalse((self.root/"backups").exists())

    async def test_actual_refresh_forces_python_extraction_into_empty_stage(self):
        self.s._setting=None  # prepare-on must not schedule a redundant lookup refresh.
        game=self.root/"LIVE";game.mkdir();(game/"Data.p4k").write_bytes(b"synthetic")
        self.write("LIVE/build_manifest.id",dict(BuildId="one"))
        self.data["metadata"].update(p4kSize=9,p4kMtime=reader._iso((game/"Data.p4k").stat().st_mtime),extractorVersion=2)
        self.write("defaults.json",self.data);self.write("defaults.json.entries.json",dict(poison=True))
        target=game/"user/client/0/Profiles/default";target.mkdir(parents=True)
        (target/"actionmaps.xml").write_text("<ActionProfiles/>",encoding="utf-8")
        extracted=[]
        def extract(game,out):
            extracted.append(Path(out));self.assertFalse(Path(out).exists())
            self.assertFalse(Path(str(out)+".entries.json").exists())
            reader.atomic_json(out,self.data)
        actual=self.m._sibling_module
        proxy=types.SimpleNamespace(extract=extract,_iso=reader._iso)
        with patch.object(self.m,"locate_game",return_value=game),patch.object(self.m.shutil,"which",return_value=None),patch.object(self.m,"_sibling_module",side_effect=lambda name:proxy if name=="p4k_reader" else actual(name)):
            report=await asyncio.to_thread(self.s._fresh_start_sync,"command")
        self.assertEqual(report["status"],"done",report)
        self.assertEqual(len(extracted),1);self.assertIn(".fresh-start-",str(extracted[0]))
        self.assertEqual(self.read("defaults.json"),self.data)
        self.assertEqual(self.s._setting,"auto")
        with patch.object(self.s,"_start_refresh") as start:
            self.s._lookup("doors")
            start.assert_not_called()


if __name__=="__main__":unittest.main()
