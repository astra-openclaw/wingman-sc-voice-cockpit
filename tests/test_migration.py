import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
def tool(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/"tools"/(name+".py"))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def test_migrate_dry_run_idempotence_and_preservation(tmp_path):
    kb=tmp_path/"kb";nav=tmp_path/"nav";out=tmp_path/"new"
    kb.mkdir();nav.mkdir()
    for name in ("defaults.json","defaults.json.entries.json","command_manifest.json","custom_binds.json"):
        (kb/name).write_text("{}",encoding="utf-8")
    for name in ("navpoints.db","dashboard_token","calibration_site.json","learned_rotation.npz"):
        (nav/name).write_bytes(b"synthetic")
    (nav/"navpoint_poll.log").write_text("excluded",encoding="utf-8")
    (nav/"refused").mkdir();(nav/"refused"/"frame.png").write_bytes(b"excluded")
    m=tool("migrate_from_skills")
    rows=m.migrate(kb,nav,out)
    assert len(rows)==8 and not out.exists()
    m.migrate(kb,nav,out,True)
    assert {p.name for p in out.iterdir()}=={r["name"] for r in rows}
    assert all(r["status"]=="identical" for r in m.migrate(kb,nav,out,True))
    before={p.name:p.read_bytes() for p in out.iterdir()}
    (kb/"custom_binds.json").write_text("changed",encoding="utf-8")
    with pytest.raises(ValueError):m.migrate(kb,nav,out,True)
    assert {p.name:p.read_bytes() for p in out.iterdir()}==before

def test_hot_database_refused_before_any_copy(tmp_path):
    kb=tmp_path/"kb";nav=tmp_path/"nav";out=tmp_path/"new";kb.mkdir();nav.mkdir()
    (nav/"navpoints.db").write_bytes(b"db")
    (nav/"navpoints.db-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError):tool("migrate_from_skills").migrate(kb,nav,out,True)
    assert not out.exists()

def test_rewire_changes_only_command_scalar_and_keeps_bom_crlf(tmp_path):
    p=tmp_path/"Wingman.yaml"
    text='''# keep comment
skills:
- module: skills.sc_keybindings.main
commands:
- name: Reset
  actions:
  - skill_action:
      skill_name: "SC_Keybindings"
      function_name: fresh_start_keybindings
      parameters: {}
other: SC_Keybindings
'''
    raw=b"\xef\xbb\xbf"+text.replace("\n","\r\n").encode()
    p.write_bytes(raw);m=tool("rewire_commands")
    diff=m.run(p)
    assert p.read_bytes()==raw and 'SC_VoiceCockpit' in diff
    m.run(p,True)
    expected=raw.replace(b'"SC_Keybindings"',b'"SC_VoiceCockpit"')
    assert p.read_bytes()==expected
    assert p.with_name(p.name+".pre-sc-voice-cockpit").read_bytes()==raw
    assert m.run(p,True)==""
