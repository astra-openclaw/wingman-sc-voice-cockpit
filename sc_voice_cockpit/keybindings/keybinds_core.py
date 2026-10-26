"""Pure stdlib SC index/override merge, ranking and natural-key formatting.

Device arrays preserve the game's source slots: SC sometimes stores mouse1
under keyboard. A kb1_ override replaces that keyboard slot, not the mouse slot.
All key matching/formatting recognizes mouse tokens in either slot.
"""
from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

DEVICES = ("keyboard", "mouse", "gamepad", "joystick")
PREFIX_DEVICE = {"kb": "keyboard", "mo": "mouse", "gp": "gamepad", "js": "joystick"}
KEY_NAMES = {
    "ralt": "Right Alt", "lalt": "Left Alt", "rctrl": "Right Control",
    "lctrl": "Left Control", "rshift": "Right Shift", "lshift": "Left Shift",
    "mouse1": "left mouse button", "mouse2": "right mouse button",
    "mouse3": "middle mouse button", "mouse4": "mouse button 4", "mouse5": "mouse button 5",
    "mwheel_up": "mouse wheel up", "mwheel_down": "mouse wheel down",
    "maxis_x": "mouse horizontal axis", "maxis_y": "mouse vertical axis",
    "maxis_z": "mouse wheel axis", "space": "Space", "escape": "Escape",
    "return": "Enter", "backspace": "Backspace", "tab": "Tab",
    "pgup": "Page Up", "pgdn": "Page Down", "capslock": "Caps Lock",
    "np_add": "numpad plus", "np_subtract": "numpad minus", "np_multiply": "numpad multiply",
    "np_divide": "numpad divide", "np_enter": "numpad Enter", "np_period": "numpad decimal",
    "comma": "comma", "period": "period", "minus": "minus", "equals": "equals",
    "semicolon": "semicolon", "apostrophe": "apostrophe", "slash": "slash",
    "backslash": "backslash", "lbracket": "left bracket", "rbracket": "right bracket",
}
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
STOPWORDS = {"what", "which", "is", "does", "do", "the", "a", "an", "to", "for", "of",
             "my", "your", "key", "keys", "button", "press", "please", "how", "i"}
QUERY_ALIASES = {
    "landing gear": "landing system", "gear": "landing system",
    "flight ready": "flight systems ready", "quantum jump": "engage quantum drive",
    "lock the doors": "lock all doors", "unlock the doors": "unlock all doors",
    "engine power": "thrusters power",
}
CONTEXTS = {
    "view_director_mode": "Advanced Camera – hold F4 modifier",
}


def is_key_question(text: str) -> bool:
    """A question about keys, not an order: "hold fire" and "press the landing gear" are orders."""
    if re.search(r"\b(?:keys?|keybind(?:ing)?s?|buttons?|bind(?:ing|ings)?|bound|shortcuts?)\b|\bwhat does\b.*\bdo\b|\bhow (?:do|can) I\b", text, re.I):
        return True
    return bool(re.search(r"\b(?:press|hold|tap|hit|push)\b", text, re.I)
                and re.search(r"\b(?:which|what|how|where|do I|should I|can I)\b|\?", text, re.I))


def capitalize(text):
    return text[:1].upper() + text[1:]


def extract_phrase(content: str, wingman_name: str = "") -> tuple[str, str]:
    """Trim transcript scaffolding, preserving the subject and reverse key chords."""
    phrase = content.strip()
    for _ in range(3):
        phrase = re.sub(r"^(?:(?:hey|okay|ok|please|could you|can you|tell me)\b[\s,]*)+", "", phrase, flags=re.I)
        if wingman_name:
            phrase = re.sub(r"^\s*" + re.escape(wingman_name) + r"\b[\s,!:.-]*", "", phrase, flags=re.I)
    reverse = re.fullmatch(r"what does\s+(.+?)\s+do[?!.]*", phrase, re.I)
    if reverse:
        return "", reverse[1].strip()
    phrase = re.sub(r"^(?:which|what)(?:'s| is)?\s+(?:the\s+)?keys?\b", "", phrase, flags=re.I).strip()
    phrase = re.sub(r"^(?:how do I|do I hold|do I press)\b", "", phrase, flags=re.I).strip()
    phrase = re.sub(r"^(?:is|for|the)\b[\s]*", "", phrase, flags=re.I).strip()
    phrase = re.sub(r"^(?:for|the)\b[\s]*", "", phrase, flags=re.I).strip()
    phrase = re.sub(r"^(?:fires|launches)\s+", "", phrase, flags=re.I)
    phrase = re.sub(r"^locks\s+", "lock ", phrase, flags=re.I)
    phrase = re.sub(r"[?!.]+$", "", phrase).strip()
    # "turn off the engines" and "turn the engines off" both mean "engines off"; keep the on/off word.
    phrase = re.sub(r"^(?:turns?|switch(?:es)?)\s+(on|off)\s+(?:the\s+)?(.+)$", r"\2 \1", phrase, flags=re.I)
    phrase = re.sub(r"^(?:turns?|switch(?:es)?)\s+(?:the\s+)?(.+?)\s+(on|off)$", r"\1 \2", phrase, flags=re.I)
    phrase = re.sub(r"[?!.]+$|[, ]*please$", "", phrase, flags=re.I).strip()
    return phrase, ""


