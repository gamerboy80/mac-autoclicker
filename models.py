from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from config import DEFAULT_EXIT_HOTKEY, DEFAULT_STOP_HOTKEY, DEFAULT_TOGGLE_HOTKEY


MOUSE_BUTTONS = ("left", "right", "middle")
OUTPUT_MODES = ("mouse", "keyboard")
MACRO_EVENT_TYPES = (
    "delay",
    "key_down",
    "key_up",
    "mouse_down",
    "mouse_up",
    "mouse_move",
)
MOVE_MODES = ("absolute", "relative")
MACRO_RUN_MODES = ("once", "repeat", "toggle", "hold")

MODIFIER_DISPLAY_ORDER = ("cmd", "ctrl", "alt", "shift")
MODIFIER_KEYS = frozenset(MODIFIER_DISPLAY_ORDER)
HOTKEY_SPLIT_RE = re.compile(r"\s*\+\s*")

KEY_ALIASES = {
    "alternate": "alt",
    "command": "cmd",
    "control": "ctrl",
    "escape": "esc",
    "meta": "cmd",
    "option": "alt",
    "return": "enter",
}

DISPLAY_NAMES = {
    "alt": "Option",
    "backspace": "Backspace",
    "cmd": "Cmd",
    "ctrl": "Ctrl",
    "delete": "Delete",
    "down": "Down",
    "end": "End",
    "enter": "Enter",
    "esc": "Esc",
    "home": "Home",
    "left": "Left",
    "page_down": "Page Down",
    "page_up": "Page Up",
    "right": "Right",
    "shift": "Shift",
    "space": "Space",
    "tab": "Tab",
    "up": "Up",
}

KEYCODE_TO_NAME = {
    0: "a",
    1: "s",
    2: "d",
    3: "f",
    4: "h",
    5: "g",
    6: "z",
    7: "x",
    8: "c",
    9: "v",
    11: "b",
    12: "q",
    13: "w",
    14: "e",
    15: "r",
    16: "y",
    17: "t",
    18: "1",
    19: "2",
    20: "3",
    21: "4",
    22: "6",
    23: "5",
    24: "=",
    25: "9",
    26: "7",
    27: "-",
    28: "8",
    29: "0",
    30: "]",
    31: "o",
    32: "u",
    33: "[",
    34: "i",
    35: "p",
    36: "enter",
    37: "l",
    38: "j",
    39: "'",
    40: "k",
    41: ";",
    42: "\\",
    43: ",",
    44: "/",
    45: "n",
    46: "m",
    47: ".",
    48: "tab",
    49: "space",
    50: "`",
    51: "backspace",
    53: "esc",
    64: "f17",
    79: "f18",
    80: "f19",
    90: "f20",
    96: "f5",
    97: "f6",
    98: "f7",
    99: "f3",
    100: "f8",
    101: "f9",
    103: "f11",
    105: "f13",
    106: "f16",
    107: "f14",
    109: "f10",
    111: "f12",
    113: "f15",
    115: "home",
    116: "page_up",
    118: "f4",
    119: "end",
    120: "f2",
    121: "page_down",
    122: "f1",
    123: "left",
    124: "right",
    125: "down",
    126: "up",
}

KEY_NAME_TO_CODE = {name: code for code, name in KEYCODE_TO_NAME.items()}
KEY_NAME_TO_CODE.update(
    {
        "cmd": 55,
        "shift": 56,
        "alt": 58,
        "ctrl": 59,
    }
)


@dataclass(frozen=True)
class ClickSettings:
    output_mode: str = "mouse"
    cps: float = 10.0
    mouse_button: str = "left"
    click_type: str = "single"
    key_name: str = "space"
    key_hold_seconds: float = 0.0
    position_mode: str = "follow"
    fixed_x: int = 0
    fixed_y: int = 0
    restore_position: bool = True
    start_delay: float = 0.0
    jitter_percent: float = 0.0
    max_clicks: int = 0
    stop_after_seconds: float = 0.0
    mouse_down_seconds: float = 0.0
    activation_mode: str = "toggle"
    toggle_hotkey: str = DEFAULT_TOGGLE_HOTKEY
    stop_hotkey: str = DEFAULT_STOP_HOTKEY
    exit_hotkey: str = DEFAULT_EXIT_HOTKEY


