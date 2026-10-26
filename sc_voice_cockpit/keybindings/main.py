"""Wingman Skill API v3 adapter. No file, process, or network work at import."""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import re

from skills.skill_base import Skill, tool, command_action


def _valid_game(folder: Path) -> bool:
    return (folder / "Data.p4k").is_file() and (folder / "build_manifest.id").is_file()


def locate_game(setting: str) -> Path:
    """Explicit folder, whitelisted launcher settings, then bounded drive search."""
    if setting and setting.strip().lower() != "auto":
        folder = Path(os.path.expandvars(setting.strip().strip('"')))
        if not _valid_game(folder):
            raise FileNotFoundError("Configured LIVE folder has no Data.p4k/build_manifest.id")
        return folder
    appdata = Path(os.environ.get("APPDATA", ""))
    # Read only known library-location fields. Never enumerate credentials.
    for name in ("settings.json", "config.json", "Config/settings.json"):
        try:
            data = json.loads((appdata / "rsilauncher" / name).read_text(encoding="utf-8-sig"))
            sources = [data]
            for key in ("settings", "library", "gameSettings"):
                if isinstance(data.get(key), dict):
                    sources.append(data[key])
            for source in sources:
                for key in ("libraryFolder", "libraryPath", "gamePath", "installDir", "installPath"):
                    value = source.get(key)
                    if isinstance(value, str):
                        base = Path(os.path.expandvars(value))
                        for relative in ("", "StarCitizen/LIVE", "LIVE"):
                            candidate = base / relative
                            if _valid_game(candidate):
                                return candidate
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    # Known shallow locations on each drive, then directory-only, depth/node bounded.
    # Never recurse into system/profile directories or symlinks/junctions.
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
        drive = Path(letter + ":\\")
        if not drive.is_dir():
            continue
        for relative in ("Games/Roberts Space Industries/StarCitizen/LIVE",
                         "Roberts Space Industries/StarCitizen/LIVE",
                         "Games/StarCitizen/LIVE", "StarCitizen/LIVE",
                         "Program Files/Roberts Space Industries/StarCitizen/LIVE"):
            candidate = drive / relative
            if _valid_game(candidate):
                return candidate
        stack, visited = [(drive, 0)], 0
        while stack and visited < 1500:
            folder, depth = stack.pop()
            visited += 1
            if folder.name.lower() == "starcitizen" and _valid_game(folder / "LIVE"):
                return folder / "LIVE"
            if depth >= 4:
                continue
            try:
                with os.scandir(folder) as children:
                    for entry in children:
                        if entry.name.lower() in {"windows", "users", "$recycle.bin",
                                "system volume information", "programdata", "node_modules",
                                "appdata", ".git"}:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            # isjunction exists on Python 3.12+, but Core is 3.11.
                            # Windows junctions expose FILE_ATTRIBUTE_REPARSE_POINT.
                            stat = entry.stat(follow_symlinks=False)
                            if getattr(stat, "st_file_attributes", 0) & 0x400:
                                continue
                            stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    raise FileNotFoundError("Star Citizen LIVE not found; set the game_folder property")


def read_build(game: Path) -> dict:
    raw = json.loads((game / "build_manifest.id").read_text(encoding="utf-8-sig"))
    build = raw.get("Data", raw)
    if not build.get("BuildId"):
        raise ValueError("Build manifest has no BuildId")
    return build


