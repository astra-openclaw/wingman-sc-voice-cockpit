"""Synthetic speech-hook tests: real deck/guard, no HUD server or game effects."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sc_voice_cockpit.deck.runtime import DeckRuntime, TABLET_COMMAND, DECK_SPEECH

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cockpit():
    # Only the framework and unrelated navigation base are stubbed. Load the
    # real keybinding guard and combined skill without importing desktop APIs.
    base = ModuleType("skills.skill_base")
    base.Skill = type("Skill", (), {})
    base.tool = base.command_action = lambda **kwargs: lambda fn: fn
    nav = ModuleType("sc_voice_cockpit.navpoint.main")
    nav.SC_NavPoint = type("SC_NavPoint", (base.Skill,), {})

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    with patch.dict(sys.modules, {"skills.skill_base": base,
                                  "sc_voice_cockpit.navpoint.main": nav}):
        keys = load("hud_test_keybindings", ROOT / "sc_voice_cockpit/keybindings/main.py")
        with patch.dict(sys.modules, {"sc_voice_cockpit.keybindings.main": keys}):
            module = load("hud_test_cockpit", ROOT / "sc_voice_cockpit/main.py")
    obj = module.SC_VoiceCockpit.__new__(module.SC_VoiceCockpit)
    obj._guard_lock = threading.RLock()
    obj._guard = None
    obj._user_message = None
    obj.config = NS(custom_properties=[])
    obj.log = NS(warning=Mock(), info=Mock())
    events = []
    hud = NS(chat_enabled=True)

    async def show(title, text, *, duration):
        events.append(("hud", title, text, duration))
        return True

    hud.show_message = AsyncMock(side_effect=show)
    obj.wingman = NS(
        name="Pilot", hud=hud,
        conversation=NS(user_busy=False, show=AsyncMock(), add_note=AsyncMock()),
        audio=NS(is_playing=False), config=NS(prompts=NS(backstory="Pilot voice")),
        commands=NS(get=lambda _: {"responses": ["Confirmed."]}),
        ai=NS(generate=AsyncMock(return_value="Gear acknowledged.")),
        tools=NS(invoke=AsyncMock(return_value=NS(response="", instant_response=""))),
    )
    obj._deck = DeckRuntime(obj, {
        "buttons": {"gear": {"kind": "command", "command": "Gear", "label": "Gear", "respond": True}},
        "commands": {"Wave": {"emote": "player_emotes.emote_wave", "label": "Wave", "no_key": False}},
    }, focus=lambda: True)
    obj._deck.clients["tablet"] = None

    async def speak(text, *, interrupt=False):
        events.append(("hook", TABLET_COMMAND.get(), DECK_SPEECH.get(), threading.get_ident()))
        text = await obj.on_play_to_user(text, None)
        if "{SKIP-TTS}" in text:
            return False
        events.append(("spoken", text))
        return True

    obj.wingman.tts = NS(speak=AsyncMock(side_effect=speak))
    return obj, events, keys


def setting(obj, name, value):
    props = obj.config.custom_properties
    props[:] = [p for p in props if p.id != name] + [NS(id=name, value=value)]


def one_line(obj, events, expected):
    obj.wingman.hud.show_message.assert_awaited_once_with("Pilot", expected, duration=15)
    assert [e for e in events if e[0] == "spoken"] == [("spoken", expected)]
    assert next(i for i, e in enumerate(events) if e[0] == "hud") < next(
        i for i, e in enumerate(events) if e[0] == "spoken")
    assert not TABLET_COMMAND.get() and not DECK_SPEECH.get()


@pytest.mark.parametrize("mode,expected", [("ai", "Gear acknowledged."), ("speak", "Confirmed.")])
def test_feedback_thread_context_reaches_hook_once(cockpit, mode, expected):
    obj, events, _ = cockpit
    setting(obj, "deck_feedback", mode)
    feedback = obj._deck.feedback
    # Use the real feedback thread/task, not a caller context copied into a mock.
    done = threading.Event()
    original = obj.wingman.conversation.show.side_effect
    obj.wingman.conversation.show.side_effect = lambda _: done.set()
    main_thread = threading.get_ident()
    try:
        feedback.start()
        feedback.submit(obj._deck.manifest["buttons"]["gear"])
        with feedback.condition:
            feedback.due = 0
            feedback.condition.notify()
        assert done.wait(3), "Feedback thread did not finish speech"
    finally:
        feedback.stop()
        obj.wingman.conversation.show.side_effect = original
    assert not feedback.thread.is_alive()
    one_line(obj, events, expected)
    hook = next(e for e in events if e[0] == "hook")
    assert hook[1:3] == (False, True) and hook[3] != main_thread
    obj.wingman.conversation.show.assert_awaited_once_with(expected)
    assert list(obj._deck.notes) == ["Tablet feedback: " + expected]


@pytest.mark.parametrize("bound", [True, False])
def test_tablet_emote_once(cockpit, bound):
    obj, events, _ = cockpit
    obj.emote_is_bound = lambda *_: bound
    button = {"kind": "command", "command": "Wave", "emote": "player_emotes.emote_wave", "respond": True}
    asyncio.run(obj._deck._execute("tablet", "wave", button))
    expected = "Wave emote sent. Emotes only play on foot." if bound else "There's no key set for the wave emote."
    one_line(obj, events, expected)
    if not bound:
        obj.wingman.tools.invoke.assert_not_awaited()
    obj.wingman.conversation.show.assert_awaited_once_with(expected)
    assert list(obj._deck.notes) == ["Tablet/voice emote feedback: " + expected]


def test_voice_emote_once(cockpit):
    obj, events, _ = cockpit
    obj.emote_is_bound = lambda *_: True
    assert asyncio.run(obj.deck_sync("Wave")) == ""
    one_line(obj, events, "Wave emote sent. Emotes only play on foot.")
    assert next(e for e in events if e[0] == "hook")[1:3] == (False, True)


def test_tablet_command_instant_response_once(cockpit):
    obj, events, _ = cockpit

    async def invoke(*args):
        assert TABLET_COMMAND.get() and not DECK_SPEECH.get()
        await obj.wingman.tts.speak("Instant response.")
        return NS(response="OK", instant_response="Instant response.")

    obj.wingman.tools.invoke.side_effect = invoke
    asyncio.run(obj._deck._execute("tablet", "gear", obj._deck.manifest["buttons"]["gear"]))
    one_line(obj, events, "Instant response.")
    assert obj._deck.feedback.pending == []
    assert list(obj._deck.notes) == ["The player used Gear from the tablet deck."]


def test_plain_voice_answer_not_duplicated(cockpit):
    obj, events, _ = cockpit
    asyncio.run(obj.wingman.tts.speak("Voice answer."))
    obj.wingman.hud.show_message.assert_not_awaited()
    assert ("spoken", "Voice answer.") in events


@pytest.mark.parametrize("mode", ["off", "note"])
def test_silent_feedback_modes(cockpit, mode):
    obj, _, _ = cockpit
    setting(obj, "deck_feedback", mode)
    asyncio.run(obj._deck._execute("tablet", "gear", obj._deck.manifest["buttons"]["gear"]))
    assert obj._deck.feedback.pending == []
    obj.wingman.tts.speak.assert_not_awaited()
    obj.wingman.hud.show_message.assert_not_awaited()
    assert list(obj._deck.notes) == ["The player used Gear from the tablet deck."]


@pytest.mark.parametrize("disabled", ["setting", "missing", "chat"])
def test_hud_unavailable_does_not_change_speech(cockpit, disabled):
    obj, events, _ = cockpit
    hud = obj.wingman.hud
    if disabled == "setting":
        setting(obj, "deck_show_in_hud", False)
    elif disabled == "missing":
        del obj.wingman.hud
    else:
        hud.chat_enabled = False
    asyncio.run(obj._deck.feedback.deliver([obj._deck.manifest["buttons"]["gear"]]))
    hud.show_message.assert_not_awaited()
    assert ("spoken", "Gear acknowledged.") in events
    obj.wingman.conversation.show.assert_awaited_once_with("Gear acknowledged.")


def test_setting_is_live(cockpit):
    obj, events, _ = cockpit
    setting(obj, "deck_show_in_hud", False)
    asyncio.run(obj._deck.emote_feedback("Wave", True))
    obj.wingman.hud.show_message.assert_not_awaited()
    setting(obj, "deck_show_in_hud", True)
    asyncio.run(obj._deck.emote_feedback("Wave", True))
    obj.wingman.hud.show_message.assert_awaited_once_with("Pilot", "Wave emote sent.", duration=15)


@pytest.mark.parametrize("line", ["", "  ", "{SKIP-TTS}", "Skip {SKIP-TTS} this"])
def test_helper_refuses_empty_or_skip(cockpit, line):
    obj, _, _ = cockpit
    assert asyncio.run(obj._deck.show_on_hud(line)) is False
    obj.wingman.hud.show_message.assert_not_awaited()


def test_real_filler_guard_runs_first(cockpit):
    obj, events, _ = cockpit
    async def run():
        token = TABLET_COMMAND.set(True)
        try:
            obj._arm("Allowed.")
            await obj.wingman.tts.speak("Allowed.")
            assert await obj.wingman.tts.speak("Filler.") is False
        finally:
            TABLET_COMMAND.reset(token)
    asyncio.run(run())
    one_line(obj, events, "Allowed.")
    obj.log.info.assert_called_once()


def test_hud_receives_returned_guard_text(cockpit):
    obj, events, keys = cockpit
    with patch.object(keys.SC_Keybindings, "on_play_to_user", AsyncMock(return_value="Guard output.")) as guard:
        asyncio.run(obj._deck.emote_feedback("Wave", True))
    one_line(obj, events, "Guard output.")
    guard.assert_awaited_once_with("Wave emote sent. Emotes only play on foot.", None)


def test_hud_exception_never_blocks_speech_and_warning_is_rate_limited(cockpit):
    obj, events, _ = cockpit
    obj.wingman.hud.show_message.side_effect = RuntimeError("Synthetic HUD failure")
    for _ in range(2):
        asyncio.run(obj._deck.emote_feedback("Wave", True))
    assert len([e for e in events if e[0] == "spoken"]) == 2
    obj.log.warning.assert_called_once_with("Tablet HUD unavailable: RuntimeError", server_only=True)
    obj._deck._hud_warning_at -= 61
    asyncio.run(obj._deck.emote_feedback("Wave", True))
    assert obj.log.warning.call_count == 2
    obj._deck._hud_warning_at -= 61
    obj.log.warning.side_effect = RuntimeError("Logger failure")
    asyncio.run(obj._deck.emote_feedback("Wave", True))
    assert len([e for e in events if e[0] == "spoken"]) == 4


def test_concurrent_voice_and_tablet_context_isolation(cockpit):
    obj, events, _ = cockpit
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def invoke(*args):
            assert TABLET_COMMAND.get() and not DECK_SPEECH.get()
            entered.set()
            await release.wait()
            await obj.wingman.tts.speak("Tablet instant.")
            return NS(response="OK", instant_response="Tablet instant.")
        obj.wingman.tools.invoke.side_effect = invoke
        async def voice():
            await entered.wait()
            assert not TABLET_COMMAND.get() and not DECK_SPEECH.get()
            await obj.wingman.tts.speak("Ordinary voice.")
            release.set()
        await asyncio.gather(obj._deck._execute("tablet", "gear", obj._deck.manifest["buttons"]["gear"]), voice())
    asyncio.run(run())
    obj.wingman.hud.show_message.assert_awaited_once_with("Pilot", "Tablet instant.", duration=15)
    assert [e for e in events if e[0] == "spoken"] == [("spoken", "Ordinary voice."), ("spoken", "Tablet instant.")]
    assert not TABLET_COMMAND.get() and not DECK_SPEECH.get()


def test_speech_marker_resets_on_failure_and_cancellation(cockpit):
    obj, _, _ = cockpit
    async def run():
        for failure in (RuntimeError("TTS failure"), asyncio.CancelledError()):
            obj.wingman.tts.speak.side_effect = failure
            for action in (
                lambda: obj._deck.emote_feedback("Wave", True),
                lambda: obj._deck.feedback.deliver([obj._deck.manifest["buttons"]["gear"]]),
            ):
                with pytest.raises(type(failure)):
                    await action()
                assert not DECK_SPEECH.get() and not TABLET_COMMAND.get()
                assert not obj._deck.feedback.speaking
    asyncio.run(run())


@pytest.mark.parametrize("blocked", ["focus", "not_set_up"])
def test_silent_refusals_stay_out_of_hud(cockpit, blocked):
    obj, _, _ = cockpit
    button = obj._deck.manifest["buttons"]["gear"]
    if blocked == "focus":
        obj._deck.focus = lambda: False
    else:
        button["not_set_up"] = True
    asyncio.run(obj._deck._execute("tablet", "gear", button))
    obj.wingman.tts.speak.assert_not_awaited()
    obj.wingman.hud.show_message.assert_not_awaited()


def test_deck_speech_marker_isolated_from_concurrent_voice_task(cockpit):
    obj, events, _ = cockpit
    line = "Wave emote sent. Emotes only play on foot."
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def show(title, text, *, duration):
            assert DECK_SPEECH.get() and not TABLET_COMMAND.get()
            entered.set()
            await release.wait()
            events.append(("hud", title, text, duration))
            return True
        obj.wingman.hud.show_message.side_effect = show
        async def voice():
            await entered.wait()
            assert not DECK_SPEECH.get() and not TABLET_COMMAND.get()
            # Same text passes the real filler guard; provenance, not content,
            # must keep this independent voice task out of the HUD helper.
            await obj.wingman.tts.speak(line)
            release.set()
        await asyncio.gather(obj._deck.emote_feedback("Wave", True), voice())
    asyncio.run(run())
    obj.wingman.hud.show_message.assert_awaited_once_with("Pilot", line, duration=15)
    assert [e for e in events if e[0] == "spoken"] == [("spoken", line)] * 2
    assert not DECK_SPEECH.get() and not TABLET_COMMAND.get()