@dataclass(frozen=True)
class MacroDefinition:
    id: str
    name: str
    hotkey: str = ""
    run_mode: str = "once"
    repeat_count: int = 1
    events: list[dict] = field(default_factory=list)


def normalize_key_name(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "_")
    return KEY_ALIASES.get(cleaned, cleaned)


def parse_hotkey(text: str) -> frozenset[str]:
    cleaned = str(text).strip().lower()
    if cleaned in ("", "none", "unassigned"):
        return frozenset()
    parts = [part for part in HOTKEY_SPLIT_RE.split(cleaned) if part]
    return frozenset(normalize_key_name(part) for part in parts)


def sort_hotkey_parts(parts: frozenset[str]) -> list[str]:
    modifiers = [part for part in MODIFIER_DISPLAY_ORDER if part in parts]
    regular = sorted(part for part in parts if part not in MODIFIER_DISPLAY_ORDER)
    return modifiers + regular


def format_hotkey(parts: frozenset[str]) -> str:
    if not parts:
        return "Unassigned"

    labels = []
    for part in sort_hotkey_parts(parts):
        if part in DISPLAY_NAMES:
            labels.append(DISPLAY_NAMES[part])
        elif re.fullmatch(r"f\d{1,2}", part):
            labels.append(part.upper())
        elif len(part) == 1:
            labels.append(part.upper())
        else:
            labels.append(part.replace("_", " ").title())
    return "+".join(labels)


