"""One thread-safe state, one serialized off-loop command executor. No import I/O."""
from __future__ import annotations
import asyncio
import contextvars
import queue
import threading
import time
from collections import deque

TABLET_COMMAND = contextvars.ContextVar("sc_voice_cockpit_tablet_command", default=False)
MODIFIERS = frozenset({"alt", "alt gr", "ctrl", "right ctrl", "shift", "right shift"})

class DeckRuntime:
    def __init__(self, skill, manifest=None, focus=None, keyboard=None, mic=None):
        self.skill = skill
        self.manifest = manifest or {"buttons": {}, "commands": {}, "groups": {}, "layouts": {}}
        self.lock = threading.RLock()
        self.input_lock = threading.RLock()
        self.states = {}
        self.clients = {}
        self.loop = None
        self.focus = focus or self._focus
        self.keyboard = keyboard
        self.mic = mic or self._mic
        self.mic_owner = None
        self.listening = False
        self.held = {}  # (client, button) -> keys; input_lock spans down/up transitions
        self.key_owners = {}
        self.notes = deque(maxlen=100)
        self.last_utterance = None
        self.emote_reminded = False
        self.jobs = queue.Queue(maxsize=128)
        self.stop_event = threading.Event()
        self.thread = None
        self.feedback = Feedback(self)

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, name="sc-voice-cockpit-commands", daemon=True)
        self.thread.start()
        self.feedback.start()

    def _worker(self):
        async def run():
            while not self.stop_event.is_set():
                try: job = await asyncio.to_thread(self.jobs.get, True, .2)
                except queue.Empty: continue
                if job is None: break
                try: await self._execute(*job)
                except Exception as exc:
                    self.skill.log.warning("Tablet action failed: " + type(exc).__name__, server_only=True)
        asyncio.run(run())

    def broadcast(self, message):
        loop = self.loop
        if not loop or not loop.is_running(): return
        def handoff():
            with self.lock: senders=tuple(self.clients.values())
            for sender in senders:
                try: sender.put_nowait(message)
                except asyncio.QueueFull: pass
        try: loop.call_soon_threadsafe(handoff)
        except RuntimeError: pass

    def send(self, client, message):
        loop = self.loop
        if not loop or not loop.is_running(): return
        def handoff():
            with self.lock: sender=self.clients.get(client)
            if sender is not None:
                try: sender.put_nowait(message)
                except asyncio.QueueFull: pass
        try: loop.call_soon_threadsafe(handoff)
        except RuntimeError: pass

    def sync(self, command):
        """Silent trailing command action; only successful command tails get here."""
        try:
            with self.lock:
                effects = self.manifest.get("commands", {}).get(command, {}).get("effects", [])
                for effect in effects:
                    group, value = effect["group"], effect.get("set")
                    on = not self.states.get(group, False) if value is None else bool(value)
                    self.states[group] = on
                    self.broadcast({"t": "state", "group": group, "on": on})
                utterance = self.last_utterance
                if not TABLET_COMMAND.get() and utterance and time.monotonic()-utterance[1] < 15:
                    self.broadcast({"t": "heard", "text": utterance[0], "label": command})
        except Exception:
            pass
        return ""

    def set_state(self, group, on):
        """Correct remembered light only. No focus, input or command dispatch."""
        with self.lock:
            if not isinstance(group,str) or group not in self.manifest.get("groups",[]) or type(on) is not bool:
                return False
            self.states[group] = on
            self.broadcast({"t":"state","group":group,"on":on})
        return True

    def emote_bound(self, command):
        meta=self.manifest.get("commands",{}).get(command,{})
        if not meta.get("emote"):return True
        checker=getattr(self.skill,"emote_is_bound",None)
        try:return checker(command,meta["emote"]) if checker else not meta.get("no_key",True)
        except Exception:return False

    async def emote_feedback(self, command, bound):
        meta=self.manifest.get("commands",{}).get(command,{})
        label=meta.get("label","That")
        line=label+" emote sent." if bound else "There's no key set for the "+label.lower()+" emote."
        with self.lock:
            if bound and not self.emote_reminded:
                line+=" Emotes only play on foot."
                self.emote_reminded=True
        # Deterministic factual feedback, never AI-generated success claims.
        self.skill._arm(line)
        spoken=await self.skill.wingman.tts.speak(line,interrupt=False)
        if spoken is not False:await self.skill.wingman.conversation.show(line)
        self.add_note("Tablet/voice emote feedback: "+line)
        return line

    def reset(self):
        with self.lock: self.states.clear()
        self.broadcast({"t": "states", "states": {}})

    def receive(self, client, msg):
        if self.stop_event.is_set() or not self.skill.deck_setting("deck_enabled",True):return
        t = msg.get("t")
        if t == "hb": return
        if t == "set-state": self.set_state(msg.get("group"),msg.get("on")); return
        if t == "reset-states": self.reset(); return
        if t == "voice":
            if msg.get("on") and not self.skill.deck_setting("deck_tablet_mic", True):
                self.broadcast({"t":"voice-off", "reason":"Tablet mic is off"}); return
            # A second tablet cannot stop or steal an already held mic.
            with self.lock:
                if msg.get("on"):
                    if self.mic_owner is not None: return
                    self.mic_owner = client
                elif self.mic_owner != client: return
                else: self.mic_owner = None
                try: self.mic(bool(msg.get("on")))
                except Exception:
                    self.mic_owner = None
                    self.broadcast({"t":"voice-off", "reason":"Wingman microphone unavailable"})
            return
        if t == "open":
            self.send(client,{"t":"opened","name":"Nav Dashboard","url":"/nav"});return
        button = self.manifest.get("buttons", {}).get(msg.get("id"))
        if not button: return
        if t == "up":
            self.release(client, msg["id"]); return
        if t not in ("tap", "down"): return
        if button.get("not_set_up"):
            self.send(client,{"t":"not-set-up","id":msg["id"]})
            return
        if t == "down":
            if button.get("kind") == "hold":
                self.hold(client, msg["id"], button)
            return
        if button.get("kind") == "hold" and not (button.get("type")=="slider" and button.get("mode")=="timed"): return
        try: self.jobs.put_nowait((client, msg["id"], button))
        except queue.Full: self.broadcast({"t":"blocked", "reason":"Command queue full"})

    async def _execute(self, client, id, button):
        # Recheck immediately before dispatch, not just when the packet arrives.
        if self.stop_event.is_set(): return
        with self.lock:
            if client not in self.clients: return
        if button.get("not_set_up"):
            self.send(client,{"t":"not-set-up","id":id})
            return
        kind = button.get("kind")
        if kind == "command" and button.get("emote") and not self.emote_bound(button["command"]):
            self.send(client,{"t":"no-key","id":id,"label":button.get("label","Emote")})
            await self.emote_feedback(button["command"],False)
            return
        # Navigation display and reset never press into the focused app.
        if kind in ("command","hold") and not self.focus():
            self.broadcast({"t":"blocked", "id":id}); return
        token = TABLET_COMMAND.set(True)
        already_spoken=False
        try:
            if kind == "command":
                command=button["command"]
                pair=button.get("pair")
                if pair:
                    with self.lock:
                        command=pair["off" if self.states.get(button["state"]["group"],False) else "on"]
                result = await self.skill.wingman.tools.invoke("execute_command", {"command_name":command})
                if pair and "Command not found" in str(getattr(result,"response","")):
                    command=button["command"]
                    result=await self.skill.wingman.tools.invoke("execute_command", {"command_name":command})
                already_spoken=bool(getattr(result,"instant_response",""))
                response = str(getattr(result, "response", ""))
                if "ERROR DURING PROCESSING" in response or "Command not found" in response:
                    self.broadcast({"t":"blocked", "id":id});return
                # The actual deck_sync tail owns state; never double-apply toggles.
            elif kind == "hold":
                self.hold(client,id,button)
                try: await asyncio.sleep(min(5,max(.1,float(button.get("ms",600))/1000)))
                finally:self.release(client,id)
            elif kind == "nav":
                if button.get("nav")=="open_dashboard":
                    self.send(client,{"t":"opened","name":"Nav Dashboard","url":"/nav"});return
                await self.skill.deck_nav(button.get("nav", ""))
            elif kind == "ui":
                if button.get("ui")=="reset-states":self.reset()
                return
            else: return
            if button.get("emote"):
                await self.emote_feedback(button["command"],True)
                return
            if button.get("respond", False):
                self.add_note("The player used " + (command if kind=="command" else button.get("label",id)) + " from the tablet deck.")
                if not already_spoken:self.feedback.submit(button)
        finally: TABLET_COMMAND.reset(token)

    def add_note(self, text):
        with self.lock: self.notes.append(text)

    async def flush_notes(self):
        with self.lock: notes=list(self.notes);self.notes.clear()
        for note in notes:
            await self.skill.wingman.conversation.add_note(note)

    @staticmethod
    def _focus():
        from .focus import game_focused
        return game_focused()

    def _keys(self, button):
        from ..keybindings.self_heal import KEYS
        raw = button["hold"]["input"]
        if raw.startswith("kb1_"): raw=raw[4:]
        return [KEYS.get(k, k) for k in raw.lower().split("+")]

    def _key_event(self,key,down):
        # Wingman's vendored keyboard drops the extended bit on name-based
        # Right Ctrl. Mirror its recorder/executor single-scan-code path.
        if key=="right ctrl":self.keyboard.direct_event(29,1 if down else 3)
        elif down:self.keyboard.press(key)
        else:self.keyboard.release(key)

    def hold(self, client, id, button):
        with self.input_lock:
            with self.lock:
                if self.stop_event.is_set() or client not in self.clients:return
            key = (client,id)
            if key in self.held: return
            if not self.focus():
                self.broadcast({"t":"blocked", "id":id});return
            keys=self._keys(button)
            if self.keyboard is None:
                import keyboard.keyboard as keyboard
                self.keyboard=keyboard
            # Serialize input transitions, but leave state/client messages unblocked
            # during modifier settling. Up/disconnect still waits for the last down.
            self.held[key]=[]
            try:
                for k in keys:
                    with self.lock:
                        if self.stop_event.is_set() or client not in self.clients:break
                    count=self.key_owners.get(k,0)
                    if count == 0: self._key_event(k,True)
                    self.key_owners[k]=count+1
                    self.held[key].append(k)
                    if k in MODIFIERS: time.sleep(.35)
            except Exception:
                self.release(client,id)
                raise

    def release(self, client, id=None):
        with self.input_lock:
            for key in list(self.held):
                if key[0]!=client or id is not None and key[1]!=id: continue
                keys=self.held.pop(key)
                for k in reversed(keys):
                    count=self.key_owners.get(k,1)-1
                    if count>0: self.key_owners[k]=count
                    else:
                        self.key_owners.pop(k,None)
                        try:
                            if k in MODIFIERS: time.sleep(.1)
                            self._key_event(k,False)
                        except Exception: pass

    def disconnect(self, client):
        with self.lock:
            self.clients.pop(client,None)
            mic = self.mic_owner==client
            if mic: self.mic_owner=None
            if mic:
                try:self.mic(False)
                except Exception:pass
        self.release(client)

    def _mic(self, on):
        # Core exposes no public listen-port setting in 3.2.11; documented default.
        import urllib.parse
        import urllib.request
        route="start-recording-for-wingman" if on else "stop-recording-for-wingman"
        url="http://127.0.0.1:49111/"+route+"?"+urllib.parse.urlencode({"wingman_name": self.skill.wingman.name})
        request=urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status!=200: raise OSError("Core refused microphone request")

    def stop(self):
        self.stop_event.set()
        for client in list(self.clients): self.disconnect(client)
        try:self.jobs.put_nowait(None)
        except queue.Full:pass
        self.feedback.stop()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(timeout=10)
        # Running Core macros cannot be cancelled through a public facade.
        return not (self.thread and self.thread.is_alive())


