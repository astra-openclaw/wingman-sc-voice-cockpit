"""Local startup repair planning; no input, network, or import-time work.

Integration: Wingman 3.2.11 SkillCommands.all/save (wingmen/facade.py).
Only the adapter mutates live command models, on its captured event loop.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

# keyboard-library names, not scan codes. Unknown/axis/scroll inputs fail closed.
KEYS = {
    "lalt":"alt", "ralt":"alt gr", "lctrl":"ctrl", "rctrl":"right ctrl",
    "lshift":"shift", "rshift":"right shift", "return":"enter", "escape":"esc",
    "pgup":"page up", "pgdn":"page down", "capslock":"caps lock",
    "np_add":"add", "np_subtract":"subtract", "np_multiply":"multiply",
    "np_divide":"divide", "np_enter":"num enter", "np_period":"decimal",
    "comma":",", "period":".", "minus":"-", "equals":"=", "semicolon":";",
    "apostrophe":"'", "slash":"/", "backslash":"\\", "lbracket":"[", "rbracket":"]",
}
RIGHT = {"ralt", "rctrl", "rshift"}
MOUSE = {"mouse1":"left", "mouse2":"right", "mouse3":"middle", "mouse4":"x", "mouse5":"x2"}


class PROCESSENTRY32W(ctypes.Structure):
    # Fixed Windows DWORD/LONG/WCHAR widths, even in Linux layout tests.
    # ULONG_PTR is pointer-sized; WCHAR is a UTF-16 code unit, not POSIX wchar_t.
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_int32),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_uint16 * 260)]


def game_running(enumerate_processes=None):
    """Toolhelp executable-name snapshot: no protected-process opens required."""
    if enumerate_processes is not None:
        try:
            return snapshot_running(enumerate_processes())
        except Exception:
            return True
    if sys.platform != "win32":
        return True
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        for name in ("Process32FirstW", "Process32NextW"):
            fn = getattr(kernel, name)
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
            fn.restype = ctypes.c_int32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int32
        handle = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if handle in (None, ctypes.c_void_p(-1).value):
            return True
        try:
            entries = []
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
                return True  # Empty snapshot cannot pass the own-process ABI check.
            while True:
                name = bytes(entry.szExeFile).decode("utf-16-le").split("\0", 1)[0]
                entries.append((entry.th32ProcessID, name))
                if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                    return True if ctypes.get_last_error() != 18 else snapshot_running(entries)
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return True



def snapshot_running(entries):
    """Validate the ABI using our own PID and executable before trusting absence."""
    entries = list(entries)
    executable = os.path.basename(sys.executable).casefold()
    own = any(pid == os.getpid() and name.casefold() == executable for pid,name in entries)
    return not own or any(name.casefold() == "starcitizen.exe" for pid,name in entries)


@contextmanager
def startup_lock(generated, closed=lambda:False, reclaim=True):
    """Cross-instance exclusion. A crashed healer's lease expires in ten minutes."""
    path = Path(generated)/"startup-heal.lock"
    owned = None
    try:
        if closed():
            yield False
            return
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            existing = path.stat()
        except FileNotFoundError:
            existing = None
        if reclaim and existing and time.time()-existing.st_mtime > 600:
            if closed():
                yield False
                return
            # Avoid removing a replacement lease after our stat.
            if path.stat().st_ino == existing.st_ino:
                path.unlink()
        if closed():
            yield False
            return
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            yield False
            return
        with os.fdopen(fd,"wb") as stream:
            owned = os.fstat(stream.fileno())
            if not closed():
                stream.write(str(os.getpid()).encode("ascii"))
        yield not closed()
    finally:
        # Lock release is cleanup, even after unload.
        if owned:
            try:
                current = path.stat()
                if current.st_ino == owned.st_ino:
                    path.unlink()
            except FileNotFoundError:
                pass


def atomic_bytes(path, data, before=None, check=None, closed=lambda:False):
    if closed():
        raise ValueError("Skill closed; no further writes")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if closed():
                raise ValueError("Skill closed; no further writes")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None and path.read_bytes() != before:
            raise ValueError("Profile changed during restore")
        if check and check():
            raise ValueError("Game running or process enumeration uncertain; restore deferred")
        if closed():
            raise ValueError("Skill closed; no further writes")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value, closed=lambda:False):
    data = json.dumps(value,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    atomic_bytes(path,data,closed=closed)


def backup(generated, kind, data, closed=lambda:False):
    if closed():
        raise ValueError("Skill closed; no further writes")
    folder = Path(generated) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (str(time.time_ns()) + "-" + kind)
    atomic_bytes(path, data, closed=closed)
    entries = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p:p.name)
    for old in entries[:-20]:
        if closed():
            raise ValueError("Skill closed; no further writes")
        old.unlink()
    return path


