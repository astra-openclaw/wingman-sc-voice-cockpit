"""Neutral regression cases for profile layout and Wingman startup ordering."""
import asyncio
import copy
import ctypes
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import test_self_heal as fixtures
from sc_voice_cockpit.keybindings import keybinds_core as core, self_heal as heal, p4k_reader as reader

RESET = ('<ActionMaps>\r\n'
         ' <ActionProfiles version="1" optionsVersion="2" rebindVersion="2" profileName="default">\r\n'
         '  <options type="keyboard" instance="1" Product="Keyboard  {00000000-0000-0000-0000-000000000000}"/>\r\n'
         '  <options type="joystick" instance="1"/>\r\n'
         '  <modifiers />\r\n'
         ' </ActionProfiles>\r\n'
         '</ActionMaps>\r\n')


class ReviewRestoreTests(unittest.TestCase):
    setUp = fixtures.RestoreTests.setUp
    restore = fixtures.RestoreTests.restore

    def add_second(self):
        data = fixtures.raw("x")
        extra = copy.deepcopy(data["actions"][0])
        extra.update(name="power", keyboard=[dict(input="p")])
        data["actions"].append(extra)
        self.live = core.LiveBindings(data,self.path)
        rows = [dict(map="spaceship_general",action=a,input=k) for a,k in
                (("doors","kb1_ralt+3"),("power","kb1_ralt+u"))]
        reader.atomic_json(self.custom,rows)
        return rows

    def test_nested_profile_byte_preservation_and_direct_root(self):
        for text in (RESET, RESET.replace("<ActionMaps>\r\n","").replace("</ActionMaps>\r\n","")):
            with self.subTest(text=text):
                (self.root/"restore-state.json").unlink(missing_ok=True)
                before = b"\xef\xbb\xbf"+text.encode("utf-8")
                self.path.write_bytes(before)
                result = self.restore()
                self.assertEqual(len(result["restored"]),1)
                after = self.path.read_bytes()
                # Subtract ONLY new complete lines: every original byte remains.
                stripped = re.sub(rb'  <actionmap name=.*?</actionmap>\r\n',b'',after,flags=re.S)
                self.assertEqual(stripped,before)
                profiles = heal.profile_root(after.decode("utf-8-sig"))
                self.assertEqual(len(profiles.findall("actionmap")),1)
                self.assertIn(b"\r\n    <rebind",after)

    def test_lf_profile_keeps_eol_and_existing_bytes(self):
        text = RESET.replace("\r\n","\n")
        self.path.write_bytes(text.encode("utf-8"))
        self.restore()
        after = self.path.read_bytes()
        self.assertNotIn(b"\r",after)
        self.assertEqual(re.sub(rb'  <actionmap name=.*?</actionmap>\n',b'',after,flags=re.S),text.encode())

    def test_wrapper_rejects_other_shapes(self):
        for text in ('<ActionMaps/>','<ActionMaps><actionmap name="x"/></ActionMaps>',
                     '<ActionMaps><ActionProfiles/><ActionProfiles/></ActionMaps>',
                     '<ActionMaps><options/><ActionProfiles/></ActionMaps>',
                     '<ActionProfiles><other><actionmap name="x"/></other></ActionProfiles>'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                heal.profile_root(text)

    def test_modifier_order_is_not_a_user_move(self):
        value="kb1_rshift+rctrl+3"
        reader.atomic_json(self.custom,[dict(map="spaceship_general",action="doors",input=value)])
        text=heal.insert_bind(RESET,dict(map="spaceship_general",action="doors",input="kb1_rctrl+rshift+3"))
        self.path.write_bytes(text.encode())
        before=self.path.read_bytes()
        self.assertEqual(self.restore()["custom_updates"],[])
        self.assertEqual(json.loads(self.custom.read_text(encoding="utf-8"))[0]["input"],value)
        self.assertEqual(before,self.path.read_bytes())

    def test_missing_game_action_reports_once_and_is_never_written(self):
        rows=json.loads(self.custom.read_text(encoding="utf-8"))
        rows.append(dict(map="spaceship_general",action="gone",input="kb1_z"))
        reader.atomic_json(self.custom,rows)
        self.path.write_bytes(RESET.encode())
        first=self.restore()
        self.assertIn("no longer in the game",first["attention"][0])
        self.assertEqual(len(first["restored"]),1)
        self.assertNotIn(b' name="gone"',self.path.read_bytes())
        self.assertEqual(self.restore()["attention"],[])

    def test_partial_missing_marks_removed_and_explicit_rebind_reactivates(self):
        rows=self.add_second()
        text=heal.insert_bind(RESET,rows[0]);self.path.write_bytes(text.encode())
        result=self.restore()
        self.assertFalse(result["restored"])
        self.assertEqual(result["custom_updates"],[dict(map="spaceship_general",action="power",removed=True)])
        self.assertEqual(self.path.read_bytes(),text.encode())
        self.assertTrue(json.loads(self.custom.read_text(encoding="utf-8"))[1]["removed"])
        # A wipe does not revive remembered removals.
        self.path.write_bytes(RESET.encode())
        self.restore()
        self.assertNotIn(b' name="power"',self.path.read_bytes())
        # An explicit user rebind to the stored value clears removed.
        self.path.write_bytes(heal.insert_bind(RESET,rows[1]).encode())
        self.restore()
        self.assertNotIn("removed",json.loads(self.custom.read_text(encoding="utf-8"))[1])

    def test_present_clear_prevents_partial_restore(self):
        rows=self.add_second()
        rows[0]["input"]="kb1_ "
        self.path.write_bytes(heal.insert_bind(RESET,rows[0]).encode())
        result=self.restore()
        self.assertFalse(result["restored"])
        saved=json.loads(self.custom.read_text(encoding="utf-8"))
        self.assertTrue(saved[0]["cleared"])
        self.assertTrue(saved[1]["removed"])

    def test_one_restore_per_build_and_new_build_allows_restore(self):
        self.path.write_bytes(RESET.encode())
        self.assertTrue(self.restore()["restored"])
        state=json.loads((self.root/"restore-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["buildId"],"one")
        self.assertEqual(state["count"],1)
        self.assertIsInstance(state["at"],float)
        self.path.write_bytes(RESET.encode())
        result=self.restore()
        self.assertFalse(result["restored"]);self.assertTrue(result["attention"])
        self.assertEqual(self.path.read_bytes(),RESET.encode())
        self.live=core.LiveBindings(fixtures.raw("x",build="two"),self.path)
        self.assertTrue(self.restore()["restored"])

    def test_closed_before_writes_and_during_final_check(self):
        self.path.write_bytes(RESET.encode());before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,"closed"):
            heal.restore(self.live,self.root,core,reader,True,lambda:False,lambda:True)
        self.assertEqual(before,self.path.read_bytes())
        self.assertFalse((self.root/"backups").exists())
        closed=[False];calls=[0]
        def process():
            calls[0]+=1
            if calls[0]==2:closed[0]=True
            return False
        # Simulate closure between staging and the final OS replace.
        # atomic_bytes checks closure again after process enumeration.
        original=heal.atomic_bytes
        with patch.object(heal,"atomic_bytes",wraps=original):
            try:
                heal.restore(self.live,self.root,core,reader,True,process,lambda:closed[0])
            except ValueError:
                pass
        self.assertEqual(before,self.path.read_bytes())


class ProcessAndLeaseTests(unittest.TestCase):
    def test_windows_structure_layout(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_size_t),8)
        self.assertEqual(ctypes.sizeof(heal.PROCESSENTRY32W),568)
        self.assertEqual(heal.PROCESSENTRY32W.th32DefaultHeapID.offset,16)
        self.assertEqual(heal.PROCESSENTRY32W.szExeFile.offset,44)

    def test_injected_snapshot_self_check(self):
        own=(os.getpid(),os.path.basename(sys.executable).upper())
        self.assertFalse(heal.game_running(lambda:[(4,"System"),own,(999,"Registry")]))
        self.assertTrue(heal.game_running(lambda:[own,(42,"sTaRcItIzEn.ExE")]))
        for entries in ([],[(os.getpid(),"unreadable")],[(999,own[1])]):
            self.assertTrue(heal.game_running(lambda:entries))
        def failure():raise OSError("snapshot failed")
        self.assertTrue(heal.game_running(failure))

    def test_toolhelp_snapshot_calls_and_handle_cleanup(self):
        own=(os.getpid(),os.path.basename(sys.executable))
        for entries,error,first_ok,expected in (
                ([own,(42,"Registry")],18,True,False),
                ([own,(43,"StarCitizen.exe")],18,True,True),
                ([(os.getpid(),"garbled")],18,True,True),
                ([own],5,True,True),([own],5,False,True)):
            with self.subTest(entries=entries,error=error):
                iterator=iter(entries)
                def populate(handle,pointer):
                    try:pid,name=next(iterator)
                    except StopIteration:return False
                    entry=pointer._obj
                    self.assertEqual(entry.dwSize,568)
                    entry.th32ProcessID=pid
                    for i in range(260):entry.szExeFile[i]=0
                    encoded=name.encode("utf-16-le")
                    for i in range(len(encoded)//2):
                        entry.szExeFile[i]=int.from_bytes(encoded[2*i:2*i+2],"little")
                    return True
                kernel=types.SimpleNamespace(
                    CreateToolhelp32Snapshot=Mock(return_value=123),
                    Process32FirstW=Mock(side_effect=populate if first_ok else None,return_value=False),
                    Process32NextW=Mock(side_effect=populate),CloseHandle=Mock(return_value=True))
                with patch.object(heal.sys,"platform","win32"),patch.object(heal.ctypes,"WinDLL",return_value=kernel,create=True),patch.object(heal.ctypes,"get_last_error",return_value=error,create=True):
                    self.assertEqual(heal.game_running(),expected)
                kernel.CloseHandle.assert_called_once_with(123)

    def test_lease_exclusion_expiry_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="sc-kb-public-lease-") as folder:
            root=Path(folder);path=root/"startup-heal.lock"
            with heal.startup_lock(root) as acquired:
                self.assertTrue(acquired)
                with heal.startup_lock(root) as second:self.assertFalse(second)
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())
            path.write_bytes(b"crashed")
            os.utime(path,(0,0))
            with self.assertRaisesRegex(RuntimeError,"test"):
                with heal.startup_lock(root) as acquired:
                    self.assertTrue(acquired)
                    raise RuntimeError("test")
            self.assertFalse(path.exists())
            with heal.startup_lock(root,lambda:True) as acquired:self.assertFalse(acquired)
            self.assertFalse(path.exists())


class ReviewStartupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.StartupTests.asyncSetUp
    startup=fixtures.StartupTests.startup
    flags=fixtures.StartupTests.flags

    async def test_false_twice_then_save_and_closed_retry_rollback(self):
        await self.startup(fixtures.raw())
        calls=[]
        async def save():
            calls.append(1)
            return len(calls)>2
        self.s.wingman.commands.save=save
        self.s._save_retry_seconds=.001
        result=await self.startup(fixtures.raw("ralt+f3"))
        self.assertEqual(len(calls),3)
        self.assertEqual(len(result["fixed"]),1)
        await self.startup(fixtures.raw())
        before=copy.deepcopy(self.live_commands)
        async def close():
            self.s._closed=True
            return False
        self.s.wingman.commands.save=close
        live=core.LiveBindings(fixtures.raw("ralt+f3"),self.root/"actionmaps.xml")
        await asyncio.to_thread(self.s._startup_heal,live)
        self.assertEqual(before,self.live_commands)
        self.assertFalse((self.root/"startup-heal.lock").exists())

    async def test_hung_save_is_bounded_and_rolled_back(self):
        await self.startup(fixtures.raw())
        before=copy.deepcopy(self.live_commands)
        async def save():await asyncio.Event().wait()
        self.s.wingman.commands.save=save
        self.s._save_timeout_seconds=.01
        result=await self.startup(fixtures.raw("ralt+f3"))
        self.assertEqual(before,self.live_commands)
        self.assertTrue(result["attention"]);self.assertFalse(result["fixed"])

    async def test_minimal_diff_nullable_modifier_and_exact_reverse(self):
        for a in self.live_commands[0]["actions"]:
            if a.get("keyboard",{}).get("press"):
                a["keyboard"]["release"]=None
                a["keyboard"]["hotkey_codes"]=None
        original=copy.deepcopy(self.live_commands)
        await self.startup(fixtures.raw())
        result=await self.startup(fixtures.raw("ralt+f3"))
        self.assertEqual(len(result["fixed"]),1)
        self.assertEqual(self.live_commands[0]["actions"][0],original[0]["actions"][0])
        self.assertIn("Right Alt plus F3",self.logs[-1])
        self.assertIn("Lock Doors",self.logs[-1])
        await self.startup(fixtures.raw())
        self.assertEqual(original,self.live_commands)

    async def test_drift_failure_does_not_poison_index_and_is_idempotent(self):
        await self.startup(fixtures.raw())
        finish=self.s._finish_heal
        def locked_report(*args):
            self.assertTrue((self.root/"startup-heal.lock").exists())
            self.assertTrue(self.s._healing)
            return finish(*args)
        with patch.object(self.s,"_finish_heal",side_effect=locked_report),patch.object(self.s,"_check_drift",return_value=None):
            result=await self.startup(fixtures.raw("z"))
            self.assertTrue(result["attention"])
            self.assertFalse(self.s._stale);self.assertEqual(self.s._error,"")
            self.assertFalse(self.s._healing)
            count=len(self.logs)
            await self.startup(fixtures.raw("z"))
            self.assertEqual(count,len(self.logs))
        self.assertFalse((self.root/"startup-heal.lock").exists())

    async def test_restore_failure_still_advances_snapshot_once(self):
        await self.startup(fixtures.raw())
        self.flags(restore_custom_binds=True)
        reader.atomic_json(self.root/"custom_binds.json",[dict(invalid=True)])
        result=await self.startup(fixtures.raw("z"))
        self.assertTrue(result["changed"])
        count=len(self.logs)
        result=await self.startup(fixtures.raw("z"))
        self.assertFalse(result["changed"])
        self.assertEqual(count,len(self.logs))

    async def test_lookup_skips_drift_while_heal_owns_lock(self):
        path=self.root/"actionmaps.xml";path.write_bytes(RESET.encode())
        self.s._setting="auto";self.s._bindings=core.LiveBindings(fixtures.raw(),path)
        self.s._bindings.refresh()
        entered,release=threading.Event(),threading.Event()
        def worker():
            with self.s._drift_lock:
                entered.set();release.wait(2)
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with patch.object(self.s,"_check_drift",side_effect=AssertionError("blocked lookup")):
                answer=self.s._lookup(key="ralt+3")
            self.assertNotIn("outdated",answer);self.assertNotIn("Refreshing",answer)
        finally:release.set();thread.join(2)

    async def test_held_lease_skips_silently(self):
        with heal.startup_lock(self.root):
            with patch.object(self.s,"_heal",side_effect=AssertionError("second healer")):
                await asyncio.to_thread(self.s._startup_heal,core.LiveBindings(fixtures.raw(),self.root/"actionmaps.xml"))
        self.assertFalse(self.logs)
        self.assertFalse((self.root/"last-changes.json").exists())

    async def test_refresh_flag_cleared_before_heal(self):
        game=self.root/"LIVE";game.mkdir();(game/"Data.p4k").write_bytes(b"synthetic")
        (game/"build_manifest.id").write_text('{"BuildId":"one"}',encoding="utf-8")
        data=fixtures.raw();stat=(game/"Data.p4k").stat()
        data["metadata"].update(p4kSize=stat.st_size,p4kMtime=reader._iso(stat.st_mtime),extractorVersion=2)
        reader.atomic_json(self.root/"defaults.json",data)
        observed=[]
        original=self.s._heal
        def check(live):
            observed.append((self.s._refreshing,threading.get_ident()))
            with patch.object(self.s,"_check_drift",return_value=None):original(live)
        self.s._refreshing=True
        with patch.object(self.m,"locate_game",return_value=game),patch.object(self.s,"_heal",side_effect=check):
            await asyncio.to_thread(self.s._refresh,"auto")
        self.assertEqual(observed[0][0],False)
        self.assertNotEqual(observed[0][1],threading.get_ident())
        self.assertFalse(self.s._stale);self.assertEqual(self.s._error,"")
        self.assertTrue(json.loads((self.root/"last-changes.json").read_text(encoding="utf-8"))["attention"])

    async def test_timeout_with_refused_cancel_settles_success(self):
        await self.startup(fixtures.raw())
        real_submit=asyncio.run_coroutine_threadsafe
        class TimeoutOnce:
            def __init__(self,future):self.future=future;self.calls=0
            def result(self,timeout=None):
                self.calls+=1
                if self.calls==1:raise self_module.concurrent.futures.TimeoutError()
                return self.future.result(timeout=5)
            def cancel(self):return False
        self_module=self.m
        with patch.object(self.m.asyncio,"run_coroutine_threadsafe",
                          side_effect=lambda coroutine,loop:TimeoutOnce(real_submit(coroutine,loop))):
            result=await self.startup(fixtures.raw("ralt+f3"))
        self.assertEqual(len(result["fixed"]),1)
        self.assertFalse(result["attention"])