def subject_kind(text: str) -> str:
    if re.search(r"\b(?:doors?|hatch(?:es)?|ramp[- ]doors?)\b", text, re.I):return "door"
    if re.search(r"\bports?\b|portlocks?", text, re.I):return "port"
    return ""


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


def key_speech(value: str) -> str:
    parts = normalize_key(value).split("+")
    def name(p):
        if p in KEY_NAMES:
            return KEY_NAMES[p]
        if re.fullmatch(r"np_\d", p):
            return "numpad " + p[-1]
        if len(p) == 1 or re.fullmatch(r"f\d{1,2}", p):
            return p.upper()
        return p.replace("_", " ")
    return " plus ".join(name(p) for p in parts if p)


def bind_speech(bind: dict, action: dict | None = None) -> str:
    mode = str(bind.get("activationMode") or (action or {}).get("activationMode") or "").lower()
    spoken = key_speech(bind.get("input", ""))
    try:
        taps = int(bind.get("multiTap", 1))
    except (ValueError, TypeError):
        taps = 1
    if taps > 1 or "double_tap" in mode:
        return ("double-tap " if taps <= 2 else f"tap {taps} times: ") + spoken
    # onHold alone is continuous input, not necessarily delayed activation.
    try:
        delayed_hold = str(bind.get("onHold", "0")) == "1" and float(bind.get("holdTriggerDelay", 0) or 0) > 0
    except (ValueError, TypeError):
        delayed_hold = False
    if delayed_hold or mode in {"hold", "hold_no_retrigger", "hold_toggle"} or mode.startswith("delayed_"):
        return "hold " + spoken
    if mode == "smart_toggle":
        return spoken + " (tap to toggle or hold)"
    if mode == "tap":
        return "tap " + spoken
    return spoken


def map_context(action: dict) -> str:
    return CONTEXTS.get(action["map"], action.get("mapLabel") or action["map"])


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


def _words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower().replace("_", " ")
    text = text.replace("thruster", "engine")
    result = []
    for word in re.findall(r"[a-z0-9]+", text):
        if word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        result.append(word)
    return result


def speakable_label(value, speech=True):
    value = re.sub(r"^(?:@?ui_|v_|pc_|pl_|eva_|spectate_)", "", value)
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value).replace("_", " ")
    modes = r"hold(?:\s*&\s*release)?|tap|toggle|short press|long press|abs\.?|rel\.?" if speech else r"hold|tap|toggle|short press|long press"
    value = re.sub(r"\((?:"+modes+r")\)", "", value, flags=re.I)
    value = re.sub(r"[\[\](){}<>]", "", value)
    return re.sub(r"\s+", " ", value.replace(" - ", " ")).strip(" ,.-") or "Unnamed action"


def speech_context(row):
    m = row["map"]
    for prefix, phrase in (("spaceship_quantum", "in quantum mode"),
            ("spaceship_mining", "in mining mode"), ("spaceship_salvage", "in salvage mode"),
            ("spaceship_missiles", "in missile mode"), ("spaceship_scanning", "in scanning mode"),
            ("turret", "in a turret"), ("view_director", "while holding F4"),
            ("player", "on foot"), ("zero_gravity", "in EVA")):
        if m.startswith(prefix): return phrase
    return ""


CLOSE_MARGIN = 4.0


