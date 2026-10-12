import threading
import time
from types import SimpleNamespace

import numpy as np

from box_box.voice.mic import PRE_ROLL_S, SAMPLE_RATE, Microphone
from box_box.voice.ptt import Binding, PushToTalk
from box_box.voice.stt import SpeechToText


def test_microphone_keeps_a_pre_roll() -> None:
    mic = Microphone()
    block = np.full(800, 0.1, dtype=np.float32)  # 50 ms
    for _ in range(40):  # 2 s before the button
        mic.feed(block)
    mic.start_recording()
    for _ in range(10):  # 0.5 s while held
        mic.feed(block)
    audio = mic.stop_recording()
    seconds = len(audio) / SAMPLE_RATE
    assert PRE_ROLL_S + 0.5 <= seconds <= PRE_ROLL_S + 0.5 + 0.06
    assert not mic.recording


class FakeWhisper:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, audio, **kw):  # type: ignore[no-untyped-def]
        self.calls += 1
        assert kw["language"] == "en" and "Box" in kw["initial_prompt"]
        return [SimpleNamespace(text=" " + self.text)], None


def test_stt_skips_silence_and_filters_hallucinations() -> None:
    stt = SpeechToText()
    stt._model = FakeWhisper("What's the gap to Leclerc?")
    speech = (np.sin(np.arange(SAMPLE_RATE) / 5) * 0.2).astype(np.float32)
    assert stt.transcribe(speech).text == "What's the gap to Leclerc?"
    assert stt.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32)).text == ""
    assert stt._model.calls == 1  # silence never reached the model
    stt._model = FakeWhisper("Thank you.")
    assert stt.transcribe(speech).text == ""


def test_learn_picks_up_a_newly_pressed_wheel_button() -> None:
    ptt = PushToTalk(lambda pressed: None)
    states = {"g1": ("MOZA R9 Base", [False, True, False, False])}  # button 1 already held: must be ignored

    ptt.joystick.start = lambda: None  # type: ignore[method-assign]
    ptt.keyboard.start = lambda: None  # type: ignore[method-assign]
    ptt.joystick.snapshot = lambda: {g: (n, list(b)) for g, (n, b) in states.items()}  # type: ignore[method-assign]

    def press_later() -> None:
        time.sleep(0.5)
        states["g1"] = ("MOZA R9 Base", [False, True, False, True])

    threading.Thread(target=press_later).start()
    binding = ptt.learn(timeout=3)
    assert binding == Binding("joystick", device_name="MOZA R9 Base", device_guid="g1", button=3)
    assert binding.describe() == "MOZA R9 Base button 4"


def test_learn_accepts_a_keyboard_key() -> None:
    ptt = PushToTalk(lambda pressed: None)
    ptt.joystick.start = lambda: None  # type: ignore[method-assign]
    ptt.keyboard.start = lambda: None  # type: ignore[method-assign]
    ptt.joystick.snapshot = lambda: {}  # type: ignore[method-assign]

    def press_later() -> None:
        time.sleep(0.4)
        ptt.keyboard.last_pressed = "f13"

    threading.Thread(target=press_later).start()
    assert ptt.learn(timeout=3) == Binding("keyboard", key="f13")


def test_wheel_button_drives_push_to_talk() -> None:
    events: list[bool] = []
    ptt = PushToTalk(events.append)
    held = {"v": False}
    ptt.joystick.start = lambda: None  # type: ignore[method-assign]
    ptt.joystick.pressed = lambda guid, name, button: held["v"] and button == 3  # type: ignore[method-assign]
    ptt.configure(Binding("joystick", device_name="MOZA", device_guid="g", button=3), "hold")
    ptt.start()
    try:
        held["v"] = True
        time.sleep(0.05)
        held["v"] = False
        time.sleep(0.05)
    finally:
        ptt.stop()
    assert events == [True, False]


def test_learning_skips_switches_that_stay_on() -> None:
    import threading
    import time as _t

    from box_box.voice.ptt import PushToTalk

    ptt = PushToTalk(lambda pressed: None)
    states = {"g": ("MOZA R9", [False] * 8)}
    ptt.joystick.start = lambda: None  # type: ignore[method-assign]
    ptt.keyboard.start = lambda: None  # type: ignore[method-assign]
    ptt.joystick.snapshot = lambda: {k: (n, list(b)) for k, (n, b) in states.items()}  # type: ignore[method-assign]
    ptt.joystick.pressed = lambda guid, name, button: states["g"][1][button]  # type: ignore[method-assign]

    def driver() -> None:
        _t.sleep(0.4)
        states["g"][1][5] = True  # a rotary switch clicks on and stays on
        _t.sleep(3.5)
        states["g"][1][2] = True  # then the real button: pressed...
        _t.sleep(0.2)
        states["g"][1][2] = False  # ...and released

    threading.Thread(target=driver, daemon=True).start()
    binding = ptt.learn(timeout=8)
    assert binding is not None and binding.button == 2