def _sibling_module(name):
    # Custom Core import probes load main.py as bare "main", not a package.
    # Load the sibling only during prepare/lookup, without global sys.path edits.
    filename = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("_sc_voice_cockpit_" + name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SC_Keybindings(Skill):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()
        self._bindings = None
        self._game = None
        self._index_path = None
        self._core = None
        self._thread = None
        self._refreshing = False
        self._error = ""
        self._stale = False
        self._setting = None
        self._attempted_build = None
        self._closed = False
        self._guard_lock = threading.Lock()
        self._guard = None
        self._user_message = None
        self._last_choice = None
        self._subscriptions = []
        self._endearment_index = 0
        self._drift = {}
        self._vocabulary_logged = set()
        self._loop = None
        self._change_note = None
        self._process_check = None  # Injectable read-only process probe.
        self._drift_lock = threading.RLock()
        self._healing = False
        self._work_lock = threading.RLock()
        self._fresh_lock = threading.Lock()
        self._fresh_owner = None
        self._fresh_phase = None
        self._fresh_heal_result = None
        self._fresh_task = None
        self._fresh_wait_seconds = 150
        self._fresh_command_seconds = 120
        self._fresh_import_dir = None
        self._save_retry_seconds = 2
        self._save_timeout_seconds = 120


    async def validate(self):
        errors = await super().validate()
        self.retrieve_custom_property_value("game_folder", errors)
        self.retrieve_custom_property_value("endearments", errors)
        return errors

    def _property(self):
        try:
            return str(self.retrieve_custom_property_value("game_folder", []) or "auto")
        except (ValueError, KeyError):
            return "auto"

    def _optional(self, name, default):
        # Wingman overrides replace the entire list; missing new ids are valid.
        prop = next((p for p in (getattr(self.config, "custom_properties", None) or [])
                     if p.id == name), None)
        return bool(prop.value) if prop is not None and prop.value is not None else default

    async def prepare(self):
        self._loop = asyncio.get_running_loop()
        with self._lock:self._change_note = None
        self._subscriptions = [self.wingman.audio.on_playback_started(self._playback_started),
                               self.wingman.audio.on_playback_finished(self._playback_finished)]
        if await self._fresh_switch_ready():
            self._launch_fresh_switch()
        else:
            self._start_refresh(self._property())

    @staticmethod
    def _normalized(text):
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    def _arm(self, spoken):
        with self._guard_lock:
            self._guard = {"text":self._normalized(spoken), "time":time.monotonic(),
                           "accepted":False, "playing":False}

    def _guard_current(self):
        if self._guard and time.monotonic()-self._guard["time"] >= 20:
            self._guard = None
        return self._guard

    async def on_play_to_user(self, text, sound_config):
        skipped = False
        with self._guard_lock:
            guard = self._guard_current()
            if guard:
                if (self._normalized(text)==guard["text"] or
                    not guard["accepted"] and time.monotonic()-guard["time"] <= 1):
                    guard["accepted"] = True
                    self._user_message = None
                else:
                    skipped = True
        if skipped:
            self.log.info("A line was skipped during a keybinding answer", server_only=True)
            return "{SKIP-TTS}"
        return text

    async def _playback_started(self, wingman_name):
        if wingman_name != self.wingman.name:return
        with self._guard_lock:
            guard = self._guard_current()
            if guard and guard["accepted"]:guard["playing"] = True

    async def _playback_finished(self, wingman_name):
        if wingman_name != self.wingman.name:return
        with self._guard_lock:
            guard = self._guard_current()
            if guard and guard["playing"]:self._guard = None

    async def on_add_user_message(self, message):
        # 3.2.11 ConversationManager passes plain content before LLM/tool rewriting.
        with self._guard_lock:
            self._guard = None
            self._user_message = (message, time.monotonic()) if isinstance(message, str) else None

    async def on_add_assistant_message(self, message, tool_calls):
        # Tool-call planning is not an answer; several lookups may share this turn.
        if message and not tool_calls:
            with self._guard_lock:self._user_message = None
            with self._lock:self._change_note = None

    def _select_query(self, action, key):
        with self._guard_lock:
            current = self._user_message
            if not current or not 0 <= time.monotonic()-current[1] < 120 or not self._core.is_key_question(current[0]):
                return action, key, None
        core = self._core
        user_action, user_key = core.extract_phrase(current[0], self.wingman.name)
        if not user_action and not user_key:return action, key, None
        engine = self._bindings.engine
        user_rows = engine.grouped(user_action, user_key)
        model_rows = engine.grouped(action, key)
        # A door/port rewrite cannot cross the transcript's explicit subject.
        subject = core.subject_kind(user_action)
        if subject:
            model_rows = [r for r in model_rows if core.subject_kind(r["label"]) != ({"door":"port", "port":"door"}[subject])]
        commands = self.wingman.commands.all()
        weak = any(((c.get("name") if isinstance(c, dict) else c.name) or "").casefold() == action.strip().casefold() for c in commands)
        def identity(row):
            return (core.speakable_label(row["label"]).lower(), tuple(sorted({b["spoken"] for b in row["bindings"] if b["device"] in {"keyboard","mouse"}})))
        def confident(rows):
            return bool(rows) and (len(rows)==1 or rows[0]["score"]-rows[1]["score"] > core.CLOSE_MARGIN)
        if user_rows and model_rows and identity(user_rows[0]) == identity(model_rows[0]):
            return user_action, user_key, None
        if confident(user_rows):return user_action, user_key, None
        if not user_rows and confident(model_rows) and not weak:return action, key, None
        if not user_rows and not model_rows:return user_action, user_key, None
        # Preserve broad original questions (e.g. shields) instead of narrowing to
        # the model's command name. On weak/conflicting evidence expose options.
        rows = []; seen = set()
        def shortlist(rows):
            return [r for r in rows[:3] if rows[0]["score"]-r["score"] <= core.CLOSE_MARGIN]
        for row in shortlist(user_rows) + shortlist(model_rows):
            ident = identity(row)
            if ident not in seen:
                seen.add(ident); rows.append(row)
        if not rows:return user_action, user_key, None
        return user_action, user_key, rows[:3]

    def _pending_change(self):
        # Call only under _lock. Prompt and spoken note share one expiry/consume.
        if self._change_note and 0 <= time.monotonic()-self._change_note["at"] < 600:
            return self._change_note
        self._change_note = None
        return None

    @staticmethod
    def _cap_spoken(text, limit):
        if len(text) <= limit:
            return text
        prefix = text[:limit-1]
        return (prefix.rsplit(" ",1)[0] if " " in prefix else prefix).rstrip(" ,;")+"…"

    async def get_prompt(self):
        base = self.config.prompt or ""
        with self._lock:
            extra = self._core.drift_prompt(self._drift) if self._core else ""
            note = self._pending_change()
            if note:
                extra += ("\n" if extra else "") + note["prompt"]
        with self._guard_lock:
            current = self._user_message
            nudge = bool(current and 0 <= time.monotonic()-current[1] < 120 and
                         self._core and self._core.is_key_question(current[0]))
        if nudge:
            extra += ("\n" if extra else "") + "The user is asking about a key now: call look_up_keybinding with their words first; earlier answers may be outdated."
        return base + ("\n" + extra if extra else "")

    def _write_heal_json(self, path, value):
        if self._closed:
            raise ValueError("Skill closed; no further writes")
        _sibling_module("self_heal").atomic_json(path, value, lambda:self._closed)

    def _check_drift(self, live):
        with self._drift_lock:
            if self._closed:
                return None
            return self._check_drift_locked(live)

    def _check_drift_locked(self, live):
        # Called only on background extraction/lookup-refresh threads, never the event loop.
        try:
            generated = Path(self.get_generated_files_dir())
            manifest_path = generated / "command_manifest.json"
            reader = _sibling_module("p4k_reader")
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                report = self._core.check_commands(live.engine, self.wingman.commands.all(), manifest)
                report["mode"] = "curated"
            else:
                baseline_path = generated / "command_baseline.json"
                baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig")) if baseline_path.exists() else None
                report, updated = self._core.check_baseline(live.engine, self.wingman.commands.all(), baseline)
                self._write_heal_json(baseline_path, updated)
            healer = _sibling_module("self_heal")
            step_path = generated / "command_steps.json"
            previous = json.loads(step_path.read_text(encoding="utf-8")) if step_path.exists() else {}
            status = {r["name"]:r["status"] for r in report["commands"]}
            recorded = {}
            for obj in self.wingman.commands.all():
                command = self._core.command_dict(obj)
                name = command.get("name") or ""
                if command.get("is_system_command"):
                    continue
                if status.get(name) == "OK":
                    recorded[name] = healer.steps(command, live.engine, self._core, report["mode"]=="curated")
                elif name in previous:
                    recorded[name] = previous[name]
            self._write_heal_json(step_path, recorded)
            self._write_heal_json(generated / "drift-report.json", report)
            with self._lock:self._drift = report
            warning = self._core.drift_prompt(report)

            self.log.info(f"Command drift check: {sum(r['status']=='DRIFT' for r in report['commands'])} DRIFT; {report['milliseconds']} ms", server_only=True)
            for entry in live.engine.stale_vocabulary:
                if entry not in self._vocabulary_logged:
                    self._vocabulary_logged.add(entry)
                    self.log.warning("vocabulary entry stale after patch: " + entry, server_only=True)
            self._log_unverifiable(report["commands"])
            return report
        except Exception as error:
            self.log.warning("Command drift check unavailable: " + str(error)[:120], server_only=True)

    def _log_unverifiable(self, commands):
        names = [row["name"] for row in commands if not row.get("verified", True)]
        if names:
            sample = ", ".join(names[:3]) + (", …" if len(names) > 3 else "")
            self.log.info(f"Can't verify {len(names)} commands: {sample}", server_only=True)

    async def _apply_repairs(self, plans):
        # This is the ONLY command mutation boundary; the refresh worker hands off here.
        if asyncio.get_running_loop() is not self._loop or self._closed:
            raise ValueError("Wingman loop unavailable")
        live = self.wingman.commands.all()
        by_name = {}
        for obj in live:
            name = self._core.command_dict(obj).get("name")
            if name in by_name:
                raise ValueError("duplicate command name")
            by_name[name] = obj
        changes = []
        for old,new in plans:
            obj = by_name.get(old["name"])
            if obj is None or self._core.command_dict(obj) != old or old.get("is_system_command"):
                raise ValueError("Command changed during repair; deferred")
            if isinstance(obj,dict):
                actions = new["actions"]
            else:
                existing = next((a for a in obj.actions or [] if a is not None), None)
                if existing is None:
                    raise ValueError("No action model class")
                actions = [type(existing).model_validate(a) for a in new["actions"]]
            changes.append((obj, obj["actions"] if isinstance(obj,dict) else obj.actions, actions))
        try:
            for obj,old,actions in changes:
                if isinstance(obj,dict):obj["actions"] = actions
                else:obj.actions = actions
            deadline = time.monotonic()+self._save_timeout_seconds
            while True:
                if self._closed:
                    raise ValueError("Skill closed; command repair rolled back")
                remaining = max(.001, deadline-time.monotonic())
                if await asyncio.wait_for(self.wingman.commands.save(), timeout=remaining):
                    break
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise ValueError("Command save failed after startup registration retry")
                await asyncio.sleep(min(self._save_retry_seconds, remaining))
        except BaseException:
            for obj,old,actions in changes:
                if isinstance(obj,dict):obj["actions"] = old
                else:obj.actions = old
            raise

    def _startup_heal(self, live):
        """Synchronous worker entry point, also used by offline acceptance."""
        with self._work_lock:
            return self._startup_heal_locked(live)

    def _startup_heal_locked(self, live):
        if self._closed or self._stale or self._error or live.warning:
            return
        try:
            healer = _sibling_module("self_heal")
            generated = Path(self.get_generated_files_dir())
            with healer.startup_lock(generated, lambda:self._closed,
                                     reclaim=self._fresh_owner != threading.get_ident()) as acquired:
                if not acquired:
                    self.log.info("Startup heal already active; skipped", server_only=True)
                    return
                with self._drift_lock:
                    self._healing = True
                    try:
                        self._heal(live)
                    except Exception as error:
                        # Diagnostic writes share the same lease and drift lock.
                        self.log.warning("Startup heal unavailable: "+str(error)[:160], server_only=True)
                        if not self._closed:
                            current = healer.snapshot(live.engine)
                            path = generated/"bindings_snapshot.json"
                            try:
                                previous = json.loads(path.read_text(encoding="utf-8"))
                            except (OSError, ValueError):
                                previous = None
                            result = dict(changed=healer.diff(previous,current), cause="your controls",
                                          fixed=[],restored=[],custom_updates=[],deferred=[],
                                          attention=[dict(name="Startup heal",reason=str(error)[:160])])
                            self._finish_heal(generated, result, current)
                    finally:
                        self._healing = False
        except Exception as error:
            # A lock/filesystem failure may also prevent its attention report.
            # Do not write without the lease and never poison a healthy index.
            self.log.warning("Cannot complete startup attention report: "+str(error)[:160], server_only=True)

    def _finish_heal(self, generated, result, snapshot):
        if self._closed:
            return
        if self._fresh_owner == threading.get_ident():
            self._fresh_heal_result = result
            return  # Fresh start owns its single report, not a session note.
        healer = _sibling_module("self_heal")
        path = generated/"last-changes.json"
        try:
            last = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            last = {}
        notify = (any(result[k] for k in ("changed","fixed","restored","custom_updates"))
                  or result["attention"] != last.get("attention",[])
                  or result["deferred"] != last.get("deferred",[]))
        self._write_heal_json(path,result)
        if notify and any(result[k] for k in ("changed","fixed","restored","custom_updates","deferred","attention")):
            line = healer.change_line(result, self._core)
            self.log.warning(healer.log_line(result, self._core))
            with self._lock:
                self._change_note = dict(prompt=line,spoken=healer.spoken_change(result,self._core),
                                         at=time.monotonic())
        self._write_heal_json(generated/"bindings_snapshot.json", snapshot)

    def _heal(self, live):
        # Fresh successful index/profile only. All disk/parse work stays on this worker.
        if self._closed or self._stale or self._error or live.warning:
            return
        healer, reader = _sibling_module("self_heal"), _sibling_module("p4k_reader")
        generated = Path(self.get_generated_files_dir())
        def read(name, default):
            path = generated / name
            return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default
        previous = read("bindings_snapshot.json", None)
        current = healer.snapshot(live.engine)
        result = dict(changed=healer.diff(previous,current),
                      cause="game update" if previous and previous["buildId"]!=current["buildId"] else "your controls",
                      fixed=[],restored=[],custom_updates=[],deferred=[],attention=[])
        try:
            restored = {} if self._fresh_owner == threading.get_ident() else healer.restore(live,generated,self._core,reader,
                                      self._optional("restore_custom_binds",False),
                                      self._process_check or healer.game_running, lambda:self._closed)
            result.update(restored)
        except Exception as error:
            result["attention"].append(dict(name="Custom keys", reason=str(error)[:160]))
            self._finish_heal(generated,result,current)
            return  # Accept diagnostic snapshot, never rewrite on restore/profile failure.
        # Normalize restoration diagnostics for the JSON and prompt.
        result["attention"] = [r if isinstance(r,dict) else dict(name="Custom keys",reason=r) for r in result["attention"]]
        report = self._check_drift(live)
        if report is None:
            raise ValueError("Drift check failed; no repairs applied")
        entries = read("command_steps.json",{})
        drift = [r for r in report["commands"] if r["status"]=="DRIFT"]
        plans, fixed = [], []
        if self._optional("auto_update_commands",True):
            if len(drift)>30:
                result["attention"].append(dict(name="Command reset",reason="More than 30 commands drift; no rewrites applied"))
            else:
                commands = [self._core.command_dict(c) for c in self.wingman.commands.all()]
                for row in drift:
                    command = next((c for c in commands if c.get("name")==row["name"]),None)
                    try:
                        entry = entries.get(row["name"])
                        if entry and set(result["deferred"]) & {a for step in entry["steps"] for a in step["actions"]}:
                            raise ValueError("Custom keys awaiting restore; command repair deferred")
                        new,changes = healer.repair(command,entry,live.engine,self._core,report["mode"]=="curated")
                        plans.append((command,new))
                        fixed.append(dict(name=row["name"],steps=changes))
                    except Exception as error:
                        result["attention"].append(dict(name=row["name"],reason=str(error)[:160]))
        else:
            result["attention"].extend(dict(name=r["name"],reason="Automatic command updates disabled") for r in drift)
        if plans:
            future = None
            try:
                if self._loop is None or not self._loop.is_running():
                    raise ValueError("Wingman event loop unavailable; repair deferred")
                healer.backup(generated,"commands.json",json.dumps([self._core.command_dict(c) for c in self.wingman.commands.all()],ensure_ascii=False).encode("utf-8"), lambda:self._closed)
                settled = threading.Event()
                async def apply():
                    try:
                        await self._apply_repairs(plans)
                    finally:
                        settled.set()
                future = asyncio.run_coroutine_threadsafe(apply(),self._loop)
                try:
                    future.result(timeout=self._save_timeout_seconds+10)
                except concurrent.futures.TimeoutError:
                    # Never report failure while a save may still finish. If
                    # cancellation is refused, settle the actual terminal result.
                    if future.cancel():
                        settled.wait()
                        raise ValueError("Command repair timed out; rolled back")
                    future.result()
                if report["mode"]=="curated":
                    manifest = read("command_manifest.json",{})
                    for old,new in plans:
                        entry = next(e for e in manifest["commands"] if e["name"]==new["name"])
                        entry.update(fingerprint=self._core.command_fingerprint(new),
                                     baselineActions=self._core.resolved(new,live.engine,True))
                    self._write_heal_json(generated/"command_manifest.json",manifest)
                checked = self._check_drift(live)
                statuses = {r["name"]:r["status"] for r in checked["commands"]}
                if any(statuses.get(f["name"])!="OK" for f in fixed):
                    raise ValueError("Post-save drift verification failed")
                result["fixed"] = fixed
            except Exception as error:
                result["attention"].append(dict(name="Command repair",reason=str(error)[:160]))
        self._finish_heal(generated,result,healer.snapshot(live.engine))

    def _start_refresh(self, setting):
        with self._lock:
            if self._closed or self._refreshing or self._fresh_lock.locked():
                return
            if setting != self._setting:
                self._bindings = None  # Do not show another installation's binds.
                self._game = None
                self._attempted_build = None
            self._setting = setting
            self._refreshing = True
            self._error = ""
            self._thread = threading.Thread(target=self._refresh, args=(setting,),
                                            name="SC-Keybindings-refresh", daemon=True)
            self._thread.start()

    def _refresh(self, setting):
        with self._work_lock:
            if not self._closed:
                self._refresh_locked(setting)

    def _refresh_locked(self, setting):
        staging = None
        with self._lock:
            self._setting = setting
            self._error = ""
        try:
            if self._core is None:
                self._core = _sibling_module("keybinds_core")
            game = locate_game(setting)
            build = read_build(game)
            out = Path(self._fresh_import_dir or self.get_generated_files_dir()) / "defaults.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._game, self._index_path = game, out
                self._attempted_build = build["BuildId"]
            cached = None
            try:
                cached = json.loads(out.read_text(encoding="utf-8-sig"))
                live = self._core.LiveBindings(cached, game / "user/client/0/Profiles/default/actionmaps.xml")
                live.refresh()
                with self._lock:
                    if not self._closed:
                        self._bindings = live
            except (OSError, ValueError, KeyError, TypeError):
                cached = None
            # Check installation identity too; BuildId may be shared by two LIVE copies.
            stat = (game / "Data.p4k").stat()
            fresh = (cached and cached.get("metadata", {}).get("buildId") == build["BuildId"]
                     and cached.get("metadata", {}).get("p4kSize") == stat.st_size
                     and cached.get("metadata", {}).get("p4kMtime") == _sibling_module("p4k_reader")._iso(stat.st_mtime)
                     and cached.get("metadata", {}).get("extractorVersion") == 2)
            with self._lock:
                self._stale = not fresh
            if not fresh:
                target = out
                staging = tempfile.TemporaryDirectory(prefix=".index-import-",dir=out.parent)
                out = Path(staging.name)/"defaults.json"
                node = shutil.which("node")
                if not node:
                    fallback = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs/node.exe"
                    if fallback.is_file():
                        node = str(fallback)
                started = time.perf_counter()
                extractor = "Python"
                if node:
                    try:
                        # The fixed helper checks Node >=22.15 and zstd availability.
                        subprocess.run(
                            [node, str(Path(__file__).with_name("extract_defaults.mjs")),
                             "--game", str(game), "--out", str(out)],
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=180,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
                        )
                        extractor = "Node.js"
                    except (OSError, subprocess.SubprocessError):
                        self.log.info("Node.js extraction unavailable; trying Python", server_only=True)
                if extractor == "Python":
                    _sibling_module("p4k_reader").extract(game, out)
                self.log.info(f"Game defaults extractor: {extractor}; {time.perf_counter()-started:.3f} seconds", server_only=True)
                raw = json.loads(out.read_text(encoding="utf-8-sig"))
                # Installation must not have updated under the extractor.
                final_stat = (game / "Data.p4k").stat()
                metadata = raw.get("metadata", {})
                if (metadata.get("buildId") != read_build(game)["BuildId"]
                    or metadata.get("p4kSize") != final_stat.st_size
                    or metadata.get("p4kMtime") != _sibling_module("p4k_reader")._iso(final_stat.st_mtime)):
                    raise ValueError("Game files changed during extraction; prepare the skill again")
                live = self._core.LiveBindings(raw, game / "user/client/0/Profiles/default/actionmaps.xml")
                live.refresh()
                healer = _sibling_module("self_heal")
                for source,dest in ((out,target),(Path(str(out)+".entries.json"),Path(str(target)+".entries.json"))):
                    if source.is_file():
                        healer.atomic_bytes(dest,source.read_bytes(),closed=lambda:self._closed)
                with self._lock:
                    self._index_path = target
                    if not self._closed:
                        self._bindings = live
                        self._stale = False
        except Exception as error:
            # No raw subprocess stdout/stderr, secrets, or full file content in tool responses.
            detail = "extractor failed" if isinstance(error, subprocess.CalledProcessError) else str(error)
            with self._lock:
                self._error = detail[:180]
                self._stale = True
        finally:
            if staging is not None:
                staging.cleanup()
            with self._lock:
                self._refreshing = False
        # Stay on the background worker, outside the index refresh exception
        # boundary and after lookups cease displaying "refreshing".
        if (not self._closed and not self._stale and not self._error
                and self._fresh_owner != threading.get_ident()):
            self._startup_heal(live)

    async def _fresh_switch_ready(self):
        if not self._optional("fresh_start", False):
            await asyncio.to_thread(self._rearm_fresh_switch)
            return False
        def pending():
            path = Path(self.get_generated_files_dir())/"fresh-start-state.json"
            try:
                return not json.loads(path.read_text(encoding="utf-8")).get("done")
            except (OSError, ValueError, AttributeError):
                return True
        return await asyncio.to_thread(pending)

    def _rearm_fresh_switch(self):
        if not self._closed:
            (Path(self.get_generated_files_dir())/"fresh-start-state.json").unlink(missing_ok=True)

    async def update_config(self, new_config):
        old = self._optional("fresh_start", False)
        await super().update_config(new_config)
        if not self._optional("fresh_start", False):
            await asyncio.to_thread(self._rearm_fresh_switch)
        elif not old:
            self._launch_fresh_switch()

    def _launch_fresh_switch(self):
        async def run():
            report = await asyncio.to_thread(self._fresh_start_sync, "switch")
            if not self._closed:
                try:
                    await self.wingman.ui.show_dialog("Fresh start keybindings", report["dialog"])
                except Exception as error:
                    self.log.warning("Fresh start dialog unavailable: "+str(error)[:120], server_only=True)
        self._fresh_task = asyncio.create_task(run())

    @command_action(label="Fresh start: re-import all keybindings",
                    description="Re-import controls and reset this skill's derived state.",
                    respond="speak")
    async def fresh_start_keybindings(self):
        # A timeout does not launch another worker or cancel an in-flight save.
        task = asyncio.create_task(asyncio.to_thread(self._fresh_start_sync, "command"))
        self._fresh_task = task
        try:
            report = await asyncio.wait_for(asyncio.shield(task), self._fresh_command_seconds)
            text = report["spoken"]
        except asyncio.TimeoutError:
            text = "Fresh start is still running. Check the skill log for its result."
        self._arm(text)
        return text

    @staticmethod
    def _fresh_message(status, spoken):
        return dict(status=status, spoken=spoken, dialog=spoken)

    def _fresh_start_sync(self, trigger):
        """Shared synchronous worker entry point; never call on Wingman's loop."""
        if not self._fresh_lock.acquire(blocking=False):
            return self._fresh_message("busy", "Fresh start is already running.")
        acquired = False
        report = self._fresh_message("failed", "Fresh start could not finish.")
        generated = Path(self.get_generated_files_dir())
        healthy = None
        self._fresh_backup = None
        try:
            acquired = self._work_lock.acquire(timeout=self._fresh_wait_seconds)
            if not acquired:
                report = self._fresh_message("busy", "Keybindings are busy refreshing or healing. Try Fresh start again shortly.")
            else:
                self._fresh_owner = threading.get_ident()
                self._fresh_phase = "repair"
                self._fresh_heal_result = None
                with self._lock:
                    self._change_note = None
                    self._last_choice = None
                self._fresh_guard()
                # One final normal refresh + repair, with profile restoration
                # suppressed until the explicit, separately guarded restore below.
                self._refresh(self._property())
                self._fresh_guard(index=True)
                self._startup_heal(self._bindings)
                if self._fresh_heal_result is None:
                    raise ValueError("Final repair pass unavailable; state was not reset")
                healer = _sibling_module("self_heal")
                with healer.startup_lock(generated, lambda:self._closed, reclaim=False) as owned:
                    if not owned:
                        raise ValueError("Another skill instance is healing; state was not reset")
                    with self._drift_lock:
                        healthy = (self._bindings,self._game,self._index_path,self._attempted_build,
                                   self._stale,self._error)
                        report = self._fresh_reimport(generated, healer, trigger)
        except Exception as error:
            if healthy is not None:
                with self._lock:
                    (self._bindings,self._game,self._index_path,self._attempted_build,
                     self._stale,self._error) = healthy
            report = self._fresh_message("failed", "Fresh start could not finish; review the skill log.")
            report["attention"] = [str(error)[:180]]
            report["dialog"] += "\n\n"+str(error)[:180]
            if self._fresh_backup:
                report["backup"] = self._fresh_backup
                report["dialog"] += "\n\nBackup bundle: "+self._fresh_backup
        finally:
            try:
                if not self._closed:
                    if report["status"] != "busy":
                        self._write_heal_json(generated/"fresh-start-report.json", report)
                        if trigger == "switch" and self._optional("fresh_start",False):
                            self._write_heal_json(generated/"fresh-start-state.json", dict(done=True,at=time.time()))
                    self.log.warning(report.get("log",report["spoken"] + (" "+ "; ".join(report.get("attention",[])) if report["status"]=="failed" else "")))
            except Exception as error:
                report = self._fresh_message("failed", "Fresh start could not save its report; review the skill log.")
                report["attention"] = [str(error)[:180]]
                self.log.warning(report["spoken"]+" "+str(error)[:180])
            finally:
                with self._lock:
                    self._change_note = None
                self._fresh_import_dir = None
                self._fresh_owner = self._fresh_phase = None
                if acquired:
                    self._work_lock.release()
                self._fresh_lock.release()
        return report

    def _fresh_guard(self, index=False):
        if self._closed:
            raise ValueError("Skill closed; fresh start stopped")
        if index and (self._bindings is None or self._stale or self._error or self._bindings.warning):
            raise ValueError(self._error or (self._bindings.warning if self._bindings else "") or "Fresh game index unavailable")

    def _fresh_reimport(self, generated, healer, trigger="command"):
        small = ("bindings_snapshot.json","command_steps.json","command_baseline.json",
                 "drift-report.json","last-changes.json","restore-state.json",
                 "custom_binds.json","command_manifest.json")
        bundle = {name:(generated/name).read_bytes().decode("utf-8")
                  for name in small if (generated/name).is_file()}
        self._fresh_guard()
        backup = healer.backup(generated,"fresh-start-state.json",
                               json.dumps(dict(files=bundle),ensure_ascii=False).encode("utf-8"),
                               lambda:self._closed)
        self._fresh_backup = backup.name
        reset = ("defaults.json","defaults.json.entries.json",*small[:6])
        for name in reset:
            self._fresh_guard()
            (generated/name).unlink(missing_ok=True)
        if "custom_binds.json" in bundle:
            custom = json.loads(bundle["custom_binds.json"].lstrip("\ufeff"))
            if not isinstance(custom,list) or any(not isinstance(row,dict) for row in custom):
                raise ValueError("Invalid custom_binds.json; restore the fresh-start bundle")
            for row in custom:
                row.pop("removed",None)
                row.pop("missing_in_game",None)
            self._write_heal_json(generated/"custom_binds.json",custom)
        self._fresh_phase = "import"
        # Extract into an empty staging folder: neither extractor can reuse the
        # old entries/index cache, or publish fresh files after unload.
        with tempfile.TemporaryDirectory(prefix=".fresh-start-",dir=generated) as stage:
            self._fresh_import_dir = stage
            self._refresh(self._property())
            self._fresh_guard(index=True)
            for name in ("defaults.json","defaults.json.entries.json"):
                source = Path(stage)/name
                if source.is_file():
                    healer.atomic_bytes(generated/name,source.read_bytes(),closed=lambda:self._closed)
        self._fresh_import_dir = None
        self._index_path = generated/"defaults.json"
        live = self._bindings
        first = self._check_drift(live)
        if first is None:
            raise ValueError("Fresh drift check unavailable")
        restored = healer.restore(live,generated,self._core,_sibling_module("p4k_reader"),
                                  self._optional("restore_custom_binds",False),
                                  self._process_check or healer.game_running,lambda:self._closed,
                                  fresh_start=True)
        # Only the final repair pass can edit commands. Re-baseline against the
        # final profile, including explicit custom-key insertions, not defaults.
        if restored["restored"] and not (generated/"command_manifest.json").exists():
            for name in ("command_baseline.json","command_steps.json"):
                self._fresh_guard()
                (generated/name).unlink(missing_ok=True)
        checked = self._check_drift(live)
        if checked is None:
            raise ValueError("Final drift check unavailable")
        self._write_heal_json(generated/"bindings_snapshot.json",healer.snapshot(live.engine))
        report = healer.fresh_report(live.raw,checked,self._fresh_heal_result,
                                     restored,self._core,trigger)
        report.update(backup=backup.name,reset=list(reset))
        report["dialog"] = (report["spoken"]+
                            ("\n\nAttention:\n"+report["details"] if report["details"] else "")+
                            "\n\nReset: "+", ".join(reset)+
                            "\n\nKept: curated intent, custom-key list, commands and backups."+
                            "\n\nBackup bundle: "+backup.name)
        return report

    def _lookup(self, action="", key=""):
        setting = self._property()
        with self._lock:
            if setting != self._setting:
                self._start_refresh(setting)
            if self._game and not self._refreshing:
                try:
                    build_id = read_build(self._game)["BuildId"]
                    current = self._bindings.engine.metadata.get("buildId") if self._bindings else None
                    if build_id != current:
                        self._stale = True
                    if build_id != self._attempted_build:
                        self._start_refresh(setting)
                except (OSError, ValueError, TypeError) as error:
                    self._error = "Build cannot be checked: " + str(error)[:110]
                    self._stale = True
            warnings = []
            if self._refreshing:
                warnings.append("Refreshing game defaults.")
            if self._error:
                warnings.append("Refresh unavailable: " + self._error)
            if self._stale and self._bindings:
                warnings.append("Last good index; possibly outdated. Do not assume current keys.")
            if not self._bindings:
                return (" ".join(warnings) or "Keybindings are not prepared.") + " No verified binding available; do not guess."
            # Never block a lookup behind startup saves or rebaseline a repair
            # in flight. RLock permits the healer's own post-save drift check.
            if self._drift_lock.acquire(blocking=False):
                try:
                    before = self._bindings.fingerprint
                    self._bindings.refresh()
                    if before != self._bindings.fingerprint and not self._stale and not self._refreshing and not self._bindings.warning:
                        self._check_drift(self._bindings)
                finally:
                    self._drift_lock.release()
            return self._bindings.engine.answer(str(action)[:300], str(key)[:160],
                                         " ".join(warnings + [self._bindings.warning]))[:1200]

    @tool(name="look_up_keybinding", description="Speak verified Star Citizen bindings. Lookup only.", summarize=True, wait_response=False)
    async def look_up_keybinding(self, action: str = "", key: str = ""):
        """Look up real game bindings.

        Args:
            action: the user's own words, not a command name.
            key: Key to reverse-look up.
        """
        return await asyncio.to_thread(self._direct_lookup, action, key)

    def _ordinal_choice(self):
        with self._guard_lock:
            current, stored = self._user_message, self._last_choice
            now = time.monotonic()
            if not current or not stored or not (0 <= now-current[1] < 120 and 0 <= now-stored[1] < 120):
                return None
            match = re.search(r"\b(first|second|third|last)\s+(?:one|option)\b|\boption\s+(one|two|three|1|2|3)\b", current[0], re.I)
            if not match:return None
            word = (match[1] or match[2]).lower()
            index = {"first":0,"one":0,"1":0,"second":1,"two":1,"2":1,"third":2,"three":2,"3":2,"last":len(stored[0])-1}[word]
            if index >= len(stored[0]):return None
            row = stored[0][index]
            self._last_choice = None
            self._user_message = None
            return row

    def _direct_lookup(self, action, key):
        # Prepare/refresh first, then choose against the same current engine.
        text = self._lookup(action, key)
        choice = None
        selected = self._ordinal_choice()
        with self._lock:
            if self._bindings and not selected and (action.strip() or key.strip()):
                action, key, choice = self._select_query(action, key)
                text = self._bindings.engine.answer(action, key) if not (self._stale or self._error or self._refreshing or self._bindings.warning) else text
        if not action.strip() and not key.strip():return text
        with self._lock:
            if not self._bindings:
                spoken = "Keybindings are refreshing; no verified binding is available." if self._refreshing else "Keybindings are not prepared; no verified binding is available."
            elif self._stale or self._error or self._refreshing or self._bindings.warning:
                spoken = "The bindings may be outdated. " if self._stale or self._bindings.warning else ""
                spoken += "Game defaults are refreshing; please ask again." if self._refreshing else "The refresh failed; I can't verify a current key."
            else:
                engine = self._bindings.engine
                options = choice or (engine.close_options(engine.grouped(str(action)[:300])) if not key and not selected else [])
                if len(options) > 1 and engine.choice(options).endswith("?"):
                    with self._guard_lock:self._last_choice = (options, time.monotonic())
                if selected:
                    spoken = engine.row_speech(selected)
                    text = engine.answer(selected["label"], "")
                else:
                    spoken = engine.choice(choice) if choice else engine.spoken(str(action)[:300], str(key)[:160])
                spoken = self._core.capitalize(spoken)
                items = str(self.retrieve_custom_property_value("endearments", []) or "").split(",")
                endearment = items[self._endearment_index % len(items)].strip()
                self._endearment_index += 1
                if endearment:
                    endearment = re.sub(r"[^a-zA-Z '-]", "", endearment)[:25]
                    spoken = spoken[:-1] + ", " + endearment + spoken[-1:]
            note = self._pending_change()
            summary = note["spoken"] if note else ""
            if summary:
                spoken = self._cap_spoken(spoken, 1200-len(summary)-1)+" "+summary
            else:
                spoken = self._cap_spoken(spoken, 1200)
            self._change_note = None
            # Arm while still holding the note-consumption lock, using the full
            # appended text rather than only the key answer.
            self._arm(spoken)
        return (f'Already said aloud to the user: "{spoken}"\n' + text)[:1200], spoken

    async def unload(self):
        with self._lock:
            self._closed = True
        with self._guard_lock:self._guard = None
        for subscription in self._subscriptions:subscription.unsubscribe()
        self._subscriptions = []
        # At most one bounded extractor may already be running (180-second timeout).
        # No further refresh can start; do not publish its result after unload.
        await super().unload()