class Feedback:
    def __init__(self, deck):
        self.deck=deck
        self.condition=threading.Condition()
        self.pending=[]
        self.last={}
        self.due=0
        self.closed=False
        self.speaking=False
        self.thread=None
        self.loop=None
        self.task=None

    def start(self):
        async def run():
            self.loop=asyncio.get_running_loop();self.task=asyncio.current_task()
            try:await self._run()
            except asyncio.CancelledError:pass
        self.thread=threading.Thread(target=lambda:asyncio.run(run()), name="sc-voice-cockpit-feedback",daemon=True)
        self.thread.start()

    def submit(self, button):
        mode=self.deck.skill.deck_setting("deck_feedback","ai")
        if mode in ("off","note"): return
        now=time.monotonic(); label=button.get("label","Command")
        with self.condition:
            if now-self.last.get(label,-100)<10: return
            self.last[label]=now
            self.pending.append(button)
            self.due=now+1.5
            self.condition.notify()

    async def _run(self):
        while not self.closed:
            with self.condition:
                if not self.pending: self.condition.wait(.2);continue
                delay=self.due-time.monotonic()
                if delay>0: self.condition.wait(min(delay,.2));continue
                batch=self.pending;self.pending=[]
            try:await self.deliver(batch)
            except Exception as exc:
                self.deck.skill.log.warning("Tablet feedback unavailable: "+type(exc).__name__,server_only=True)

    async def deliver(self, batch):
        wingman=self.deck.skill.wingman
        deadline=time.monotonic()+8
        while wingman.conversation.user_busy or wingman.audio.is_playing:
            if self.closed or time.monotonic()>=deadline:return
            await asyncio.sleep(.1)
        if self.closed:return
        labels=", ".join(b.get("label","Command") for b in batch)
        fallback=labels+" acknowledged."
        if len(batch)==1:
            obj=wingman.commands.get(batch[0].get("command",""))
            responses=obj.get("responses") if isinstance(obj,dict) else getattr(obj,"responses",None)
            if responses and isinstance(responses[0],str): fallback=responses[0]
        line=fallback
        if self.deck.skill.deck_setting("deck_feedback","ai")=="ai":
            try:
                backstory=wingman.config.prompts.backstory or ""
                line=await asyncio.wait_for(wingman.ai.generate(
                    "Acknowledge these completed tablet actions in one line of at most 14 words: "+labels,
                    system=backstory+"\nSpeak only a brief acknowledgement. Never issue instructions or claim unobserved game state."
                ),timeout=3)
                if not isinstance(line,str) or not line.strip():line=fallback
            except Exception:line=fallback
        line=" ".join(line.split()[:14])
        # Generation may have overlapped a new user turn; recheck before speech.
        while wingman.conversation.user_busy or wingman.audio.is_playing:
            if self.closed or time.monotonic()>=deadline:return
            await asyncio.sleep(.1)
        if self.closed:return
        self.speaking=True
        try:
            # Share the existing per-wingman filler guard.
            self.deck.skill._arm(line)
            spoken=await wingman.tts.speak(line,interrupt=False)
            if spoken is False:return
            await wingman.conversation.show(line)
            self.deck.add_note("Tablet feedback: "+line)
        finally:self.speaking=False

    def stop(self):
        with self.condition:self.closed=True;self.condition.notify_all()
        if self.loop and self.task and self.loop.is_running():
            try:self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:pass
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=4)