class KeybindingIndex:
    def __init__(self, index: dict, overrides: str | None = None):
        if not isinstance(index.get("actions"), list) or not index["actions"]:
            raise ValueError("No keybinding actions in index")
        self.metadata = index.get("metadata", {})
        self.actions = merge_overrides(index, overrides)
        self.command_inputs = {}
        for row in self.actions:
            for device in ("keyboard", "mouse"):
                for binding in row[device]:
                    self.command_inputs.setdefault(normalize_key(binding["input"]), []).append((row,binding))
        self.vocabulary = json.loads(Path(__file__).with_name("vocabulary.json").read_text(encoding="utf-8"))
        self.stale_vocabulary = sorted({target for target in self.vocabulary.values()
            if target not in {a["map"]+"."+a["name"] for a in self.actions}})

    @classmethod
    def load(cls, path: Path, overrides: str | None = None):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")), overrides)

    def search(self, action: str = "", key: str = "", limit: int = 5) -> list[dict]:
        limit = min(5, max(0, limit))
        if key.strip():
            target = normalize_key(key)
            if not target:
                return []
            matches = []
            for row in self.actions:
                binds = [(dev, b) for dev in DEVICES for b in row[dev]
                         if normalize_key(b["input"]) == target]
                if binds:
                    score = self._context_rank(row)
                    # General weapon fire precedes operator-specific mouse actions.
                    if row["name"] == "v_weapon_preset_attack":score += 100
                    elif row["map"] == "spaceship_quantum":score += 90
                    matches.append((score, row, binds))
            matches.sort(key=lambda x: (-x[0], x[1]["map"], x[1]["name"]))
            return [self._result(row, binds, score) for score, row, binds in matches[:limit]]
        original = action.strip().lower()[:300]
        action, reverse_key = extract_phrase(action)
        if reverse_key:return self.search(key=reverse_key, limit=limit)
        query = action.strip().lower()[:300]
        query = QUERY_ALIASES.get(query, query)
        query = re.sub(r"\b(?:hatch(?:es)?|ramp[- ]doors?)\b", "doors", query)
        if re.search(r"\bpower up\b", query) and re.search(r"\b(?:shields?|engines?|weapons?)\b", query):
            query = re.sub(r"\bpower up\b", "power on", query)
        query = re.sub(r"\blanding\s+gear\b", "landing system", query)
        # Explicit gear down/up bindings outrank a toggle for directional questions.
        if re.search(r"\b(?:gear|landing system)\b", query):
            if re.search(r"\b(?:lowers?|lowering|deploys?|down|extend)\b", query):query = "landing system deploy"
            elif re.search(r"\b(?:raises?|raising|retracts?|up|stow)\b", query):query = "landing system retract"
        power = re.search(r"(?:full|maximum|max)\s+power\s+(?:to\s+)?(?:the\s+)?(engines?|thrusters?|shields?|weapons?)", query)
        if power:
            subsystem = re.sub(r"thrusters?", "engines", power[1])
            query = subsystem + " set max"
        # Vocabulary is written in natural wording: match it before and after normalisation.
        haystack = query + " | " + original
        alias_targets = {target for phrase, target in sorted(self.vocabulary.items(), key=lambda x: -len(x[0]))
                         if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", haystack)
                         and target not in self.stale_vocabulary}
        # Generic aliases may decorate the subject, never replace a named one.
        for phrase in ("power up", "stand up", "scan", "gimbal"):
            target = self.vocabulary.get(phrase)
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            # The phrase may survive only in the original wording ("power up the shields" is normalised to "power on").
            text = query if re.search(pattern, query) else original
            if target in alias_targets and re.search(pattern, text):
                remainder = re.sub(pattern, "", text)
                extra = set(_words(remainder)) - {"now", "ship", "seat", "weapon", "weapons"}
                specific = any(len(other)>len(phrase) and value==target and re.search(r"(?<!\w)"+re.escape(other)+r"(?!\w)", haystack) for other,value in self.vocabulary.items())
                if extra and not specific:alias_targets.discard(target)
        qw = _words(query)
        if not qw:
            return []
        ranked = []
        subject = subject_kind(query)
        for row in self.actions:
            row_subject = subject_kind(row["name"].replace("_", " ") + " " + row["label"])
            if subject and row_subject and subject != row_subject:continue
            label = " ".join(_words(speakable_label(row.get("label", ""), False)))
            desc = " ".join(_words(row.get("description", "")))
            name = " ".join(_words(row["name"]))
            context = " ".join(_words(row.get("mapLabel", "") + " " + row["map"]))
            words = set((label + " " + desc + " " + name + " " + context).split())
            # A missing semantic anchor must not turn "transponder" into a random key.
            coverage = sum(w in words for w in qw) / len(qw)
            near = max(difflib.SequenceMatcher(None, " ".join(qw), text).ratio()
                       for text in (label, desc, name) if text)
            is_alias = row["map"]+"."+row["name"] in alias_targets
            if not is_alias and coverage < .67 and near < .78:
                continue
            score = (150 if is_alias else 0) + coverage * 70 + near * 20 + self._context_rank(row) / 10
            if all(w in set(label.split()) for w in qw):
                score += 12
            if " ".join(qw) == label:
                score += 12
            # "Lock" must not prefer the opposite operation.
            if ("unlock" in qw and "unlock" not in words) or ("lock" in qw and "unlock" in words and "lock" not in words):
                score -= 30
            if "lock" in qw and "door" in qw and row["name"] == "v_lock_all_doors":
                score += 18
            binds = [(dev, b) for dev in DEVICES for b in row[dev]]
            if any(dev in {"keyboard", "mouse"} for dev, b in binds):
                score += 9
            if query == "shields":
                if row["name"] == "v_engineering_assignment_shields_max":score += 20
                elif row["name"] == "v_power_toggle_shields":score += 22.4
            if query == "lights" and row["name"] in {"v_lights", "v_light_amplification_toggle"}:
                score += 20
            if row["map"].startswith(("spectator", "debug", "server_")):
                score -= 60
            ranked.append((score, row, binds))
        ranked.sort(key=lambda x: (-x[0], x[1]["map"], x[1]["name"]))
        return [self._result(row, binds, score) for score, row, binds in ranked[:limit]]

    @staticmethod
    def _context_rank(row):
        name = row["map"]
        if name == "lights_controller":
            return 30
        if name.startswith("spaceship"):
            return 30
        if name in {"seat_general", "view_director_mode"}:
            return 25
        if name.startswith(("debug", "server_", "default", "spectator")):
            return -30
        if name.startswith("vehicle"):
            return 10
        return 15

    @staticmethod
    def _result(row, binds, score):
        return {"map": row["map"], "name": row["name"], "label": row["label"],
                "context": map_context(row), "description": row.get("description", ""),
                "score": round(score, 1),
                "bindings": [{"device": dev, "input": b["input"], "spoken": bind_speech(b, row),
                              "activationMode": b.get("activationMode", row.get("activationMode", "")),
                              **{k:b[k] for k in ("multiTap", "pressTriggerThreshold", "releaseTriggerThreshold") if k in b}}
                             for dev, b in binds]}

    def grouped(self, action="", key=""):
        groups = []
        seen = set()
        for row in self.search(action, key):
            primary = tuple(sorted(set(re.sub(r"^(?:tap|hold) ", "", b["spoken"]).replace(" (tap to toggle or hold)", "") for b in row["bindings"] if b["device"] in {"keyboard", "mouse"})))
            identity = (speakable_label(row["label"]).lower(), primary)
            if identity not in seen:
                seen.add(identity)
                groups.append(row)
        return groups

    @staticmethod
    def close_options(rows):
        if len(rows) < 2 or rows[0]["score"]-rows[1]["score"] > CLOSE_MARGIN:
            return []
        return [r for r in rows[:3] if rows[0]["score"]-r["score"] <= CLOSE_MARGIN]

    @staticmethod
    def row_speech(row):
        label = " ".join(speakable_label(row["label"]).split()[:14])
        keys = [b["spoken"] for b in row["bindings"] if b["device"] in {"keyboard", "mouse"}]
        if not keys:
            return f"{label} isn't bound on keyboard or mouse; look under {speakable_label(row['context'])} in the keybinding menu."
        key = keys[0].replace(" (tap to toggle or hold)", "")
        context = speech_context(row)
        return capitalize(f"{key} for {label}" + (f" {context}" if context else "") + ".")

    def spoken(self, action="", key=""):
        if not key:action, key = extract_phrase(action)
        rows = self.grouped(action, key)
        if not rows:
            query = " ".join(speakable_label(action or key).split()[:10])
            return f"I can't find a binding for {query} in your controls; I won't guess."
        if key:
            chord = key_speech(key)
            if len(rows) == 1:
                return capitalize(f"{chord} is {speakable_label(rows[0]['label'])}.")
            phrases = []
            for row in rows[:3]:
                bind = next((b for b in row["bindings"] if b["device"] in {"keyboard", "mouse"}), None)
                mode = bind["spoken"].replace(chord, "").strip() if bind else ""
                label = " ".join(speakable_label(row["label"]).split()[:7])
                context = speech_context(row)
                phrases.append((mode + " for " if mode else "") + label + (f" {context}" if context else ""))
            return capitalize(chord + ": " + "; ".join(phrases) + ".")
        options = self.close_options(rows)
        if options:return self.choice(options)
        if rows[0]["name"] == "v_weapon_countermeasure_decoy_launch":
            primary = [b for b in rows[0]["bindings"] if b["device"] in {"keyboard", "mouse"}]
            if primary and not primary[0]["activationMode"] and "(tap)" in rows[0]["label"].lower() and "(hold)" in rows[0]["label"].lower():
                chord = key_speech(primary[0]["input"])
                return f"Tap {chord} for a decoy burst; hold {chord} to set and launch a burst."
        line = self.row_speech(rows[0])
        if not any(b["device"] in {"keyboard", "mouse"} for b in rows[0]["bindings"]):
            nouns = set(_words(rows[0]["label"])) - {"power", "set", "on", "off", "all", "toggle", "open", "close", "lock", "unlock"}
            candidates = rows[1:] + self.grouped(" ".join(sorted(nouns)))
            bound = [r for r in candidates if any(b["device"] in {"keyboard", "mouse"} for b in r["bindings"]) and nouns & set(_words(r["label"]))]
            shared = set(_words(rows[0]["label"])) - {"set", "on", "off", "toggle"}
            alternative = max(bound, key=lambda r: (len(shared & set(_words(r["label"]))), r["score"]), default=None)
            if alternative:
                label = speakable_label(rows[0]["label"])
                extra = self.row_speech(alternative)
                alt_label = speakable_label(alternative["label"])
                if alt_label.startswith("Toggle Power "):
                    bind = next(b for b in alternative["bindings"] if b["device"] in {"keyboard", "mouse"})
                    noun = alt_label.removeprefix("Toggle Power ").lower()
                    if noun == "shields":noun = "shield"
                    extra = f"{bind['spoken']} toggles {noun} power."
                line = f"{label} isn't bound; " + extra
        return line

    @staticmethod
    def choice(rows, best_score=None):
        rows = [r for r in rows[:3] if best_score is None or best_score-r["score"] <= CLOSE_MARGIN]
        if len(rows) == 1:return KeybindingIndex.row_speech(rows[0])
        if rows and all(not any(b["device"] in {"keyboard", "mouse"} for b in r["bindings"]) for r in rows):
            labels = " and ".join(" ".join(speakable_label(r["label"]).split()[:6]) for r in rows)
            return f"{labels} aren't bound on keyboard or mouse; look under {speakable_label(rows[0]['context'])} in the keybinding menu."
        choices = []
        for row in rows:
            keys = [b["spoken"] for b in row["bindings"] if b["device"] in {"keyboard", "mouse"}]
            label = " ".join(speakable_label(row["label"]).split()[:6])
            choices.append(keys[0].replace(" (tap to toggle or hold)", "") + " for " + label if keys else label + " (not bound)")
        return "Do you mean " + ", or ".join(choices) + "?"

    def answer(self, action: str = "", key: str = "", warning: str = "") -> str:
        if not key:action, key = extract_phrase(action)
        results = self.search(action, key)
        lines = [warning.strip()[:240]] if warning else []
        if not action.strip() and not key.strip():
            lines.append("Provide an action or a key. Lookup only; no keys are pressed.")
        elif not results:
            lines.append("No matching action or binding found in the extracted game defaults plus your overrides. Do not invent a key.")
        else:
            for i, row in enumerate(results, 1):
                # Keyboard/mouse are primary. Explicitly identify secondary devices.
                primary = [b for b in row["bindings"] if b["device"] in {"keyboard", "mouse"}]
                values = list(dict.fromkeys(b["spoken"] for b in primary))
                keys = "; ".join(values) if values else "not bound on keyboard/mouse"
                secondary = [b for b in row["bindings"] if b["device"] in {"gamepad", "joystick"}]
                if not values and secondary:
                    keys += "; " + "; ".join(f'{b["device"]}: {b["spoken"]}' for b in secondary[:2])
                line = f'{i}. {row["label"][:95]} — {keys[:140]} [{row["context"][:85]}]'
                if len("\n".join(lines + [line])) > 1160:
                    lines.append("More matches omitted; narrow the action or map.")
                    break
                lines.append(line)
        return "\n".join(lines)[:1200]