def snapshot(index):
    return {"buildId":index.metadata.get("buildId"), "actions":{
        r["map"]+"."+r["name"]:{d:r[d] for d in ("keyboard", "mouse")}
        for r in index.actions}}


def diff(old, new):
    if not old:
        return []
    return sorted(k for k in set(old["actions"]) | set(new["actions"])
                  if old["actions"].get(k) != new["actions"].get(k))


def steps(command, index, core, curated=False):
    return {"fingerprint":core.command_fingerprint(command), "steps":[
        {"event":i, "actions":sorted({r["map"]+"."+r["name"]
         for r,b in core.matches(e, command, index, curated)}), "key":e[0],
         "hold":e[1], "camera":e[2]} for i,e in enumerate(core.events(command))]}


def deck_tail(command):
    actions=command.get("actions") or []
    if not actions:return None
    action=actions[-1]
    if not isinstance(action,dict):return None
    step=action.get("skill_action") or {}
    if (step.get("skill_name")=="SC_VoiceCockpit" and step.get("function_name")=="deck_sync"
            and step.get("parameters",{}).get("command")==command.get("name")
            and all(v is None for k,v in action.items() if k!="skill_action")):
        return action
    return None


def segments(command, core):
    """Parse strict keyboard/mouse/wait macros and balanced modifier/F4 wrappers.

    A shared modifier around multiple key steps cannot be replaced independently.
    Refuse it instead of changing neighbouring steps' context.
    """
    active, groups, result = {}, [], []
    actions = command.get("actions") or []
    if deck_tail(command):actions=actions[:-1]
    for i, a in enumerate(actions):
        if not a or len([v for v in a.values() if v is not None]) != 1:
            raise ValueError("mixed or nullable action")
        k, m = a.get("keyboard"), a.get("mouse")
        if k:
            if k.get("hotkey_codes") or k.get("hotkey_extended"):
                # The one recorded scan-code shape whose name/identity we can
                # prove: Right Ctrl must retain its extended flag in Wingman.
                if not (core.normalize_key(k.get("hotkey") or "")=="rctrl"
                        and k.get("hotkey_codes")==[29] and k.get("hotkey_extended") is True):
                    raise ValueError("scan-code keyboard action")
            tokens = core.normalize_key(k.get("hotkey") or "").split("+")
            if not all(tokens):
                raise ValueError("empty hotkey")
            if k.get("press") != k.get("release"):
                if len(tokens) != 1 or tokens[0] not in (*core.MODIFIERS, "f4"):
                    raise ValueError("unsupported held key")
                key = tokens[0]
                if k.get("press"):
                    if key in active:
                        raise ValueError("duplicate key down")
                    active[key] = [i, None, key]
                else:
                    if key not in active:
                        raise ValueError("unmatched key up")
                    group = active.pop(key)
                    group[1] = i+1
                    groups.append(group)
                continue
        elif m:
            if m.get("scroll") is not None or m.get("move") is not None or m.get("move_to") is not None:
                raise ValueError("mouse movement/scroll")
            if m.get("button") not in MOUSE.values():
                raise ValueError("unknown mouse button")
        elif a.get("wait") is not None:
            if not isinstance(a["wait"], (int,float)) or not math.isfinite(a["wait"]) or a["wait"] < 0:
                raise ValueError("invalid wait")
            continue
        else:
            raise ValueError("non-key action")
        result.append({"index":i, "groups":[g for key,g in active.items() if key != "f4"],
                       "camera":"f4" in active})
    if active:
        raise ValueError("unclosed held key")
    for step in result:
        step["start"] = min([step["index"]] + [g[0] for g in step["groups"]])
        step["end"] = max([step["index"]+1] + [g[1] for g in step["groups"]])
    if any(a["end"] > b["start"] for a,b in zip(result, result[1:])):
        raise ValueError("shared modifier group")
    if len(result) != len(core.events(command)):
        raise ValueError("event parse mismatch")
    return result


def key_name(key):
    if key in KEYS:
        return KEYS[key]
    if re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])|space|tab|backspace|delete|insert|home|end|up|down|left|right", key):
        return key
    if re.fullmatch(r"np_[0-9]", key):
        return "num "+key[-1]
    raise ValueError("unsupported key")


