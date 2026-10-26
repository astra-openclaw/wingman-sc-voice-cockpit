"""Offline, dry-run-first generated-data migration. No Wingman HTTP calls."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

KB = {"command_manifest.json","custom_binds.json","command_steps.json","bindings_snapshot.json",
      "bindings.json","command_baseline.json","drift-report.json","last-changes.json","restore-state.json",
      "defaults.json","defaults.json.entries.json","fresh-start-state.json","fresh-start-report.json"}
NAV = {"navpoints.db","navpoints.db-wal","navpoints.db-shm","dashboard_token",
       "learned_fields.json","calibration.json","navpoint_calibration.json","learned.json"}

def data_dir(path):
    path=Path(path)
    return path/"generated_files" if (path/"generated_files").is_dir() else path

def plan(old_keybindings,old_navpoint,new):
    new=Path(new);rows=[]
    for source,names in ((data_dir(old_keybindings),KB),(data_dir(old_navpoint),NAV)):
        for path in sorted(source.iterdir()):
            # Root-only learned/calibration data, never logs/refused frames or models.
            allowed=path.name in names or (path.suffix in {".json",".npz"} and
                      path.name.startswith(("learned_","calibration_")))
            if not path.is_file() or path.is_symlink() or not allowed:continue
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            target=new/path.name
            status="copy"
            if target.exists():
                if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                    raise ValueError("Refusing different existing destination: "+path.name)
                status="identical"
            rows.append((path,target,digest,status))
    # DB files must come from a stopped skill / coherent backup. Refuse a hot WAL.
    if any(p.name=="navpoints.db-wal" and p.stat().st_size for p,_,_,_ in rows):
        raise ValueError("NavPoint WAL is nonempty: stop Wingman and take a coherent SQLite backup first")
    return rows

def migrate(old_keybindings,old_navpoint,new,apply=False):
    rows=plan(old_keybindings,old_navpoint,new)
    if apply:
        Path(new).mkdir(parents=True,exist_ok=True)
        for source,target,digest,status in rows:
            if status=="identical":continue
            # Exclusive create, never overwrite a destination changed after preflight.
            with target.open("xb") as out:out.write(source.read_bytes())
            if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError("Readback mismatch")
    return [{"name":p.name,"sha256":digest,"status":status} for p,_,digest,status in rows]

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-keybindings",required=True,type=Path);ap.add_argument("--old-navpoint",required=True,type=Path)
    ap.add_argument("--new",required=True,type=Path);ap.add_argument("--apply",action="store_true");args=ap.parse_args()
    try:print(json.dumps({"applied":args.apply,"files":migrate(args.old_keybindings,args.old_navpoint,args.new,args.apply)},indent=2))
    except (ValueError,OSError) as error:ap.exit(1,str(error)+"\n")
