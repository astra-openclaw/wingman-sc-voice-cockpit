"""Fresh-start speech and shared visible diagnostics use final state only."""
import copy
import itertools
import unittest

from sc_voice_cockpit.keybindings import self_heal as heal, keybinds_core as core
from test_self_heal import raw


class FreshReportTests(unittest.TestCase):
    def report(self, commands=None, attention=None, repair=None, restored=None,
               deferred=None, trigger="command", metadata=None):
        data=raw()
        data["actions"][0].update(mapLabel="Ship Controls",label="Lock Doors")
        data["metadata"].update(branch="sc-alpha-4.10.2",version="4.10.197.2878")
        if metadata is not None:data["metadata"]=metadata
        return heal.fresh_report(data,dict(commands=commands or []),
            repair or dict(attention=[],fixed=[]),
            dict(attention=attention or [],restored=restored or [],deferred=deferred or []),
            core,trigger)

    def key(self,kind,action="doors"):
        return dict(kind=kind,map="spaceship_general",action=action,reason=
                    "you changed this key; kept it as it is" if kind=="left"
                    else "this action no longer exists in the game")

    def test_stale_repair_attention_dropped_after_final_ok(self):
        commands=[dict(name="Power Engines",status="OK")]
        old=dict(attention=[dict(name="Power Engines",reason="no unchanged recorded step intent"),
                            dict(name="Command repair",reason="save was not ready")],fixed=[])
        report=self.report(commands=commands,repair=old,restored=["a"]*8)
        self.assertEqual(report["attention"],[])
        self.assertEqual(report["details"],"")
        self.assertIn("1 voice command checked, all OK",report["spoken"])
        self.assertIn("put back 8 custom keys",report["spoken"])
        self.assertNotIn("notices",report["log"]);self.assertNotIn("Details are",report["spoken"])

    def test_only_final_non_ok_commands_keep_human_repair_details(self):
        commands=[dict(name="Door Lock",status="OK"),dict(name="Power Engines",status="DRIFT")]
        repair=dict(attention=[dict(name="Door Lock",reason="action removed"),
                               dict(name="Power Engines",reason="no unchanged recorded step intent")])
        report=self.report(commands=commands,repair=repair)
        self.assertEqual(report["attention"],["Power Engines: no matching saved key history; check this command"])
        self.assertIn("2 voice commands checked, 1 needs a look",report["spoken"])
        self.assertIn("Details are in the Wingman log",report["spoken"])
        self.assertNotIn("Door Lock",report["log"])

    def test_new_final_drift_edited_unaudited_are_all_visible(self):
        commands=[dict(name=name,status=status) for name,status in
                  (("Changed", "DRIFT"),("Edited", "EDITED"),("Unchecked", "UNAUDITED"))]
        report=self.report(commands=commands)
        self.assertEqual(report["drift"],3)
        self.assertIn("3 voice commands checked, 3 need a look",report["spoken"])
        self.assertTrue(all(name in report["details"] for name in ("Changed","Edited","Unchecked")))
        self.assertNotIn("UNAUDITED",report["log"])

    def test_every_normal_spoken_clause_in_order_with_singular_and_plural(self):
        for n in (1,2):
            report=self.report(commands=[dict(name="OK",status="OK")],
                               attention=[self.key("left")]*n+[self.key("missing","absent")]*n)
            speech=report["spoken"]
            key="key" if n==1 else "keys"
            self.assertIn(f"left {n} {key} you changed as "+("it is" if n==1 else "they are"),speech)
            self.assertIn(f"{n} listed {key} no longer "+("exists" if n==1 else "exist")+" in the game",speech)
            self.assertLess(speech.index("left "),speech.index("listed "))
            self.assertIn("Details are in the Wingman log",speech)
            self.assertEqual(report["left"],n);self.assertEqual(report["missing"],n)
        speech=self.report(restored=["a"])["spoken"]
        self.assertIn("put back 1 custom key",speech)
        self.assertNotIn("voice commands",speech)
        self.assertNotIn("Details are",speech)

    def test_trigger_specific_deferred_sentence_and_visible_key_name(self):
        for trigger,instruction in (("command","say fresh start keybindings again"),
                                    ("switch","switch Fresh start off and on again")):
            report=self.report(commands=[dict(name="OK",status="OK")],
                               deferred=["spaceship_general.doors"],trigger=trigger)
            self.assertIn("Close Star Citizen and "+instruction+" to put back 1 key",report["spoken"])
            self.assertIn("Ship Controls / Lock Doors: close Star Citizen",report["log"])
            self.assertNotIn("spaceship_general.doors",report["log"])
            self.assertIn("Details are in the Wingman log",report["spoken"])

    def test_branch_version_and_two_part_build_fallback(self):
        cases=[(dict(branch="sc-alpha-4.10.2",version="4.10.197.2878"),"4.10.2"),
               (dict(branch="sc-alpha-4.10.2-ptu",version="4.10.197.2878"),"4.10.2"),
               (dict(branch="",version="4.10.197.2878"),"4.10"),
               (dict(branch="unknown",version="4.10"),"4.10"),
               (dict(branch="sc-alpha-4.10.197.2878",version="4.10.197.2878"),"4.10"),
               (dict(version="unavailable"),"")]
        for metadata,expected in cases:
            with self.subTest(metadata=metadata):
                self.assertEqual(heal.fresh_version(metadata),expected)
                line=self.report(metadata=metadata)["spoken"]
                if expected:self.assertIn("from Star Citizen "+expected,line)
                else:self.assertNotIn("from Star Citizen",line)
                self.assertNotIn("4.10.197.2878",line)

    def test_shared_visible_list_human_labels_eight_item_bound(self):
        report=self.report(attention=[self.key("left")]*12)
        lines=report["details"].splitlines()
        self.assertEqual(len(lines),9)
        self.assertEqual(lines[-1],"- and 4 more")
        self.assertTrue(all("Ship Controls / Lock Doors: you changed this key" in line for line in lines[:8]))
        self.assertNotIn("device slot",report["log"])
        self.assertEqual(report["log"],report["spoken"]+"\n"+report["details"])
        self.assertEqual(len(report["attention"]),12)
        self.assertLessEqual(max(map(len,heal.fresh_details(["long "*500]*10).splitlines())),242)

    def test_word_cap_all_counts_bad_status_and_triggers(self):
        for trigger in ("command","switch"):
            for bits in itertools.product((False,True),repeat=5):
                bad,left,missing,put_back,deferred=bits
                commands=[dict(name="Command",status="DRIFT" if bad else "OK")]*90
                report=self.report(commands=commands,attention=
                    ([self.key("left")]*12 if left else [])+
                    ([self.key("missing","absent")]*7 if missing else []),
                    restored=["a"]*8 if put_back else [],
                    deferred=["spaceship_general.doors"]*8 if deferred else [],trigger=trigger)
                line=report["spoken"]
                self.assertLessEqual(len(line.split()),40,(trigger,bits,line))
                self.assertTrue(line.startswith("Fresh start done: re-imported"))
                self.assertIn("4.10.2",line)
                self.assertNotIn("notices",line)
                if report["attention"]:self.assertIn("Details are in the Wingman log",line)
                if deferred:
                    self.assertIn("switch Fresh start off and on again" if trigger=="switch" else
                                  "say fresh start keybindings again",line)

    def test_no_input_mutations_or_technical_ids_in_speech(self):
        data=raw();commands=dict(commands=[dict(name="Door Lock",status="DRIFT")])
        repairs=dict(attention=[dict(name="Door Lock",reason="action removed")])
        restored=dict(restored=["spaceship_general.doors"],attention=[self.key("left")],deferred=[])
        before=copy.deepcopy((data,commands,repairs,restored))
        report=heal.fresh_report(data,commands,repairs,restored,core,"command")
        self.assertEqual((data,commands,repairs,restored),before)
        self.assertNotIn("spaceship_general",report["spoken"])
        self.assertNotIn("Door Lock",report["spoken"])


if __name__=="__main__":unittest.main()