def render(key, hold, core, waits=None):
    tokens = core.normalize_key(key).split("+")
    if not tokens or len(tokens) != len(set(tokens)):
        raise ValueError("invalid chord")
    mods = [k for k in tokens if k in core.MODIFIERS]
    base = [k for k in tokens if k not in core.MODIFIERS]
    if not base and len(mods) == 1:
        base, mods = mods, []
    if len(base) != 1:
        raise ValueError("non-chord input")
    split = mods if base[0] in MOUSE else [m for m in mods if m in RIGHT]
    combined = [m for m in mods if m not in split]
    # Two modifier sides of the same family cannot be faithfully executed.
    if len({m[1:] for m in mods}) != len(mods):
        raise ValueError("conflicting modifier sides")
    def keyboard_step(key, **values):
        row={"hotkey":key,**values}
        if key=="right ctrl":row.update(hotkey_codes=[29],hotkey_extended=True)
        return {"keyboard":row}
    result = [keyboard_step(key_name(m),press=True,release=False) for m in split]
    if split or waits:
        result.append({"wait":waits[0] if waits else .35})
    if base[0] in MOUSE:
        result.append({"mouse":{"button":MOUSE[base[0]], "hold":hold}})
    else:
        result.append(keyboard_step("+".join(key_name(k) for k in combined+base),hold=hold))
    if split or waits:
        result.append({"wait":waits[-1] if waits and len(waits)>1 else .1})
        result.extend(keyboard_step(key_name(m),press=False,release=True) for m in reversed(split))
    return result


def safe_bind(bind):
    try:
        return int(bind.get("multiTap") or 1) <= 1 and "double" not in (bind.get("activationMode") or "").lower()
    except (ValueError,TypeError):
        return False


def choose(index, intended, old, core):
    rows = {r["map"]+"."+r["name"]:r for r in index.actions}
    candidates = None
    per_key = {}
    for ident in intended:
        row = rows.get(ident)
        if not row:
            raise ValueError("action removed")
        current = {}
        for d in ("keyboard","mouse"):
            for bind in row[d]:
                if not safe_bind(bind):
                    continue
                key = core.normalize_key(bind["input"])
                try:
                    render(key, .1, core)
                except ValueError:
                    continue
                current.setdefault(key, []).append(bind)
        candidates = set(current) if candidates is None else candidates & set(current)
        for key, binds in current.items():
            per_key.setdefault(key, []).extend(binds)
    if not candidates:
        raise ValueError("no single supported input reaches every intended action")
    family = lambda key:tuple(k for k in key.split("+") if k in core.MODIFIERS)
    device = lambda key:"mouse" if any(k in MOUSE for k in key.split("+")) else "keyboard"
    rank = lambda key:(device(key)==device(old["key"]), family(key)==family(old["key"]))
    ranked = sorted(candidates, key=rank, reverse=True)
    best = [key for key in ranked if rank(key)==rank(ranked[0])]
    if len(best) != 1:
        raise ValueError("ambiguous current bindings")
    key = best[0]
    binds = per_key[key]
    hold = old["hold"]
    if hold is None or not all(core.applies(b, hold) for b in binds):
        thresholds = [float(b.get(k) or 0) for b in binds for k in
                      ("pressTriggerThreshold","holdTriggerDelay")]
        delayed = any((b.get("activationMode") or "").startswith(("hold","delayed")) or
                      float(b.get("holdTriggerDelay") or 0)>0 for b in binds)
        hold = max([.1]+thresholds)+.35 if delayed else .1
    if not math.isfinite(hold) or hold <= 0 or not all(core.applies(b,hold) for b in binds):
        raise ValueError("incompatible activation thresholds")
    return key, hold


