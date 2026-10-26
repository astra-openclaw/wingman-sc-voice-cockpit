"""Re-point only command skill_action names, preserving all other YAML bytes."""
import argparse
import difflib
from pathlib import Path
import re
import shutil
import yaml

def rewrite(text):
    # YAML node marks let us replace only the scalar in command actions, without
    # reserializing a private profile or touching unrelated skill configuration.
    tree=yaml.compose(text);changes=[]
    def mapping(node):
        return {k.value:v for k,v in node.value} if isinstance(node,yaml.MappingNode) else {}
    root=mapping(tree)
    commands=root.get("commands")
    if not isinstance(commands,yaml.SequenceNode):return text
    for command in commands.value:
        actions=mapping(command).get("actions")
        if not isinstance(actions,yaml.SequenceNode):continue
        for action in actions.value:
            skill=mapping(mapping(action).get("skill_action"))
            node=skill.get("skill_name")
            if node is not None and node.value=="SC_Keybindings":
                old=text[node.start_mark.index:node.end_mark.index]
                new=old.replace("SC_Keybindings","SC_VoiceCockpit")
                changes.append((node.start_mark.index,node.end_mark.index,new))
    for start,end,new in reversed(changes):text=text[:start]+new+text[end:]
    return text

def run(path,apply=False):
    raw=path.read_bytes();text=raw.decode("utf-8-sig");new=rewrite(text)
    if new==text:return ""
    diff="".join(difflib.unified_diff(text.splitlines(True),new.splitlines(True),fromfile="Wingman.yaml before",tofile="Wingman.yaml after",n=0))
    if apply:
        backup=path.with_name(path.name+".pre-sc-voice-cockpit")
        if backup.exists() and backup.read_bytes()!=raw:raise ValueError("Backup already exists with different data")
        if not backup.exists():
            with backup.open("xb") as out:out.write(raw)
        if path.read_bytes()!=raw:raise ValueError("Config changed during planning")
        staged=path.with_name(path.name+".sc-voice-cockpit-tmp")
        with staged.open("xb") as out:out.write((b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"")+new.encode("utf-8"))
        staged.replace(path)
    return diff

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument("--config",required=True,type=Path);ap.add_argument("--apply",action="store_true");args=ap.parse_args()
    try:print(run(args.config,args.apply) or "No changes.")
    except (ValueError,OSError) as error:ap.exit(1,str(error)+"\n")
