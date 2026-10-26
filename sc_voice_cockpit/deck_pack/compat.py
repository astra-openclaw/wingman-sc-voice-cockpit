"""Pure compatibility primitives from SC_Keybindings 1.4.2 (MIT, Dominicus).

No vocabulary file reads; fingerprints intentionally match that release.
"""

import copy, json, re

import xml.etree.ElementTree as ET

DEVICES = ("keyboard", "mouse", "gamepad", "joystick")

PREFIX_DEVICE = {"kb": "keyboard", "mo": "mouse", "gp": "gamepad", "js": "joystick"}

KEY_ALIASES = {
    "right alt": "ralt", "alt gr": "ralt", "altgr": "ralt", "left alt": "lalt", "alt": "lalt",
    "right control": "rctrl", "right ctrl": "rctrl", "left control": "lctrl",
    "left ctrl": "lctrl", "control": "lctrl", "ctrl": "lctrl",
    "right shift": "rshift", "left shift": "lshift", "shift": "lshift",
    "left mouse button": "mouse1", "right mouse button": "mouse2",
    "middle mouse button": "mouse3", "left mouse": "mouse1",
    "right mouse": "mouse2", "middle mouse": "mouse3",
    "mouse wheel up": "mwheel_up", "mouse wheel down": "mwheel_down",
    "scroll up": "mwheel_up", "scroll down": "mwheel_down",
    "page up": "pgup", "page down": "pgdn", "enter": "return", "esc": "escape",
    "spacebar": "space", "space bar": "space", "caps lock":"capslock",
    "add":"np_add", "subtract":"np_subtract", "multiply":"np_multiply",
    "divide":"np_divide", "num enter":"np_enter", "decimal":"np_period",
    "delete":"delete", "insert":"insert",
    "numpad plus": "np_add", "numpad minus": "np_subtract", "numpad enter": "np_enter",
    "up arrow": "up", "down arrow": "down", "left arrow": "left", "right arrow": "right",
}

MODIFIERS = ("lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift")

def strip_device(value: str) -> str:
    return re.sub(r"^(?:kb|mo|gp|js)\d+_", "", value.strip().lower())

def normalize_key(value: str) -> str:
    """Accept SC notation or spoken key combinations; never drop modifier sides."""
    value = re.sub(r"^(?:what does|what is|which action uses)\s+", "", value.strip().lower())
    value = re.sub(r"\s+(?:do|does)\??$", "", value)
    value = re.sub(r"^(?:hold|tap|double[- ]tap)\s+", "", value)
    value = re.sub(r"\?+$", "", value)
    value = strip_device(value)
    value = re.sub(r"\b(?:numpad|num pad|num)\s*([0-9])\b", r"np_\1", value)
    for source, dest in sorted(KEY_ALIASES.items(), key=lambda x: -len(x[0])):
        value = re.sub(r"(?<!\w)" + re.escape(source) + r"(?!\w)", dest, value)
    value = re.sub(r"\b(?:plus|and|with)\b", "+", value)
    symbols = {",":"comma", ".":"period", "-":"minus", "=":"equals", ";":"semicolon", "\'":"apostrophe", "/":"slash", "\\":"backslash", "[":"lbracket", "]":"rbracket"}
    value = "+".join(symbols.get(p,p) for p in value.split("+"))
    parts = [p for p in re.split(r"\s*\+\s*|\s+", value.strip()) if p]
    # Canonical modifier ordering makes spoken and SC order equivalent.
    mods = [m for m in MODIFIERS if m in parts]
    rest = [p for p in parts if p not in MODIFIERS]
    return "+".join(mods + rest)

def _slot(value: str) -> tuple[str, int, str]:
    match = re.match(r"^(kb|mo|gp|js)(\d+)_(.*)$", value, re.I | re.S)
    if match:
        return PREFIX_DEVICE[match[1].lower()], int(match[2]), match[3].strip()
    raw = strip_device(value)
    device = "mouse" if re.search(r"mouse\d|m(?:wheel|axis)_", raw) else "keyboard"
    return device, 1, raw