def repair(command, entry, index, core, curated=False):
    if command.get("is_system_command"):
        raise ValueError("system command")
    if not entry or entry["fingerprint"] != core.command_fingerprint(command):
        raise ValueError("no unchanged recorded step intent")
    parsed = segments(command, core)
    recorded = entry["steps"]
    if len(parsed) != len(recorded):
        raise ValueError("step intent mismatch")
    changed, replacements = [], []
    for i, (segment, old, event) in enumerate(zip(parsed,recorded,core.events(command))):
        if old["event"] != i or old["camera"] != segment["camera"]:
            raise ValueError("camera or step mismatch")
        reached = {r["map"]+"."+r["name"] for r,b in core.matches(event,command,index,curated)}
        intended = set(old["actions"])
        if intended <= reached:
            continue
        key, hold = choose(index, intended, old, core)
        original = command["actions"][segment["start"]:segment["end"]]
        waits = [a["wait"] for a in original if a.get("wait") is not None]
        # Keep every original wait inside a modifier group too. Unsupported shapes skip.
        if waits and (len(waits)!=2 or len([a for a in command["actions"][segment["start"]:segment["index"]] if a.get("wait") is not None])!=1):
            raise ValueError("cannot preserve modifier waits")
        if old["camera"] and "f4" in key.split("+"):
            raise ValueError("replacement conflicts with held F4")
        actions = render(key, hold, core, waits)
        # Same modifier family and combined/split shape: preserve every original
        # field (including nullable press/release) and change only the base key.
        family = lambda k: tuple(t for t in core.normalize_key(k).split("+") if t in core.MODIFIERS)
        base_action = command["actions"][segment["index"]]
        rendered_steps = [a for a in actions if a.get("mouse") or
                          a.get("keyboard") and a["keyboard"].get("press") == a["keyboard"].get("release")]
        if family(key) == family(old["key"]) and len(actions) == len(original) and len(rendered_steps) == 1:
            target = rendered_steps[0]
            kind = "keyboard" if base_action.get("keyboard") else "mouse"
            if target.get(kind):
                actions = copy.deepcopy(original)
                for action in actions:
                    keyboard = action.get("keyboard") or {}
                    if core.normalize_key(keyboard.get("hotkey") or "")=="rctrl":
                        keyboard.update(hotkey_codes=[29],hotkey_extended=True)
                base = actions[segment["index"]-segment["start"]][kind]
                field = "hotkey" if kind == "keyboard" else "button"
                base[field] = target[kind][field]
                if hold != old["hold"]:
                    base["hold"] = hold
        probe = {"name":command.get("name"), "actions":actions}
        new_events = core.events(probe)
        if len(new_events) != 1:
            raise ValueError("reverse mapping event count")
        e = (new_events[0][0],new_events[0][1],old["camera"])
        got = {r["map"]+"."+r["name"] for r,b in core.matches(e,command,index,curated)}
        maps = {a.rsplit(".",1)[0] for a in intended}
        if not intended <= got or {a for a in got if a.rsplit(".",1)[0] in maps}-intended:
            raise ValueError("step verification failed")
        replacements.append((segment["start"],segment["end"],actions))
        changed.append({"event":i,"from":old["key"],"to":key})
    if not changed:
        raise ValueError("no missing step to repair")
    new = copy.deepcopy(command)
    for start,end,actions in reversed(replacements):
        new["actions"][start:end] = actions
    after = core.events(new)
    if len(after)!=len(recorded) or any(a[2]!=b["camera"] for a,b in zip(after,recorded)):
        raise ValueError("camera context changed")
    intended = {a for s in recorded for a in s["actions"]}
    got = set(core.resolved(new,index,curated))
    maps = {a.rsplit(".",1)[0] for a in intended}
    if not intended <= got or {a for a in got if a.rsplit(".",1)[0] in maps}-intended:
        raise ValueError("whole-command verification failed")
    return new, changed


