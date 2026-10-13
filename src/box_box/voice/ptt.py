"""Push-to-talk: a wheel button (any DirectInput device: Moza, Fanatec, Logitech...), a keyboard key,
an in-game "UDP Action" binding, or the button in the web UI.

Wheels are read with SDL (via pygame) in background mode, so buttons register while F1 25 has focus.
All sources funnel into one `PushToTalk` that applies hold/toggle mode and calls `on_change(pressed)`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

UDP_ACTION_BASE = 0x00100000  # UDP Action 1; action N is this << (N - 1)


@dataclass
class Binding:
    kind: str  # "joystick" | "keyboard" | "udp_action" | "none"
    device_name: str = ""
    device_guid: str = ""
    button: int = -1
    key: str = ""
    udp_action: int = 1

    def describe(self) -> str:
        if self.kind == "joystick":
            return f"{self.device_name or 'controller'} button {self.button + 1}"
        if self.kind == "keyboard":
            return f"keyboard key {self.key.upper()}"
        if self.kind == "udp_action":
            return f"in-game UDP Action {self.udp_action}"
        return "not set"


# ---- joystick (wheel) ---------------------------------------------------------------------------


class JoystickReader:
    """Polls every connected controller's buttons on a background thread (200 Hz), with hot-plug."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, tuple[str, list[bool]]] = {}  # guid -> (name, buttons)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: str | None = None
        self.available = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="joystick", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def devices(self) -> list[dict]:
        with self._lock:
            return [{"guid": g, "name": n, "buttons": len(b)} for g, (n, b) in self._states.items()]

    def snapshot(self) -> dict[str, tuple[str, list[bool]]]:
        with self._lock:
            return {g: (n, list(b)) for g, (n, b) in self._states.items()}

    def pressed(self, guid: str, name: str, button: int) -> bool:
        with self._lock:
            state = self._states.get(guid)
            if state is None:  # GUIDs can change between USB ports; fall back to the name
                state = next((s for s in self._states.values() if s[0] == name), None)
            if state is None or not (0 <= button < len(state[1])):
                return False
            return state[1][button]

    def _run(self) -> None:
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        if os.name != "nt":
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # headless Linux/macOS (tests, CI)
        try:
            import pygame

            pygame.display.init()  # needed for the event pump; no window is created
            pygame.joystick.init()
            self.available = True
            self.error = None
        except Exception as e:  # noqa: BLE001
            self.error = f"controller support unavailable: {e}"
            log.error(self.error)
            return
        joysticks: dict[int, object] = {}

        def rescan() -> None:
            joysticks.clear()
            for i in range(pygame.joystick.get_count()):
                try:
                    js = pygame.joystick.Joystick(i)
                    js.init()
                    joysticks[js.get_instance_id()] = js
                except pygame.error as e:  # noqa: PERF203
                    log.debug("joystick %d: %s", i, e)
            names = [j.get_name() for j in joysticks.values()]  # type: ignore[attr-defined]
            log.info("controllers: %s", ", ".join(names) or "none")

        rescan()
        while not self._stop.is_set():
            try:
                for event in pygame.event.get():
                    if event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                        rescan()
                states = {}
                for js in joysticks.values():
                    guid = js.get_guid()  # type: ignore[attr-defined]
                    states[guid] = (js.get_name(), [bool(js.get_button(b)) for b in range(js.get_numbuttons())])  # type: ignore[attr-defined]
                with self._lock:
                    self._states = states
            except Exception as e:  # noqa: BLE001
                log.warning("controller polling error: %s", e)
                time.sleep(1)
                rescan()
            time.sleep(0.005)


# ---- keyboard -----------------------------------------------------------------------------------


def key_name(key) -> str:  # type: ignore[no-untyped-def]
    """Normalise a pynput key to a stable string: 'f13', 'space', 'ctrl_r', 'a'."""
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    name = getattr(key, "name", None)
    if name:
        return name
    vk = getattr(key, "vk", None)
    return f"vk{vk}" if vk is not None else str(key)


