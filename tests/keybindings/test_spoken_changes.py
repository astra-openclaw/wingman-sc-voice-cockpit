"""Synthetic one-time spoken startup notes; no model or audio hardware required."""
import asyncio
import copy
import itertools
import json
import time
import unittest
from unittest.mock import patch

import test_self_heal as fixtures
from sc_voice_cockpit.keybindings import keybinds_core as core, self_heal as heal


def report(**overrides):
    data=dict(changed=[],cause="your controls",fixed=[],restored=[],
              custom_updates=[],deferred=[],attention=[])
    data.update(overrides)
    return data


def fixed(name="Secure All Hatches", key="ralt+f3"):
    return dict(name=name,steps=[{"from":"ralt+3","to":key}])


class SummaryTests(unittest.TestCase):
    def test_human_keys_and_one_complete_command_detail(self):
        data=report(changed=["a","b"],fixed=[fixed(),fixed("Other Command")])
        line=heal.spoken_change(data,core)
        self.assertIn("2 keys moved",line)
        self.assertIn("updated 2 voice commands",line)
        self.assertIn("Secure All Hatches now uses Right Alt plus F3",line)
        self.assertNotIn("Other Command",line)
        self.assertNotIn("ralt",line);self.assertNotIn("Mention briefly once",line)
        self.assertLessEqual(len(line.split()),30)
        log=heal.log_line(data,core)
        self.assertTrue(log.startswith("Keybindings: since last session "))
        self.assertIn("Secure All Hatches",log)
        self.assertIn("Right Alt plus 3 to Right Alt plus F3",log)
        self.assertNotIn("Mention briefly once",log)
        self.assertIn("Mention briefly once.",heal.change_line(data,core))

    def test_restore_defer_removal_attention_and_recorded_changes(self):
        cases=[(report(restored=["a"]*4),"restored your 4 custom keys"),
               (report(deferred=["a"]*4),"close Star Citizen and restart Wingman"),
               (report(custom_updates=[dict(removed=True)]*4),"4 of your custom keys were removed"),
               (report(attention=[dict(name="Example")]*4),"4 items need a look"),
               (report(custom_updates=[dict(cleared=True)]),"recorded 1 change to your custom keys")]
        for data,expected in cases:
            with self.subTest(expected=expected):
                text=heal.spoken_change(data,core)
                self.assertIn(expected,text);self.assertLessEqual(len(text.split()),30)
                self.assertTrue(text.endswith("."))
        self.assertEqual(heal.spoken_change(report(),core),"")

    def test_combined_caps_and_word_safe_names(self):
        data=report(changed=["a"]*200,fixed=[fixed("a "*40)],restored=["a"]*20,
                    deferred=["a"]*20,custom_updates=[dict(removed=True)]*20,
                    attention=[dict(name="a")]*31)
        fields=("changed","fixed","restored","deferred","custom_updates","attention")
        for bits in itertools.product((False,True),repeat=6):
            case=copy.deepcopy(data)
            for key,on in zip(fields,bits):
                if not on:case[key]=[]
            spoken=heal.spoken_change(case,core)
            self.assertLessEqual(len(spoken.split()),30)
            self.assertLessEqual(len(spoken),361)
            self.assertNotIn("ralt",spoken)
            self.assertNotIn("Mention briefly once",heal.log_line(case,core))
        self.assertEqual(heal.short_command("Complete Name IndivisiblyLongFinalWord"),"Complete Name")
        self.assertEqual(heal.short_command("界"*100),"command")
        # Do not truncate a complex chord into another key.
        line=heal.spoken_change(report(fixed=[fixed("Short Name",key="rctrl+rshift+np_enter")]),core)
        self.assertIn("Right Control plus Right Shift plus numpad Enter",line)


class SpokenLookupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.StartupTests.asyncSetUp
    startup=fixtures.StartupTests.startup
    flags=fixtures.StartupTests.flags

    async def moved_note(self):
        await self.startup(fixtures.raw())
        await self.startup(fixtures.raw("ralt+f3"))
        self.s._setting="auto"
        self.s._bindings=core.LiveBindings(fixtures.raw("ralt+f3"),self.root/"actionmaps.xml")
        self.s._bindings.refresh()
        return self.s._change_note["spoken"]

    async def lookup(self):
        return await self.s.look_up_keybinding(key="ralt+f3")

    async def test_appends_once_after_endearment_and_clears_both_texts(self):
        summary=await self.moved_note()
        self.s.endearments="friend"
        self.assertIn("Mention briefly once.",await self.s.get_prompt())
        result,spoken=await self.lookup()
        self.assertIn(", friend. By the way,",spoken)
        self.assertTrue(spoken.endswith(summary))
        self.assertIn(spoken,result)
        self.assertIsNone(self.s._change_note)
        self.assertNotIn("Mention briefly once.",await self.s.get_prompt())
        self.assertNotIn("By the way", (await self.lookup())[1])

    async def test_expiry_applies_without_prompt_read(self):
        await self.moved_note()
        self.s._change_note["at"]=time.monotonic()-601
        self.assertNotIn("By the way",(await self.lookup())[1])
        self.assertIsNone(self.s._change_note)

    async def test_no_change_pending_is_not_appended(self):
        await self.startup(fixtures.raw())
        self.s._setting="auto"
        self.s._bindings=core.LiveBindings(fixtures.raw(),self.root/"actionmaps.xml")
        self.assertNotIn("By the way",(await self.lookup())[1])
        self.assertIsNone(self.s._change_note);self.assertFalse(self.logs)

    async def test_assistant_answer_wins_over_lookup_but_tool_planning_does_not(self):
        await self.moved_note()
        await self.s.on_add_assistant_message("planning",[{}])
        self.assertIsNotNone(self.s._change_note)
        await self.s.on_add_assistant_message("answer",[])
        self.assertNotIn("By the way",(await self.lookup())[1])
        self.assertIsNone(self.s._change_note)

    async def test_full_appended_guard_accepts_and_skips_other_lines(self):
        summary=await self.moved_note()
        spoken=(await self.lookup())[1]
        self.assertTrue(spoken.endswith(summary))
        self.assertEqual(self.s._guard["text"],self.s._normalized(spoken))
        self.assertEqual(await self.s.on_play_to_user(spoken,None),spoken)
        self.assertEqual(await self.s.on_play_to_user("Another overlapping line",None),"{SKIP-TTS}")

    async def test_caps_include_summary_and_full_guard(self):
        summary=await self.moved_note()
        with patch.object(self.s._bindings.engine,"spoken",return_value="Verified answer. "*200):
            result,spoken=await self.lookup()
        self.assertLessEqual(len(result),1200);self.assertLessEqual(len(spoken),1200)
        self.assertTrue(spoken.endswith(summary))
        self.assertEqual(self.s._guard["text"],self.s._normalized(spoken))

    async def test_log_is_human_and_note_keeps_both_until_consumed(self):
        await self.moved_note()
        note=self.s._change_note
        self.assertEqual(set(note),{"prompt","spoken","at"})
        self.assertIn("Mention briefly once.",note["prompt"])
        self.assertNotIn("Mention briefly once",note["spoken"])
        self.assertEqual(len(self.logs),1)
        self.assertNotIn("Mention briefly once",self.logs[0])
        self.assertIn("Lock Doors",self.logs[0]);self.assertIn("Right Alt plus F3",self.logs[0])

    async def test_concurrent_lookups_only_one_consumes_note(self):
        summary=await self.moved_note()
        answers=await asyncio.gather(self.lookup(),self.lookup())
        self.assertEqual(sum(summary in answer[1] for answer in answers),1)
        self.assertIsNone(self.s._change_note)

    async def test_nonspoken_empty_lookup_does_not_consume_note(self):
        await self.moved_note()
        response=await self.s.look_up_keybinding()
        self.assertIsInstance(response,str)
        self.assertIsNotNone(self.s._change_note)