def profile_root(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("unexpected XML declarations")
    root = ET.fromstring(text)
    if root.tag == "ActionMaps":
        if len(root) != 1 or root[0].tag != "ActionProfiles":
            raise ValueError("unexpected profile structure")
        root = root[0]
    if root.tag != "ActionProfiles" or any(a not in list(root) for a in root.iter("actionmap")):
        raise ValueError("unexpected profile structure")
    if len(list(root.iter("ActionProfiles"))) != 1:
        raise ValueError("unexpected nested profiles")
    maps = root.findall("actionmap")
    if len({a.get("name") for a in maps}) != len(maps):
        raise ValueError("duplicate actionmap")
    for amap in maps:
        actions = amap.findall("action")
        if any(c.tag != "action" for c in amap):
            raise ValueError("unexpected actionmap structure")
        if len({a.get("name") for a in actions}) != len(actions):
            raise ValueError("duplicate action")
        for action in actions:
            if any(c.tag != "rebind" for c in action):
                raise ValueError("unexpected action structure")
    return root


def insert_bind(text, row):
    """Insert at validated profile boundaries, without reserializing existing XML."""
    root = profile_root(text)
    q = quoteattr
    eol_match = re.search(r"\r\n|\n|\r", text)
    eol = eol_match[0] if eol_match else "\r\n"
    pads = re.findall(r"(?:^|\n)([ \t]+)<", text)
    # Indentation increments, not the absolute indentation of the nested profile.
    widths = sorted({len(p) for p in pads})
    unit = "\t" if any("\t" in p for p in pads) else " " * min(
        [b-a for a,b in zip([0]+widths, widths) if b>a], default=2)
    def block(tag, value=None, source=text):
        pattern = r"<"+tag+r"\b[^>]*(?:/>|>.*?</"+tag+r"\s*>)"
        found = [m for m in re.finditer(pattern, source, re.S)
                 if value is None or ET.fromstring(m[0]).get("name") == value]
        if len(found) != 1:
            raise ValueError("unrecognized XML block")
        return found[0]
    def append(source, match, child, tag):
        start, end = match.span()
        line_start = source.rfind("\n", 0, start)+1
        prefix = source[line_start:start]
        pad = prefix if not prefix.strip() else ""
        child_text = pad+unit+child.replace(eol, eol+pad+unit)
        original = match[0]
        if original.endswith("/>"):
            updated = original[:-2]+">"+eol+child_text+eol+pad+"</"+tag+">"
        else:
            pos = original.rfind("</"+tag)
            closing_start = original.rfind("\n", 0, pos)+1
            if not original[closing_start:pos].strip():
                pos = closing_start
            prefix = original[:pos]
            separator = "" if prefix.endswith(eol) else eol
            updated = prefix+separator+child_text+eol+original[pos:]
        return source[:start]+updated+source[end:]
    reb = "<rebind input="+q(row["input"])+"/>"
    amap = next((a for a in root.findall("actionmap") if a.get("name")==row["map"]), None)
    action = next((a for a in amap.findall("action") if a.get("name")==row["action"]), None) if amap is not None else None
    if amap is None:
        child = "<actionmap name="+q(row["map"])+">"+eol+unit+"<action name="+q(row["action"])+">"+eol+unit*2+reb+eol+unit+"</action>"+eol+"</actionmap>"
        return append(text, block("ActionProfiles"), child, "ActionProfiles")
    match = block("actionmap", row["map"])
    if action is None:
        child = "<action name="+q(row["action"])+">"+eol+unit+reb+eol+"</action>"
        return append(text, match, child, "actionmap")
    inner = block("action", row["action"], match[0])
    modified = append(match[0], inner, reb, "action")
    return text[:match.start()]+modified+text[match.end():]


def restore(live, generated, core, reader, enabled, process_check=game_running, closed=lambda:False, fresh_start=False):
    path = Path(generated)/"custom_binds.json"
    result = {"restored":[], "custom_updates":[], "deferred":[], "attention":[]}
    if not enabled or not path.is_file():
        return result
    custom = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(custom,list) or any(not isinstance(r,dict) or not all(isinstance(r.get(k),str) for k in ("map","action","input")) for r in custom):
        raise ValueError("invalid custom_binds.json")
    before = live.path.read_bytes()
    text = before.decode("utf-8-sig")
    root = profile_root(text)
    updates, pending = copy.deepcopy(custom), []
    present = False
    state_path = Path(generated)/"restore-state.json"
    build = live.raw.get("metadata", {}).get("buildId")
    def guard():
        if closed():
            raise ValueError("Skill closed; no further writes")
    def save_updates():
        if updates != custom:
            guard()
            backup(generated,"custom-binds.json",path.read_bytes(),closed)
            guard()
            atomic_json(path,updates,closed)
    defaults = {(r["map"],r["name"]):r for r in live.raw["actions"]}
    for i, row in enumerate(custom):
        slot = core._slot(row["input"])[:2]
        existing = [b.get("input","") for a in root.findall("actionmap") if a.get("name")==row["map"]
                    for act in a.findall("action") if act.get("name")==row["action"]
                    for b in act.findall("rebind") if core._slot(b.get("input",""))[:2]==slot]
        if fresh_start:
            if (row["map"],row["action"]) not in defaults:
                result["attention"].append(dict(kind="missing",map=row["map"],action=row["action"],
                                                reason="this action no longer exists in the game"))
            elif row.get("cleared") or any(not core._slot(value)[2] for value in existing):
                # Remember a newly observed blank so a later wipe cannot undo
                # this deliberate clear. Never adopt a moved/occupied input.
                if not row.get("cleared"):
                    value = next(v for v in existing if not core._slot(v)[2])
                    updates[i].update(input=value,cleared=True)
                    result["custom_updates"].append(dict(map=row["map"],action=row["action"],cleared=True))
                result["attention"].append(dict(kind="left",map=row["map"],action=row["action"],
                                                reason="you cleared this key; kept it unbound"))
            elif existing:
                if len(existing)!=1 or core.normalize_key(existing[0]) != core.normalize_key(row["input"]):
                    result["attention"].append(dict(kind="left",map=row["map"],action=row["action"],
                                                    reason="you changed this key; kept it as it is"))
            elif core._slot(row["input"])[2]:
                pending.append((i,row))
            continue
        if (row["map"],row["action"]) not in defaults:
            # Persist the diagnostic marker so removed game actions report once.
            if not row.get("missing_in_game"):
                updates[i]["missing_in_game"] = True
                result["attention"].append(row["action"]+": no longer in the game")
            continue
        updates[i].pop("missing_in_game",None)
        present = present or bool(existing)
        if any(not core._slot(value)[2] for value in existing):
            value = next(v for v in existing if not core._slot(v)[2])
            if not row.get("cleared") or row["input"] != value or row.get("removed"):
                updates[i].update(input=value,cleared=True)
                updates[i].pop("removed",None)
                result["custom_updates"].append({"map":row["map"],"action":row["action"],"cleared":True})
            continue
        if existing and len(existing)==1 and (
            row.get("cleared") or row.get("removed") or
            core.normalize_key(row["input"]) != core.normalize_key(existing[0])):
            base = defaults[(row["map"],row["action"])]
            default_inputs = {core.normalize_key(b["input"]) for d in ("keyboard","mouse")
                              for b in base.get(d,[]) if core._slot(b["input"])[:2]==slot}
            if not (row.get("cleared") or row.get("removed")) and core.normalize_key(existing[0]) in default_inputs:
                result["attention"].append("existing device slot: "+row["action"])
                continue
            updates[i].update(input=existing[0])
            updates[i].pop("cleared",None)
            updates[i].pop("removed",None)
            result["custom_updates"].append({"map":row["map"],"action":row["action"],"input":existing[0]})
        elif existing:
            if len(existing)>1:
                result["attention"].append("multiple custom inputs: "+row["action"])
        elif not row.get("cleared") and not row.get("removed") and core._slot(row["input"])[2]:
            pending.append((i,row))
    if pending and present and not fresh_start:
        for i,row in pending:
            updates[i]["removed"] = True
            result["custom_updates"].append({"map":row["map"],"action":row["action"],"removed":True})
        pending = []
    save_updates()
    pending = [row for i,row in pending]
    def conservative_check():
        try:
            return bool(process_check())
        except Exception:
            return True
    if pending:
        state = json.loads(state_path.read_text(encoding="utf-8")) if not fresh_start and state_path.exists() else {}
        if not fresh_start and (not build or state.get("buildId") == build):
            result["attention"].append("Automatic restore already attempted for this game build; review custom keys")
            return result
        if conservative_check():
            result["deferred"] = [r["map"]+"."+r["action"] for r in pending]
            return result
        for row in pending:
            text = insert_bind(text,row)
        verified = profile_root(text)
        for row in pending:
            if not any(b.get("input")==row["input"] for a in verified.findall("actionmap") if a.get("name")==row["map"]
                       for act in a.findall("action") if act.get("name")==row["action"] for b in act.findall("rebind")):
                raise ValueError("restore verification failed")
        data = (b"\xef\xbb\xbf" if before.startswith(b"\xef\xbb\xbf") else b"")+text.encode("utf-8")
        guard()
        backup(generated,"actionmaps.xml",before,closed)
        # Reserve the build before touching the profile, including failed readback.
        guard()
        if not fresh_start:
            atomic_json(state_path,dict(buildId=build,at=time.time(),count=len(pending)),closed)
        def write_check():
            guard()
            running = conservative_check()
            guard()
            return running
        guard()
        atomic_bytes(live.path,data,before,write_check,closed)
        if live.path.read_bytes()!=data:
            raise ValueError("profile readback failed")
        live.refresh()
        if live.warning:
            raise ValueError(live.warning)
        result["restored"] = [r["map"]+"."+r["action"] for r in pending]
    return result


def short_command(value, cap=32):
    """Keep whole words within a UTF-8 byte budget; never cut a command word."""
    words, size = [], 0
    for word in str(value).split():
        amount = len(word.encode("utf-8")) + bool(words)
        if size+amount > cap:
            break
        words.append(word)
        size += amount
    return " ".join(words) or "command"


def change_line(report, core):
    """Bounded model hint; full names/details remain in last-changes.json."""
    parts = []
    if report["changed"]:
        parts.append(report["cause"]+" moved "+str(len(report["changed"]))+" keys")
    removed = sum(bool(row.get("removed")) for row in report["custom_updates"] if isinstance(row,dict))
    if report["fixed"]:
        item = report["fixed"][0]
        detail = ""
        if not report["deferred"] and not removed and not report["restored"]:
            change = item["steps"][0]
            keys = core.key_speech(change["from"])+" to "+core.key_speech(change["to"])
            # Do not truncate a key name into a different key. Omit long chords.
            if len(keys.encode("utf-8")) <= 64:
                detail = " ("+short_command(item["name"])+": "+keys+")"
        parts.append("updated "+str(len(report["fixed"]))+" commands"+detail)
    if report["deferred"]:
        parts.append("close Star Citizen and restart Wingman to restore "+str(len(report["deferred"]))+" keys")
    else:
        if report["restored"]:
            parts.append("restored "+str(len(report["restored"]))+" keys")
        if removed:
            parts.append(str(removed)+" custom keys removed in your controls; left as they are")
        elif report["custom_updates"] and not report["fixed"]:
            parts.append("recorded "+str(len(report["custom_updates"]))+" user changes")
    if report["attention"]:
        parts.append(str(len(report["attention"]))+" need attention")
    return "Since last session: "+"; ".join(parts)+". Mention briefly once."


def log_line(report, core):
    """Human-visible diagnostic, with no instructions for the language model."""
    detail = change_line(report, core).removeprefix("Since last session: ")
    detail = detail.removesuffix(". Mention briefly once.")
    return "Keybindings: since last session "+detail+"."


def spoken_change(report, core):
    """At most 30 words, complete clauses, one human key detail, no raw inputs."""
    def counted(rows, noun):
        count = len(rows)
        return str(count)+" "+noun+("" if count == 1 else "s")
    parts = []
    if report["changed"]:
        moved = "since last time "+counted(report["changed"],"key")+" moved"
        if report["fixed"]:
            moved += ", so I updated "+counted(report["fixed"],"voice command")
        parts.append(moved)
    elif report["fixed"]:
        parts.append("I updated "+counted(report["fixed"],"voice command"))
    if report["restored"]:
        parts.append("your controls were reset, so I restored your "+counted(report["restored"],"custom key"))
    if report["deferred"]:
        parts.append("close Star Citizen and restart Wingman so I can restore your "+counted(report["deferred"],"custom key"))
    removed = sum(bool(r.get("removed")) for r in report["custom_updates"] if isinstance(r,dict))
    if removed:
        parts.append((str(removed)+" of your custom keys were" if removed != 1 else "1 of your custom keys was")+" removed in your controls; I left them as they are")
    if report["attention"]:
        parts.append(counted(report["attention"],"item")+(" needs" if len(report["attention"]) == 1 else " need")+" a look; details are in the Wingman log")
    if report["custom_updates"] and not any(report[k] for k in ("changed","fixed","restored","deferred","attention")) and not removed:
        parts.append("I recorded "+counted(report["custom_updates"],"change")+" to your custom keys")
    if not parts:
        return ""
    text = "By the way, "
    for part in parts:
        candidate = text+("; " if text != "By the way, " else "")+part
        if len(candidate.split()) > 30 or len(candidate) > 360:
            break  # Do not skip a higher-priority clause to speak a lower one.
        text = candidate
    if report["fixed"] and len(parts) == 1:
        item = report["fixed"][0]
        detail = short_command(item["name"])+" now uses "+core.key_speech(item["steps"][0]["to"])
        candidate = text+"; "+detail
        if len(candidate.split()) <= 30 and len(candidate) <= 360:
            text = candidate
    return text.rstrip(" ;")+"."


def fresh_version(metadata):
    """Prefer the player-facing branch version, not the four-part build id."""
    branch = re.search(r"(?:^|-)([0-9]+\.[0-9]+\.[0-9]+)(?:$|[-+])",
                       str(metadata.get("branch") or ""))
    build = re.match(r"([0-9]+\.[0-9]+)(?:\.|$)",str(metadata.get("version") or ""))
    return branch[1] if branch else build[1] if build else ""


def fresh_details(items):
    """The dialog and visible log share eight bounded, complete detail lines."""
    lines = []
    for item in items[:8]:
        text = " ".join(item.split())
        if len(text)>240:
            text = text[:239].rsplit(" ",1)[0]+"…"
        lines.append("- "+text)
    if len(items)>8:
        lines.append(f"- and {len(items)-8} more")
    return "\n".join(lines)


def fresh_report(raw, checked, repairs, restored, core, trigger):
    """Count-only speech; retain only unresolved final command attention."""
    rows = checked["commands"]
    bad = [row for row in rows if row["status"]!="OK"]
    prior = {row["name"]:row["reason"] for row in repairs.get("attention",[])}
    reasons = {
        "DRIFT":"its keys no longer reach the intended controls",
        "EDITED":"you changed this command since its last check",
        "UNAUDITED":"this command has not been checked against your intended controls",
    }
    friendly = {
        "no unchanged recorded step intent":"no matching saved key history; check this command",
        "no single supported input reaches every intended action":"no single key reaches all its intended controls",
        "ambiguous current bindings":"several keys could match; choose one yourself",
        "action removed":"an intended control no longer exists in the game",
        "Automatic command updates disabled":"automatic command updates are switched off",
    }
    attention = []
    for row in bad:
        reason = prior.get(row["name"])
        # Preserve useful repair detail without exposing parser terminology.
        reason = friendly.get(reason,reasons.get(row["status"],"this command needs a manual check"))
        attention.append(row["name"]+": "+reason)
    actions = {(r["map"],r["name"]):r for r in raw["actions"]}
    left = missing = 0
    for item in restored["attention"]:
        if not isinstance(item,dict):
            attention.append(str(item))
            continue
        left += item.get("kind")=="left"
        missing += item.get("kind")=="missing"
        action = actions.get((item.get("map"),item.get("action")),{})
        context = core.speakable_label(action.get("mapLabel") or item.get("map") or "Controls")
        label = core.speakable_label(action.get("label") or item.get("action") or "Key")
        attention.append(context+" / "+label+": "+item["reason"])
    for identity in restored["deferred"]:
        context,_,name = identity.rpartition(".")
        action = actions.get((context,name),{})
        context = core.speakable_label(action.get("mapLabel") or context)
        label = core.speakable_label(action.get("label") or name)
        attention.append(context+" / "+label+": close Star Citizen to put this key back")
    count, commands = len(raw["actions"]),len(rows)
    version = fresh_version(raw.get("metadata",{}))
    intro = f"Fresh start done: re-imported {count:,} keybindings"
    if version:
        intro += " from Star Citizen "+version
    parts = [intro]
    if commands:
        state = f"{len(bad)} {'needs' if len(bad)==1 else 'need'} a look" if bad else "all OK"
        parts.append(f"{commands} voice {'command' if commands==1 else 'commands'} checked, "+state)
    key = lambda n:"key" if n==1 else "keys"
    middle = []
    if restored["restored"]:
        n = len(restored["restored"])
        middle.append((f"put back {n} custom {key(n)}",f"put back {n} custom {key(n)}"))
    if left:
        middle.append((f"left {left} {key(left)} you changed as {'it is' if left==1 else 'they are'}",
                       f"left {left} changed {key(left)} alone"))
    if missing:
        middle.append((f"{missing} listed {key(missing)} no longer {'exists' if missing==1 else 'exist'} in the game",
                       f"{missing} listed {key(missing)} no longer {'exists' if missing==1 else 'exist'}"))
    tail = []
    if restored["deferred"]:
        again = "switch Fresh start off and on again" if trigger=="switch" else "say fresh start keybindings again"
        n = len(restored["deferred"])
        tail.append(f"Close Star Citizen and {again} to put back {n} {key(n)}")
    if attention:
        tail.append("Details are in the Wingman log")
    # Keep whole clauses and the actionable deferral/log instruction within the
    # speech budget. Less urgent counts remain in the shared visible details.
    words = lambda items:len(" ".join(items).split())
    if words(parts+tail)>40 and commands:
        parts[1] = f"{commands} commands: {len(bad)} {'needs' if len(bad)==1 else 'need'} a look"
    chosen = [full for full,short in middle]
    if words(parts+chosen+tail)>40:
        chosen = [short for full,short in middle]
    if words(parts+chosen+tail)>40:
        chosen = []
        for full,short in middle:
            if words(parts+chosen+[short]+tail)<=40:
                chosen.append(short)
    line = "; ".join(parts+chosen+tail)+"."
    details = fresh_details(attention)
    return dict(status="done",spoken=line,bindings=count,commands=commands,
                attention=attention,drift=len(bad),repaired=len(repairs.get("fixed",[])),
                restored=restored["restored"],deferred=restored["deferred"],
                left=left,missing=missing,details=details,
                log=line+("\n"+details if details else ""))