class LiveBindings:
    """Refresh user overrides by mtime/size; preserve last good data on parse failure."""
    def __init__(self, index: dict, override_path: Path):
        self.raw = index
        self.path = Path(override_path)
        self.fingerprint = object()
        self.engine = KeybindingIndex(index)
        self.warning = ""

    def refresh(self):
        try:
            try:
                stat = self.path.stat()
                fingerprint = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                fingerprint = None
            if fingerprint == self.fingerprint:
                return
            text = self.path.read_text(encoding="utf-8-sig") if fingerprint is not None else None
            engine = KeybindingIndex(self.raw, text)
            self.engine, self.fingerprint, self.warning = engine, fingerprint, ""
        except (OSError, ValueError, ET.ParseError) as error:
            self.warning = "Overrides could not be read; last good bindings may be outdated: " + str(error)[:120]

    def answer(self, action="", key="", warning=""):
        self.refresh()
        return self.engine.answer(action, key, " ".join(filter(None, [warning, self.warning])))


# Read-only command audit rules, shared with manifest generator.
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


def command_dict(command):
    return command if isinstance(command, dict) else command.model_dump(mode="json")


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


def check_commands(index, commands, manifest):
    import time
    start = time.perf_counter()
    entries = {e["name"]:e for e in manifest["commands"]}
    rows = []
    for obj in commands or []:
        command = command_dict(obj)
        if not any(a.get("keyboard") or a.get("mouse") for a in command.get("actions") or [] if a):continue
        entry = entries.get(command.get("name") or "")
        row = {"name":command.get("name") or "", "status":"UNAUDITED"}
        if entry:
            edited = command_fingerprint(command) != entry["fingerprint"]
            reached = resolved(command, index, curated=True)
            expected = set(entry["expectedActions"])
            missing = expected - set(reached)
            maps = {ident.rsplit(".",1)[0] for ident in expected}
            new = {ident for ident in reached if ident.rsplit(".",1)[0] in maps} - set(entry["baselineActions"])
            certain = bool(missing or new) and entry["verifiable"]
            row.update(status="DRIFT" if certain else "EDITED" if edited else "OK",
                       edited=edited, reached=reached, missing=sorted(missing), unexpected=sorted(new),
                       verified=bool(entry["verifiable"] and not (entry.get("emote") and (missing or new or edited))))
        rows.append(row)
    return {"commands":rows,"milliseconds":round((time.perf_counter()-start)*1000,3)}