class KeyboardReader:
    def __init__(self) -> None:
        self._down: set[str] = set()
        self._by_vk: dict[int, str] = {}  # physical key -> the name it was pressed as
        self._lock = threading.Lock()
        self._listener = None
        self.error: str | None = None
        self.last_pressed: str | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        try:
            from pynput import keyboard

            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:  # noqa: BLE001
            self.error = f"keyboard hotkeys unavailable: {e}"
            log.warning(self.error)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _press(self, key) -> None:  # type: ignore[no-untyped-def]
        name = key_name(key)
        with self._lock:
            self._down.add(name)
            self.last_pressed = name
            vk = getattr(key, "vk", None)
            if vk is not None:
                self._by_vk[vk] = name

    def _release(self, key) -> None:  # type: ignore[no-untyped-def]
        with self._lock:
            self._down.discard(key_name(key))
            # Released with a different modifier held ("7" pressed, "&" released): match the physical key.
            vk = getattr(key, "vk", None)
            pressed_as = self._by_vk.pop(vk, None) if vk is not None else None
            if pressed_as is not None:
                self._down.discard(pressed_as)

    def pressed(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._down


# ---- the push-to-talk service ---------------------------------------------------------------------


class PushToTalk:
    """Merges all sources, applies hold/toggle, and reports changes via `on_change(pressed)`."""

    def __init__(self, on_change: Callable[[bool], None]) -> None:
        self.on_change = on_change
        self.joystick = JoystickReader()
        self.keyboard = KeyboardReader()
        self.binding = Binding("none")
        self.mode = "hold"
        self._ui_down = False
        self._ui_held = False  # the current press came from the web UI's talk button (always hold-to-talk)
        self._udp_buttons = 0
        self._udp_at = 0.0
        self._udp_repeats = False  # does the game resend BUTN while a button is held?
        self._raw = False
        self.active = False
        self._stuck = False  # ignore the input until it reads released (see force_release)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._learn_cancel = threading.Event()

    def configure(self, binding: Binding, mode: str) -> None:
        self.binding, self.mode = binding, mode
        if binding.kind == "joystick":
            self.joystick.start()
        if binding.kind == "keyboard":
            self.keyboard.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ptt", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.joystick.stop()
        self.keyboard.stop()

    # inputs from elsewhere
    def ui_button(self, down: bool) -> None:
        self._ui_down = down

    def game_buttons(self, mask: int) -> None:
        """From the game's BUTN event. Some game versions resend it while a button is held, others only
        when the buttons change; learn which, so hold-to-talk works either way."""
        now = time.monotonic()
        if mask and mask == self._udp_buttons and now - self._udp_at < 1.0:
            self._udp_repeats = True
        self._udp_buttons = mask
        self._udp_at = now

    def _raw_pressed(self) -> bool:
        b = self.binding
        if self._ui_down:
            return True
        if b.kind == "joystick":
            return self.joystick.pressed(b.device_guid, b.device_name, b.button)
        if b.kind == "keyboard":
            return bool(b.key) and self.keyboard.pressed(b.key)
        if b.kind == "udp_action":
            bit = UDP_ACTION_BASE << (b.udp_action - 1)
            if not self._udp_buttons & bit:
                return False
            # If the game repeats BUTN while held, silence means released (a lost release packet can't
            # leave the radio keyed). If it only reports changes, the bit stays set until the release.
            return not self._udp_repeats or time.monotonic() - self._udp_at < 0.7
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._raw_pressed()
                if self._stuck:
                    if raw:
                        time.sleep(0.05)
                        continue
                    self._stuck = False
                    self._raw = False
                if raw != self._raw:
                    self._raw = raw
                    via_ui = self._ui_down or self._ui_held  # the UI button's release must release too
                    self._ui_held = self._ui_down and raw
                    if self.mode == "toggle" and self.binding.kind != "none" and not via_ui:
                        if raw:  # toggle on press only
                            self._set(not self.active)
                    else:
                        self._set(raw)
            except Exception:  # noqa: BLE001
                log.exception("push-to-talk polling failed")
                time.sleep(0.5)
            time.sleep(0.005)

    def force_release(self) -> None:
        """The engine decided the button is stuck: release, and ignore it until it actually lets go."""
        self._stuck = self._raw
        self.active = False

    def _set(self, active: bool) -> None:
        if active == self.active:
            return
        self.active = active
        try:
            self.on_change(active)
        except Exception:  # noqa: BLE001
            log.exception("push-to-talk handler failed")

    def _released_within(self, guid: str, name: str, button: int, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not self.joystick.pressed(guid, name, button):
                return True
            time.sleep(0.01)
        return False

    # learning a new binding
    def cancel_learn(self) -> None:
        self._learn_cancel.set()

    def learn(self, timeout: float = 15.0) -> Binding | None:
        """Wait for the driver to press a wheel button or a key; return the binding. Blocking.
        Returns None on timeout, on Esc, or when cancel_learn() is called."""
        self._learn_cancel.clear()
        self.joystick.start()
        self.keyboard.start()
        time.sleep(0.3)  # let the reader see the devices
        baseline = self.joystick.snapshot()
        self.keyboard.last_pressed = None
        deadline = time.monotonic() + timeout
        latched: set[tuple[str, int]] = set()  # switches that stayed on: never a push-to-talk button
        while time.monotonic() < deadline and not self._learn_cancel.is_set():
            for guid, (name, buttons) in self.joystick.snapshot().items():
                # Buttons already held when learning started don't count until they're released.
                held = baseline.get(guid, (name, []))[1]
                for i, down in enumerate(buttons):
                    if down and not (i < len(held) and held[i]) and (guid, i) not in latched:
                        # A real button is let go again. Rotary encoders and mode switches (common on
                        # Moza and Fanatec wheels) stay "pressed": reject those, or the radio would be
                        # keyed forever and the engineer would never talk.
                        if self._released_within(guid, name, i, 3.0):
                            return Binding("joystick", device_name=name, device_guid=guid, button=i)
                        log.info("ignoring %s button %d while learning: it stays on (a switch?)", name, i)
                        latched.add((guid, i))
                baseline[guid] = (name, [i < len(held) and held[i] and d for i, d in enumerate(buttons)])
            key = self.keyboard.last_pressed
            if key == "esc":
                return None
            if key:
                return Binding("keyboard", key=key)
            time.sleep(0.01)
        return None
