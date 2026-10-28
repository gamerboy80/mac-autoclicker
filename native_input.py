from __future__ import annotations

import ctypes
import ctypes.util
import sys
import threading
from typing import Callable, Optional

from models import (
    KEYCODE_TO_NAME,
    KEY_NAME_TO_CODE,
    MODIFIER_KEYS,
    parse_key_shortcut,
)


KCG_EVENT_LEFT_MOUSE_DOWN = 1
KCG_EVENT_LEFT_MOUSE_UP = 2
KCG_EVENT_RIGHT_MOUSE_DOWN = 3
KCG_EVENT_RIGHT_MOUSE_UP = 4
KCG_EVENT_MOUSE_MOVED = 5
KCG_EVENT_LEFT_MOUSE_DRAGGED = 6
KCG_EVENT_RIGHT_MOUSE_DRAGGED = 7
KCG_EVENT_KEY_DOWN = 10
KCG_EVENT_KEY_UP = 11
KCG_EVENT_FLAGS_CHANGED = 12
KCG_EVENT_OTHER_MOUSE_DOWN = 25
KCG_EVENT_OTHER_MOUSE_UP = 26
KCG_EVENT_OTHER_MOUSE_DRAGGED = 27
KCG_EVENT_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
KCG_EVENT_TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF

KCG_HID_EVENT_TAP = 0
KCG_SESSION_EVENT_TAP = 1
KCG_HEAD_INSERT_EVENT_TAP = 0
KCG_EVENT_TAP_OPTION_LISTEN_ONLY = 1
KCG_MOUSE_EVENT_BUTTON_NUMBER = 3
KCG_KEYBOARD_EVENT_KEYCODE = 9
KCG_EVENT_SOURCE_USER_DATA = 42
GENERATED_EVENT_MARKER = 0x4D41434B

KCG_MOUSE_BUTTON_LEFT = 0
KCG_MOUSE_BUTTON_RIGHT = 1
KCG_MOUSE_BUTTON_CENTER = 2

KCG_EVENT_FLAG_MASK_SHIFT = 0x00020000
KCG_EVENT_FLAG_MASK_CONTROL = 0x00040000
KCG_EVENT_FLAG_MASK_ALTERNATE = 0x00080000
KCG_EVENT_FLAG_MASK_COMMAND = 0x00100000

MODIFIER_FLAG_MASKS = {
    "cmd": KCG_EVENT_FLAG_MASK_COMMAND,
    "ctrl": KCG_EVENT_FLAG_MASK_CONTROL,
    "alt": KCG_EVENT_FLAG_MASK_ALTERNATE,
    "shift": KCG_EVENT_FLAG_MASK_SHIFT,
}

class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


CGEventTapCallBack = ctypes.CFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_void_p,
)

class NativeInputError(RuntimeError):
    pass


def modifier_names_from_flags(flags: int) -> frozenset[str]:
    modifiers = set()
    if flags & KCG_EVENT_FLAG_MASK_COMMAND:
        modifiers.add("cmd")
    if flags & KCG_EVENT_FLAG_MASK_CONTROL:
        modifiers.add("ctrl")
    if flags & KCG_EVENT_FLAG_MASK_ALTERNATE:
        modifiers.add("alt")
    if flags & KCG_EVENT_FLAG_MASK_SHIFT:
        modifiers.add("shift")
    return frozenset(modifiers)


def modifier_flags_for_keys(keys) -> int:
    flags = 0
    for key in keys:
        flags |= MODIFIER_FLAG_MASKS.get(key, 0)
    return flags