def drift_prompt(report):
    drift = [r for r in report.get("commands",[]) if r["status"]=="DRIFT"]
    if not drift:return ""
    def short_name(value):
        # A UTF-8 byte cap also bounds token cost for non-English/custom names.
        name = " ".join(str(value).split())
        raw = name.encode("utf-8")
        return raw[:20].decode("utf-8", errors="ignore") + ("..." if len(raw)>20 else "")
    names = "; ".join(short_name(r["name"]) for r in drift[:3])
    more = f" and {len(drift)-3} more" if len(drift)>3 else ""
    return f"Keybinding drift: don't use {names}{more}; keys no longer match the audit. Tell the user."


def check_baseline(index, commands, baseline=None):
    """Unedited commands drift only when an originally reached action is lost.

    Edited/new commands acquire their current resolution; removed commands are
    dropped. Extras in any map do not invalidate a surviving baseline action.
    """
    import time
    start = time.perf_counter()
    previous = {e["name"]:e for e in (baseline or {}).get("commands",[])}
    rows, entries = [], []
    for obj in commands or []:
        command = command_dict(obj)
        if not any(a and (a.get("keyboard") or a.get("mouse")) for a in command.get("actions") or []):
            continue
        name = command.get("name") or ""
        fingerprint = command_fingerprint(command)
        reached = resolved(command,index)
        old = previous.get(name)
        edited = bool(old and old["fingerprint"] != fingerprint)
        if old is None or edited:
            old = dict(name=name,fingerprint=fingerprint,resolved=reached)
        missing = sorted(set(old["resolved"])-set(reached))
        entries.append(old)
        rows.append(dict(name=name,status="DRIFT" if missing else "OK",edited=edited,
                         reached=reached,missing=missing,verified=bool(old["resolved"])))
    return ({"mode":"baseline","commands":rows,"milliseconds":round((time.perf_counter()-start)*1000,3)},
            {"version":1,"commands":entries})