def has_regular_key(hotkey: frozenset[str]) -> bool:
    return any(part not in MODIFIER_KEYS for part in hotkey)


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def coerce_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def coerce_int(value, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def sanitize_key_name(value, fallback: str = "space") -> str:
    key_name = normalize_key_name(str(value))
    if key_name in KEY_NAME_TO_CODE:
        return key_name
    return fallback


def parse_key_shortcut(value) -> tuple[str, ...]:
    parts = parse_hotkey(value)
    if not parts:
        return ()

    ordered = sort_hotkey_parts(parts)
    if all(part in KEY_NAME_TO_CODE for part in ordered):
        return tuple(ordered)
    return ()


def format_key_shortcut(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return format_hotkey(frozenset(parts))


def sanitize_key_shortcut(value, fallback: str = "space") -> str:
    shortcut = parse_key_shortcut(value)
    if shortcut:
        return format_key_shortcut(shortcut)

    fallback_shortcut = parse_key_shortcut(fallback)
    if fallback_shortcut:
        return format_key_shortcut(fallback_shortcut)

    return "space"


def sanitize_hotkey(value, fallback: str = "", allow_empty: bool = True) -> str:
    hotkey = parse_hotkey(value)
    if not hotkey:
        return "" if allow_empty else fallback
    if has_regular_key(hotkey):
        return format_hotkey(hotkey)
    return fallback


def sanitize_macro_event(payload: dict) -> dict:
    kind = str(payload.get("kind", "delay")).strip().lower()
    if kind not in MACRO_EVENT_TYPES:
        kind = "delay"

    mouse_button = str(payload.get("mouse_button", "left")).lower()
    if mouse_button not in MOUSE_BUTTONS:
        mouse_button = "left"

    move_mode = str(payload.get("move_mode", "absolute")).lower()
    if move_mode not in MOVE_MODES:
        move_mode = "absolute"

    return {
        "kind": kind,
        "key": sanitize_key_shortcut(payload.get("key", "space")),
        "mouse_button": mouse_button,
        "x": coerce_int(payload.get("x", 0), 0),
        "y": coerce_int(payload.get("y", 0), 0),
        "move_mode": move_mode,
        "duration": clamp_float(coerce_float(payload.get("duration", 0.05), 0.05), 0.0, 3600.0),
    }


def default_macro_events() -> list[dict]:
    return [
        sanitize_macro_event({"kind": "key_down", "key": "space"}),
        sanitize_macro_event({"kind": "delay", "duration": 0.05}),
        sanitize_macro_event({"kind": "key_up", "key": "space"}),
    ]


def sanitize_click_settings(payload: dict, current: ClickSettings) -> ClickSettings:
    updates = {}

    if "output_mode" in payload and payload["output_mode"] in OUTPUT_MODES:
        updates["output_mode"] = payload["output_mode"]
    if "cps" in payload:
        updates["cps"] = clamp_float(coerce_float(payload["cps"], current.cps), 0.1, 250.0)
    if "mouse_button" in payload and payload["mouse_button"] in MOUSE_BUTTONS:
        updates["mouse_button"] = payload["mouse_button"]
    if "click_type" in payload and payload["click_type"] in ("single", "double"):
        updates["click_type"] = payload["click_type"]
    if "key_name" in payload:
        updates["key_name"] = sanitize_key_shortcut(payload["key_name"], current.key_name)
    if "key_hold_seconds" in payload:
        updates["key_hold_seconds"] = clamp_float(
            coerce_float(payload["key_hold_seconds"], current.key_hold_seconds),
            0.0,
            5.0,
        )
    if "position_mode" in payload and payload["position_mode"] in ("follow", "fixed"):
        updates["position_mode"] = payload["position_mode"]
    if "fixed_x" in payload:
        updates["fixed_x"] = coerce_int(payload["fixed_x"], current.fixed_x)
    if "fixed_y" in payload:
        updates["fixed_y"] = coerce_int(payload["fixed_y"], current.fixed_y)
    if "restore_position" in payload:
        updates["restore_position"] = bool(payload["restore_position"])
    if "start_delay" in payload:
        updates["start_delay"] = clamp_float(
            coerce_float(payload["start_delay"], current.start_delay), 0.0, 3600.0
        )
    if "jitter_percent" in payload:
        updates["jitter_percent"] = clamp_float(
            coerce_float(payload["jitter_percent"], current.jitter_percent), 0.0, 95.0
        )
    if "max_clicks" in payload:
        updates["max_clicks"] = max(0, coerce_int(payload["max_clicks"], current.max_clicks))
    if "stop_after_seconds" in payload:
        updates["stop_after_seconds"] = clamp_float(
            coerce_float(payload["stop_after_seconds"], current.stop_after_seconds),
            0.0,
            86400.0,
        )
    if "mouse_down_seconds" in payload:
        updates["mouse_down_seconds"] = clamp_float(
            coerce_float(payload["mouse_down_seconds"], current.mouse_down_seconds),
            0.0,
            5.0,
        )
    if "activation_mode" in payload and payload["activation_mode"] in ("toggle", "hold"):
        updates["activation_mode"] = payload["activation_mode"]
    if "toggle_hotkey" in payload:
        hotkey = parse_hotkey(payload["toggle_hotkey"])
        if has_regular_key(hotkey):
            updates["toggle_hotkey"] = format_hotkey(hotkey)
    if "stop_hotkey" in payload:
        hotkey = parse_hotkey(payload["stop_hotkey"])
        if has_regular_key(hotkey):
            updates["stop_hotkey"] = format_hotkey(hotkey)
    if "exit_hotkey" in payload:
        hotkey = parse_hotkey(payload["exit_hotkey"])
        if has_regular_key(hotkey):
            updates["exit_hotkey"] = format_hotkey(hotkey)

    return replace(current, **updates)


def sanitize_macro_definition(payload: dict, fallback_id: str) -> MacroDefinition:
    name = str(payload.get("name", fallback_id)).strip()[:64] or fallback_id
    raw_events = payload.get("events", default_macro_events())
    if not isinstance(raw_events, list):
        raw_events = default_macro_events()

    run_mode = str(payload.get("run_mode", "repeat")).strip().lower()
    if run_mode not in MACRO_RUN_MODES:
        run_mode = "repeat"

    return MacroDefinition(
        id=str(payload.get("id", fallback_id)).strip() or fallback_id,
        name=name,
        hotkey=sanitize_hotkey(payload.get("hotkey", ""), allow_empty=True),
        run_mode=run_mode,
        repeat_count=max(0, min(9999, coerce_int(payload.get("repeat_count", 1), 1))),
        events=[
            sanitize_macro_event(event if isinstance(event, dict) else {})
            for event in raw_events[:300]
        ],
    )