class MacNativeInput:
    def __init__(self, event_callback: Callable[[dict], None]):
        if sys.platform != "darwin":
            raise NativeInputError("This app uses macOS CoreGraphics APIs.")

        self.event_callback = event_callback
        self.core_graphics = self._load_framework("CoreGraphics")
        self.core_foundation = self._load_framework("CoreFoundation")
        self._configure_core_graphics()
        self._configure_core_foundation()

        self._event_tap = None
        self._run_loop = None
        self._run_loop_source = None
        self._tap_thread: Optional[threading.Thread] = None
        self._tap_callback = CGEventTapCallBack(self._handle_tap_event)

    def _load_framework(self, name: str) -> ctypes.CDLL:
        path = ctypes.util.find_library(name)
        if not path:
            path = f"/System/Library/Frameworks/{name}.framework/{name}"
        try:
            return ctypes.CDLL(path)
        except OSError as exc:
            raise NativeInputError(f"Could not load {name}.framework.") from exc

    def _configure_core_graphics(self) -> None:
        cg = self.core_graphics

        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            CGPoint,
            ctypes.c_uint32,
        ]
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint16,
            ctypes.c_bool,
        ]
        cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        cg.CGEventSetFlags.restype = None
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGEventPost.restype = None
        cg.CGWarpMouseCursorPosition.argtypes = [CGPoint]
        cg.CGWarpMouseCursorPosition.restype = ctypes.c_int32
        cg.CGEventTapCreate.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint64,
            CGEventTapCallBack,
            ctypes.c_void_p,
        ]
        cg.CGEventTapCreate.restype = ctypes.c_void_p
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventTapEnable.restype = None
        cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cg.CGEventGetIntegerValueField.restype = ctypes.c_int64
        cg.CGEventSetIntegerValueField.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int64,
        ]
        cg.CGEventSetIntegerValueField.restype = None
        cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]
        cg.CGEventGetFlags.restype = ctypes.c_uint64

    def _configure_core_foundation(self) -> None:
        cf = self.core_foundation

        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFMachPortCreateRunLoopSource.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int64,
        ]
        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopAddSource.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cf.CFRunLoopAddSource.restype = None
        cf.CFRunLoopRun.argtypes = []
        cf.CFRunLoopRun.restype = None
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        cf.CFRunLoopStop.restype = None
        self._common_modes = ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes")

    @property
    def position(self) -> tuple[float, float]:
        event = self.core_graphics.CGEventCreate(None)
        if not event:
            return (0.0, 0.0)
        try:
            point = self.core_graphics.CGEventGetLocation(event)
            return (point.x, point.y)
        finally:
            self.core_foundation.CFRelease(event)

    @position.setter
    def position(self, xy: tuple[float, float]) -> None:
        x, y = xy
        result = self.core_graphics.CGWarpMouseCursorPosition(
            CGPoint(float(x), float(y))
        )
        if result != 0:
            raise NativeInputError(f"Could not move the cursor to {int(x)}, {int(y)}")

    @property
    def keyboard_events_running(self) -> bool:
        return bool(
            self._event_tap
            and self._tap_thread is not None
            and self._tap_thread.is_alive()
        )

    def start_keyboard_events(self) -> None:
        if self._tap_thread and self._tap_thread.is_alive():
            return

        self._tap_thread = threading.Thread(
            target=self._run_event_tap,
            name="mac-input-event-tap",
            daemon=True,
        )
        self._tap_thread.start()

    def stop_keyboard_events(self) -> None:
        if self._event_tap:
            self.core_graphics.CGEventTapEnable(self._event_tap, False)
        if self._run_loop:
            self.core_foundation.CFRunLoopStop(self._run_loop)

    def mouse_down(self, button_name: str) -> None:
        self._post_mouse_event(button_name, down=True)

    def mouse_up(self, button_name: str) -> None:
        self._post_mouse_event(button_name, down=False)

    def click(self, button_name: str) -> None:
        self.mouse_down(button_name)
        self.mouse_up(button_name)

    def key_down(self, key_name: str) -> None:
        self._post_key_event(key_name, down=True)

    def key_up(self, key_name: str) -> None:
        self._post_key_event(key_name, down=False)

    def tap_key(self, key_name: str) -> None:
        self.key_down(key_name)
        self.key_up(key_name)

    def _post_mouse_event(self, button_name: str, down: bool) -> None:
        button_id, down_type, up_type = {
            "right": (
                KCG_MOUSE_BUTTON_RIGHT,
                KCG_EVENT_RIGHT_MOUSE_DOWN,
                KCG_EVENT_RIGHT_MOUSE_UP,
            ),
            "middle": (
                KCG_MOUSE_BUTTON_CENTER,
                KCG_EVENT_OTHER_MOUSE_DOWN,
                KCG_EVENT_OTHER_MOUSE_UP,
            ),
        }.get(
            button_name,
            (
                KCG_MOUSE_BUTTON_LEFT,
                KCG_EVENT_LEFT_MOUSE_DOWN,
                KCG_EVENT_LEFT_MOUSE_UP,
            ),
        )

        x, y = self.position
        event_type = down_type if down else up_type
        event = self.core_graphics.CGEventCreateMouseEvent(
            None, event_type, CGPoint(x, y), button_id
        )
        if not event:
            action = "press" if down else "release"
            raise NativeInputError(f"Could not create mouse {action} event")
        try:
            self._mark_generated_event(event)
            self.core_graphics.CGEventPost(KCG_HID_EVENT_TAP, event)
        finally:
            self.core_foundation.CFRelease(event)

    def _post_key_event(self, key_name: str, down: bool) -> None:
        shortcut = parse_key_shortcut(key_name)
        if not shortcut:
            raise NativeInputError(f"Unsupported key: {key_name}")

        modifiers = tuple(key for key in shortcut if key in MODIFIER_KEYS)
        regular_keys = tuple(key for key in shortcut if key not in MODIFIER_KEYS)

        if down:
            active_modifiers = set()
            for key in modifiers:
                active_modifiers.add(key)
                self._post_single_key_event(
                    key, True, modifier_flags_for_keys(active_modifiers)
                )

            flags = modifier_flags_for_keys(modifiers)
            for key in regular_keys:
                self._post_single_key_event(key, True, flags)
            return

        flags = modifier_flags_for_keys(modifiers)
        for key in reversed(regular_keys):
            self._post_single_key_event(key, False, flags)

        active_modifiers = set(modifiers)
        for key in reversed(modifiers):
            active_modifiers.discard(key)
            self._post_single_key_event(
                key, False, modifier_flags_for_keys(active_modifiers)
            )

    def _post_single_key_event(self, key_name: str, down: bool, flags: int = 0) -> None:
        keycode = KEY_NAME_TO_CODE.get(key_name)
        if keycode is None:
            raise NativeInputError(f"Unsupported key: {key_name}")

        event = self.core_graphics.CGEventCreateKeyboardEvent(None, keycode, down)
        if not event:
            raise NativeInputError(f"Could not create key event for {key_name}")
        try:
            if flags:
                self.core_graphics.CGEventSetFlags(event, flags)
            self._mark_generated_event(event)
            self.core_graphics.CGEventPost(KCG_HID_EVENT_TAP, event)
        finally:
            self.core_foundation.CFRelease(event)

    def _mark_generated_event(self, event: ctypes.c_void_p) -> None:
        self.core_graphics.CGEventSetIntegerValueField(
            event,
            KCG_EVENT_SOURCE_USER_DATA,
            GENERATED_EVENT_MARKER,
        )

    def _run_event_tap(self) -> None:
        try:
            self._run_event_tap_unsafe()
        except Exception:
            self.event_callback({"kind": "error"})

    def _run_event_tap_unsafe(self) -> None:
        event_mask = (
            (1 << KCG_EVENT_KEY_DOWN)
            | (1 << KCG_EVENT_KEY_UP)
            | (1 << KCG_EVENT_FLAGS_CHANGED)
            | (1 << KCG_EVENT_LEFT_MOUSE_DOWN)
            | (1 << KCG_EVENT_LEFT_MOUSE_UP)
            | (1 << KCG_EVENT_RIGHT_MOUSE_DOWN)
            | (1 << KCG_EVENT_RIGHT_MOUSE_UP)
            | (1 << KCG_EVENT_OTHER_MOUSE_DOWN)
            | (1 << KCG_EVENT_OTHER_MOUSE_UP)
            | (1 << KCG_EVENT_MOUSE_MOVED)
            | (1 << KCG_EVENT_LEFT_MOUSE_DRAGGED)
            | (1 << KCG_EVENT_RIGHT_MOUSE_DRAGGED)
            | (1 << KCG_EVENT_OTHER_MOUSE_DRAGGED)
        )
        tap = self.core_graphics.CGEventTapCreate(
            KCG_SESSION_EVENT_TAP,
            KCG_HEAD_INSERT_EVENT_TAP,
            KCG_EVENT_TAP_OPTION_LISTEN_ONLY,
            event_mask,
            self._tap_callback,
            None,
        )

        if not tap:
            self.event_callback({"kind": "error"})
            return

        source = self.core_foundation.CFMachPortCreateRunLoopSource(None, tap, 0)
        if not source:
            self.core_foundation.CFRelease(tap)
            self.event_callback({"kind": "error"})
            return

        self._event_tap = tap
        self._run_loop_source = source
        self._run_loop = self.core_foundation.CFRunLoopGetCurrent()
        self.core_foundation.CFRunLoopAddSource(
            self._run_loop, source, self._common_modes
        )
        self.core_graphics.CGEventTapEnable(tap, True)
        self.core_foundation.CFRunLoopRun()

        self.core_foundation.CFRelease(source)
        self.core_foundation.CFRelease(tap)
        self._run_loop_source = None
        self._event_tap = None
        self._run_loop = None

    def _handle_tap_event(
        self,
        proxy: ctypes.c_void_p,
        event_type: int,
        event: ctypes.c_void_p,
        refcon: ctypes.c_void_p,
    ) -> ctypes.c_void_p:
        if event_type in (
            KCG_EVENT_TAP_DISABLED_BY_TIMEOUT,
            KCG_EVENT_TAP_DISABLED_BY_USER_INPUT,
        ):
            if self._event_tap:
                self.core_graphics.CGEventTapEnable(self._event_tap, True)
            return event

        if event_type not in (
            KCG_EVENT_KEY_DOWN,
            KCG_EVENT_KEY_UP,
            KCG_EVENT_FLAGS_CHANGED,
            KCG_EVENT_LEFT_MOUSE_DOWN,
            KCG_EVENT_LEFT_MOUSE_UP,
            KCG_EVENT_RIGHT_MOUSE_DOWN,
            KCG_EVENT_RIGHT_MOUSE_UP,
            KCG_EVENT_OTHER_MOUSE_DOWN,
            KCG_EVENT_OTHER_MOUSE_UP,
            KCG_EVENT_MOUSE_MOVED,
            KCG_EVENT_LEFT_MOUSE_DRAGGED,
            KCG_EVENT_RIGHT_MOUSE_DRAGGED,
            KCG_EVENT_OTHER_MOUSE_DRAGGED,
        ):
            return event

        try:
            if self._is_generated_event(event):
                return event

            if event_type in (
                KCG_EVENT_LEFT_MOUSE_DOWN,
                KCG_EVENT_LEFT_MOUSE_UP,
                KCG_EVENT_RIGHT_MOUSE_DOWN,
                KCG_EVENT_RIGHT_MOUSE_UP,
                KCG_EVENT_OTHER_MOUSE_DOWN,
                KCG_EVENT_OTHER_MOUSE_UP,
                KCG_EVENT_MOUSE_MOVED,
                KCG_EVENT_LEFT_MOUSE_DRAGGED,
                KCG_EVENT_RIGHT_MOUSE_DRAGGED,
                KCG_EVENT_OTHER_MOUSE_DRAGGED,
            ):
                self._handle_tap_mouse_event(event_type, event)
                return event

            flags = int(self.core_graphics.CGEventGetFlags(event))
            modifiers = modifier_names_from_flags(flags)

            if event_type == KCG_EVENT_FLAGS_CHANGED:
                self.event_callback(
                    {"kind": "flags", "modifiers": modifiers}
                )
                return event

            keycode = int(
                self.core_graphics.CGEventGetIntegerValueField(
                    event, KCG_KEYBOARD_EVENT_KEYCODE
                )
            )
            key_name = KEYCODE_TO_NAME.get(keycode)
            if not key_name:
                return event

            kind = "down" if event_type == KCG_EVENT_KEY_DOWN else "up"
            self.event_callback(
                {"kind": kind, "key": key_name, "modifiers": modifiers}
            )
        except Exception:
            return event

        return event

    def _is_generated_event(self, event: ctypes.c_void_p) -> bool:
        try:
            return (
                int(
                    self.core_graphics.CGEventGetIntegerValueField(
                        event,
                        KCG_EVENT_SOURCE_USER_DATA,
                    )
                )
                == GENERATED_EVENT_MARKER
            )
        except Exception:
            return False

    def _handle_tap_mouse_event(self, event_type: int, event: ctypes.c_void_p) -> None:
        point = self.core_graphics.CGEventGetLocation(event)
        button = {
            KCG_EVENT_LEFT_MOUSE_DOWN: "left",
            KCG_EVENT_LEFT_MOUSE_UP: "left",
            KCG_EVENT_LEFT_MOUSE_DRAGGED: "left",
            KCG_EVENT_RIGHT_MOUSE_DOWN: "right",
            KCG_EVENT_RIGHT_MOUSE_UP: "right",
            KCG_EVENT_RIGHT_MOUSE_DRAGGED: "right",
        }.get(event_type, "left")

        if event_type in (
            KCG_EVENT_OTHER_MOUSE_DOWN,
            KCG_EVENT_OTHER_MOUSE_UP,
            KCG_EVENT_OTHER_MOUSE_DRAGGED,
        ):
            button_number = int(
                self.core_graphics.CGEventGetIntegerValueField(
                    event, KCG_MOUSE_EVENT_BUTTON_NUMBER
                )
            )
            button = "middle" if button_number == KCG_MOUSE_BUTTON_CENTER else "left"

        if event_type in (
            KCG_EVENT_LEFT_MOUSE_DOWN,
            KCG_EVENT_RIGHT_MOUSE_DOWN,
            KCG_EVENT_OTHER_MOUSE_DOWN,
        ):
            kind = "mouse_down"
        elif event_type in (
            KCG_EVENT_LEFT_MOUSE_UP,
            KCG_EVENT_RIGHT_MOUSE_UP,
            KCG_EVENT_OTHER_MOUSE_UP,
        ):
            kind = "mouse_up"
        else:
            kind = "mouse_move"

        self.event_callback(
            {
                "kind": kind,
                "button": button,
                "x": point.x,
                "y": point.y,
            }
        )