def merge_overrides(index: dict, xml_text: str | None) -> list[dict]:
    """Replace only explicitly overridden device instances; blank input clears."""
    actions = copy.deepcopy(index["actions"])
    if not xml_text:
        return actions
    root = ET.fromstring(xml_text)
    by_id = {(a["map"], a["name"]): a for a in actions}
    for amap in root.iter("actionmap"):
        map_name = amap.get("name", "")
        for element in amap.findall("action"):
            name = element.get("name", "")
            rebinds = element.findall("rebind")
            if not rebinds:
                continue
            action = by_id.get((map_name, name))
            if action is None:
                action = {"map": map_name, "mapLabel": map_name, "name": name, "label": name,
                          "description": "", "activationMode": "", **{d: [] for d in DEVICES}}
                by_id[(map_name, name)] = action
                actions.append(action)
            replacements = {}
            for reb in rebinds:
                if reb.get("input") is None:
                    continue
                dev, instance, raw = _slot(reb.get("input"))
                replacements.setdefault((dev, instance), [])
                if raw:
                    inherited = next((b for b in action[dev] if _slot(
                        b.get("input", ""))[1] == instance), {})
                    binding = {**{k:v for k,v in inherited.items() if k != "input"}, **reb.attrib}
                    if "activationMode" in reb.attrib and reb.attrib["activationMode"] != inherited.get("activationMode"):
                        binding = dict(reb.attrib)  # Don't retain defaults from a different mode.
                    binding["input"] = reb.get("input").strip()
                    binding.setdefault("activationMode", action.get("activationMode", ""))
                    replacements[(dev, instance)].append(binding)
            for (dev, instance), binds in replacements.items():
                action[dev] = [b for b in action[dev] if _slot(b.get("input", ""))[1] != instance] + binds
    return actions

def events(c):
    held=[];result=[]
    for a in c.get("actions") or []:
        a = a or {}
        if a.get("keyboard"):
            k=a["keyboard"];tokens=normalize_key(k.get("hotkey") or "").split("+")
            if k.get("press")==k.get("release"):
                chord=normalize_key("+".join([x for x in held if x!="f4"]+tokens))
                result.append((chord,k.get("hold") or .1,"f4" in held))
            elif k.get("press"):held.extend(x for x in tokens if x not in held)
            else:held=[x for x in held if x not in tokens]
        elif a.get("mouse"):
            m=a["mouse"];button={"left":"mouse1","right":"mouse2","middle":"mouse3","x":"mouse4","x2":"mouse5"}.get(m.get("button") or "",m.get("button") or "")
            result.append((normalize_key("+".join(x for x in held if x!="f4")+"+"+button),m.get("hold"),"f4" in held))
    return result

def applies(bind,hold):
    mode=bind.get("activationMode") or ""
    if hold is None:return True
    release=float(bind.get("releaseTriggerThreshold",-1) or -1)
    press=float(bind.get("pressTriggerThreshold",-1) or -1)
    delay=float(bind.get("holdTriggerDelay",0) or 0)
    if mode.startswith("delayed_") and press>=0 and hold<press:return False
    if mode.startswith("tap") and release>0 and hold>release:return False
    return not(delay>0 and hold<delay)

def relevant(row,command,curated=False):
    if not curated:
        return row["map"] not in {"debug", "spectator", "flycam", "ui_textfield", "character_customizer"} and not row["map"].startswith(("server_", "debug_", "spectator_", "flycam_", "ui_textfield_", "character_customizer_"))
    m=row["map"];name=command.get("name") or ""
    if m=="player_emotes":return name.startswith("Emote ")
    if name.startswith(("Save View","View ")) or name=="Clear Saved View":return m=="view_director_mode"
    if name in {"Inventory","Loot","Open Inner Thought Menu"} or "Helmet" in name:return m.startswith("player")
    return m.startswith(("spaceship","vehicle_","seat_","turret_")) or m=="lights_controller"

def matches(event,command,index,curated=False):
    key,hold,camera=event
    return [(row,b) for row,b in index.command_inputs.get(key, [])
            if relevant(row,command,curated) and (row["map"]!="view_director_mode" or camera) and applies(b,hold)]

def command_fingerprint(command):
    import hashlib
    # Numbers compare by value: Wingman's model says 2.0 where a JSON round trip may say 2.
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    canonical = []
    for action in command.get("actions") or []:
        action = action or {}
        canonical.append({k: {n:num(v) for n,v in value.items() if v is not None}
                          if isinstance(value, dict) else num(value)
                          for k,value in action.items() if value is not None})
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def resolved(command, index, curated=False):
    return sorted({r["map"]+"."+r["name"] for event in events(command)
                   for r,b in matches(event, command, index, curated)})
