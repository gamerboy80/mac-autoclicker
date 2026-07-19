from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import asdict, replace
from typing import Callable, Optional

from config import CONFIG_PATH, CONFIG_VERSION, DEFAULT_MACRO_HOTKEY
from models import (
    MACRO_RUN_MODES,
    MODIFIER_DISPLAY_ORDER,
    MODIFIER_KEYS,
    ClickSettings,
    MacroDefinition,
    clamp_float,
    coerce_float,
    coerce_int,
    default_macro_events,
    format_hotkey,
    parse_hotkey,
    sanitize_click_settings,
    sanitize_hotkey,
    sanitize_key_name,
    sanitize_key_shortcut,
    sanitize_macro_definition,
    sanitize_macro_event,
)
from native_input import MacNativeInput, NativeInputError


class AutoClickerService:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.native_lock = threading.Lock()
        self.hotkey_lock = threading.Lock()
        self.settings = ClickSettings()
        self.running = False
        self.clicks_sent = 0
        self.status = "Stopped"
        self.message = "Ready"
        self.global_hotkeys_enabled = False
        self.global_hotkey_status = "Off"
        self.shutdown_callback: Optional[Callable[[], None]] = None
        self.stop_event: Optional[threading.Event] = None
        self.click_thread: Optional[threading.Thread] = None
        self.native: Optional[MacNativeInput] = None
        self.pressed_keys: set[str] = set()
        self.fired_combos: set[frozenset[str]] = set()
        self.macros = [
            MacroDefinition(
                id="macro-1",
                name="Space Tap",
                hotkey=DEFAULT_MACRO_HOTKEY,
                events=default_macro_events(),
            )
        ]
        self.next_macro_number = 2
        self.macro_stop_events: dict[str, threading.Event] = {}
        self.macro_threads: dict[str, threading.Thread] = {}
        self.hotkey_debounce_seconds = 0.35
        self.last_hotkey_action_at = -self.hotkey_debounce_seconds
        self.position_pick_active = False
        self.position_pick_token = 0
        self.position_pick_result: Optional[dict] = None
        self.recording_active = False
        self.recording_start_at = 0.0
        self.recording_events: list[dict] = []
        self.recording_last_event_at = 0.0
        self.recording_last_move_at = 0.0
        self.recording_last_move_position: Optional[tuple[int, int]] = None
        self.recording_modifier_keys: frozenset[str] = frozenset()
        self.recording_keys_down: set[str] = set()
        self.recording_mouse_buttons_down: set[str] = set()
        self.recording_delay_seconds = 3.0
        self.recording_max_events = 300
        self.last_recorded_macro_id = ""
        self._load_config()

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self.shutdown_callback = callback

    def _load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return

        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            with self.lock:
                self.message = f"Could not load saved settings: {exc}"
            return

        if not isinstance(payload, dict):
            return

        with self.lock:
            settings_payload = payload.get("settings", {})
            if isinstance(settings_payload, dict):
                self.settings = sanitize_click_settings(settings_payload, self.settings)

            macros_payload = payload.get("macros", [])
            if isinstance(macros_payload, list):
                macros = self._sanitize_config_macros_locked(macros_payload)
                if macros:
                    self.macros = macros
                    self._refresh_next_macro_number_locked()

            self.message = "Saved settings loaded"

    def _sanitize_config_macros_locked(self, payload: list) -> list[MacroDefinition]:
        macros = []
        used_ids = set()
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                item = {}

            macro = sanitize_macro_definition(item, fallback_id=f"macro-{index}")
            if macro.id in used_ids:
                macro = replace(macro, id=f"macro-{index}")
            while macro.id in used_ids:
                macro = replace(macro, id=f"{macro.id}-{index}")

            used_ids.add(macro.id)
            macros.append(macro)

        return macros

    def _refresh_next_macro_number_locked(self) -> None:
        next_number = 1
        for macro in self.macros:
            match = re.fullmatch(r"macro-(\d+)", macro.id)
            if match:
                next_number = max(next_number, int(match.group(1)) + 1)
        self.next_macro_number = next_number

    def _config_payload_locked(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "settings": asdict(self.settings),
            "macros": [asdict(macro) for macro in self.macros],
        }

    def _save_config_locked(self) -> None:
        payload = self._config_payload_locked()
        temp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(CONFIG_PATH)
        except OSError as exc:
            self.message = f"Could not save settings: {exc}"

    def _get_native(self) -> Optional[MacNativeInput]:
        with self.native_lock:
            if self.native is not None:
                return self.native

            try:
                self.native = MacNativeInput(self._on_native_input_event)
            except NativeInputError as exc:
                with self.lock:
                    self.message = str(exc)
                return None

            return self.native

    def _position_pick_state_locked(self) -> dict:
        return {
            "active": self.position_pick_active,
            "token": self.position_pick_token,
            "result": dict(self.position_pick_result) if self.position_pick_result else None,
        }

    def _recorder_state_locked(self) -> dict:
        now = time.monotonic()
        remaining = max(0.0, self.recording_start_at - now)
        if not self.recording_active:
            phase = "idle"
        elif remaining > 0:
            phase = "countdown"
        else:
            phase = "recording"

        return {
            "active": self.recording_active,
            "phase": phase,
            "remaining": round(remaining, 1),
            "event_count": len(self.recording_events),
            "delay_seconds": self.recording_delay_seconds,
            "max_events": self.recording_max_events,
        }

    def state(self) -> dict:
        with self.lock:
            settings = asdict(self.settings)
            settings["toggle_hotkey"] = format_hotkey(parse_hotkey(self.settings.toggle_hotkey))
            settings["exit_hotkey"] = format_hotkey(parse_hotkey(self.settings.exit_hotkey))
            settings["key_name"] = sanitize_key_shortcut(self.settings.key_name)
            macros = []
            for macro in self.macros:
                macro_payload = asdict(macro)
                macro_hotkey = parse_hotkey(macro.hotkey)
                macro_payload["hotkey"] = (
                    format_hotkey(macro_hotkey) if macro_hotkey else "Unassigned"
                )
                macros.append(macro_payload)
            return {
                "settings": settings,
                "running": self.running,
                "clicks_sent": self.clicks_sent,
                "status": self.status,
                "message": self.message,
                "global_hotkeys_enabled": self.global_hotkeys_enabled,
                "global_hotkey_status": self.global_hotkey_status,
                "macros": macros,
                "running_macros": sorted(self.macro_stop_events),
                "position_pick": self._position_pick_state_locked(),
                "recorder": self._recorder_state_locked(),
                "last_recorded_macro_id": self.last_recorded_macro_id,
            }

    def update_settings(self, payload: dict) -> dict:
        with self.lock:
            self.settings = sanitize_click_settings(payload, self.settings)
            self.message = "Settings saved"
            self._save_config_locked()

        return self.state()

    def start(self) -> dict:
        with self.lock:
            if self.running:
                return self.state()

            self.running = True
            self.clicks_sent = 0
            self.status = "Running"
            self.message = "Starting clicker"
            self.stop_event = threading.Event()
            stop_event = self.stop_event

        self.click_thread = threading.Thread(
            target=self._click_loop,
            args=(stop_event,),
            daemon=True,
            name="click-loop",
        )
        self.click_thread.start()
        self._ensure_global_hotkeys_started()
        return self.state()

    def stop(self, message: str = "Stopped") -> dict:
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
            self.running = False
            self.status = "Stopped"
            self.message = message
        return self.state()

    def toggle(self) -> dict:
        with self.lock:
            running = self.running
        if running:
            return self.stop()
        return self.start()

    def toggle_from_hotkey(self) -> dict:
        if not self._accept_hotkey_action():
            return self.state()
        return self.toggle()

    def create_macro(self) -> dict:
        with self.lock:
            macro_number = self.next_macro_number
            self.next_macro_number += 1
            macro = MacroDefinition(
                id=f"macro-{macro_number}",
                name=f"Macro {macro_number}",
                events=default_macro_events(),
            )
            self.macros.append(macro)
            self.message = f"{macro.name} created"
            self._save_config_locked()
        return self.state()

    def update_macro(self, payload: dict) -> dict:
        macro_id = str(payload.get("id", ""))
        with self.lock:
            for index, current in enumerate(self.macros):
                if current.id != macro_id:
                    continue

                raw_events = payload.get("events", current.events)
                if not isinstance(raw_events, list):
                    raw_events = current.events

                name = str(payload.get("name", current.name)).strip()[:64]
                hotkey = sanitize_hotkey(
                    payload.get("hotkey", current.hotkey),
                    fallback=current.hotkey,
                    allow_empty=True,
                )
                run_mode = str(payload.get("run_mode", current.run_mode)).strip().lower()
                if run_mode not in MACRO_RUN_MODES:
                    run_mode = current.run_mode
                updated = replace(
                    current,
                    name=name or current.name,
                    hotkey=hotkey,
                    run_mode=run_mode,
                    repeat_count=max(
                        0,
                        min(
                            9999,
                            coerce_int(
                                payload.get("repeat_count", current.repeat_count),
                                current.repeat_count,
                            ),
                        ),
                    ),
                    events=[
                        sanitize_macro_event(event if isinstance(event, dict) else {})
                        for event in raw_events[:300]
                    ],
                )
                self.macros[index] = updated
                self.message = f"{updated.name} saved"
                self._save_config_locked()
                break
            else:
                self.message = "Macro not found"
        return self.state()

    def delete_macro(self, macro_id: str) -> dict:
        macro_id = str(macro_id)
        with self.lock:
            if macro_id in self.macro_stop_events:
                self.macro_stop_events[macro_id].set()
            before = len(self.macros)
            self.macros = [macro for macro in self.macros if macro.id != macro_id]
            deleted = len(self.macros) < before
            if not self.macros:
                self.macros.append(
                    MacroDefinition(
                        id=f"macro-{self.next_macro_number}",
                        name=f"Macro {self.next_macro_number}",
                        events=default_macro_events(),
                    )
                )
                self.next_macro_number += 1
            self.message = "Macro deleted" if deleted else "Macro not found"
            if deleted:
                self._save_config_locked()
        return self.state()

    def duplicate_macro(self, macro_id: str) -> dict:
        macro_id = str(macro_id)
        with self.lock:
            source = next((item for item in self.macros if item.id == macro_id), None)
            if source is None:
                self.message = "Macro not found"
                return self.state()

            macro_number = self.next_macro_number
            self.next_macro_number += 1
            macro = MacroDefinition(
                id=f"macro-{macro_number}",
                name=f"{source.name} Copy"[:64],
                hotkey="",
                run_mode=source.run_mode,
                repeat_count=source.repeat_count,
                events=[dict(event) for event in source.events],
            )
            self.macros.append(macro)
            self.last_recorded_macro_id = macro.id
            self.message = f"{source.name} duplicated"
            self._save_config_locked()
        return self.state()

    def _macro_repeat_count_for_mode(self, macro: MacroDefinition) -> int:
        if macro.run_mode == "once":
            return 1
        if macro.run_mode in ("toggle", "hold"):
            return 0
        return macro.repeat_count

    def run_macro(self, macro_id: str) -> dict:
        macro_id = str(macro_id)
        with self.lock:
            macro = next((item for item in self.macros if item.id == macro_id), None)
            if macro is None:
                self.message = "Macro not found"
                return self.state()
            if macro.id in self.macro_stop_events:
                self.message = f"{macro.name} is already running"
                return self.state()

            macro_snapshot = replace(
                macro,
                repeat_count=self._macro_repeat_count_for_mode(macro),
                events=[dict(event) for event in macro.events],
            )
            stop_event = threading.Event()
            self.macro_stop_events[macro.id] = stop_event
            self.message = f"Running {macro.name}"

        thread = threading.Thread(
            target=self._macro_loop,
            args=(macro_snapshot, stop_event),
            daemon=True,
            name=f"macro-{macro_snapshot.id}",
        )
        with self.lock:
            self.macro_threads[macro_snapshot.id] = thread
        thread.start()
        self._ensure_global_hotkeys_started()
        return self.state()

    def run_macro_from_hotkey(self, macro_id: str) -> dict:
        macro_id = str(macro_id)
        with self.lock:
            macro = next((item for item in self.macros if item.id == macro_id), None)
            if macro is None:
                self.message = "Macro not found"
                return self.state()

            if macro.run_mode == "toggle" and macro.id in self.macro_stop_events:
                self.macro_stop_events[macro.id].set()
                self.message = f"Stopping {macro.name}"
                return self.state()

        return self.run_macro(macro_id)

    def stop_macro(self, macro_id: str) -> dict:
        macro_id = str(macro_id)
        with self.lock:
            stop_event = self.macro_stop_events.get(macro_id)
            if stop_event is None:
                self.message = "Macro is not running"
            else:
                stop_event.set()
                self.message = "Stopping macro"
        return self.state()

    def stop_all_macros(self, message: str = "Macros stopped") -> dict:
        with self.lock:
            for stop_event in list(self.macro_stop_events.values()):
                stop_event.set()
            self.message = message
        return self.state()

    def _accept_hotkey_action(self) -> bool:
        with self.lock:
            now = time.monotonic()
            if now - self.last_hotkey_action_at < self.hotkey_debounce_seconds:
                return False
            self.last_hotkey_action_at = now
            return True

    def read_cursor_position(self) -> dict:
        backend = self._get_native()
        if backend is None:
            return self.state()

        x, y = backend.position
        self.update_settings({"fixed_x": int(x), "fixed_y": int(y), "position_mode": "fixed"})
        with self.lock:
            self.message = f"Fixed point set to {int(x)}, {int(y)}"
        return self.state()

    def start_position_pick(self) -> dict:
        backend = self._get_native()
        if backend is None:
            return self.state()

        self._ensure_global_hotkeys_started(force=True)
        with self.lock:
            self.position_pick_token += 1
            self.position_pick_active = True
            self.position_pick_result = None
            self.message = "Click anywhere on screen to pick the macro move point"
        return self.state()

    def cancel_position_pick(self) -> dict:
        with self.lock:
            self.position_pick_active = False
            self.message = "Position picker cancelled"
        return self.state()

    def clear_position_pick(self) -> dict:
        with self.lock:
            self.position_pick_result = None
        return self.state()

    def start_macro_recording(self, delay_seconds: float = 3.0) -> dict:
        backend = self._get_native()
        if backend is None:
            return self.state()

        delay = clamp_float(coerce_float(delay_seconds, 3.0), 0.0, 30.0)
        self._ensure_global_hotkeys_started(force=True)
        with self.lock:
            if self.recording_active:
                self.message = "Macro recording is already active"
                return self.state()

            now = time.monotonic()
            self.recording_active = True
            self.recording_start_at = now + delay
            self.recording_events = []
            self.recording_last_event_at = self.recording_start_at
            self.recording_last_move_at = 0.0
            self.recording_last_move_position = None
            self.recording_modifier_keys = frozenset()
            self.recording_keys_down = set()
            self.recording_mouse_buttons_down = set()
            self.last_recorded_macro_id = ""
            self.message = f"Macro recording starts in {delay:g} seconds"
        return self.state()

    def stop_macro_recording(self, message: str = "Macro recording stopped") -> dict:
        with self.lock:
            if not self.recording_active:
                self.message = "Macro recorder is not active"
                return self.state()

            if time.monotonic() >= self.recording_start_at:
                now = time.monotonic()
                for key in sorted(self.recording_keys_down):
                    self._append_recorded_event_locked({"kind": "key_up", "key": key}, now)
                for button in sorted(self.recording_mouse_buttons_down):
                    self._append_recorded_event_locked(
                        {"kind": "mouse_up", "mouse_button": button},
                        now,
                    )

            events = [dict(event) for event in self.recording_events]
            self.recording_active = False
            self.recording_start_at = 0.0
            self.recording_events = []
            self.recording_last_event_at = 0.0
            self.recording_last_move_at = 0.0
            self.recording_last_move_position = None
            self.recording_modifier_keys = frozenset()
            self.recording_keys_down = set()
            self.recording_mouse_buttons_down = set()

            if not events:
                self.last_recorded_macro_id = ""
                self.message = "Recording discarded; no events captured"
                return self.state()

            macro_number = self.next_macro_number
            self.next_macro_number += 1
            macro = MacroDefinition(
                id=f"macro-{macro_number}",
                name=f"Recorded Macro {macro_number}",
                events=events[: self.recording_max_events],
            )
            self.macros.append(macro)
            self.last_recorded_macro_id = macro.id
            self.message = f"{message}; created {macro.name}"
            self._save_config_locked()
        return self.state()

    def enable_global_hotkeys(self) -> dict:
        self._ensure_global_hotkeys_started(force=True)
        return self.state()

    def _ensure_global_hotkeys_started(self, force: bool = False) -> None:
        with self.lock:
            if self.global_hotkey_status in ("On", "Starting"):
                return
            if self.global_hotkey_status == "Blocked" and not force:
                return
            self.global_hotkeys_enabled = True
            self.global_hotkey_status = "Starting"
            self.message = "Starting global hotkeys"

        threading.Thread(
            target=self._start_global_hotkeys_worker,
            daemon=True,
            name="global-hotkey-starter",
        ).start()

    def disable_global_hotkeys(self) -> dict:
        backend = self.native
        if backend:
            backend.stop_keyboard_events()
        with self.lock:
            self.global_hotkeys_enabled = False
            self.global_hotkey_status = "Off"
            self.message = "Global hotkeys off"
        return self.state()

    def _start_global_hotkeys_worker(self) -> None:
        backend = self._get_native()
        if backend is None:
            with self.lock:
                self.global_hotkeys_enabled = False
                self.global_hotkey_status = "Blocked"
                self.message = "macOS blocked native input access"
            return

        backend.start_keyboard_events()
        time.sleep(0.5)
        with self.lock:
            if backend.keyboard_events_running:
                self.global_hotkey_status = "On"
                self.message = "Global hotkeys on"
            else:
                self.global_hotkeys_enabled = False
                self.global_hotkey_status = "Blocked"
                self.message = "Enable Input Monitoring and Accessibility for Terminal/Python"

    def _settings_snapshot(self) -> ClickSettings:
        with self.lock:
            return self.settings

    def _set_finished(self, message: str, count: int) -> None:
        with self.lock:
            self.running = False
            self.status = "Stopped"
            self.message = message
            self.clicks_sent = count

    def _click_loop(self, stop_event: threading.Event) -> None:
        count = 0
        backend = self._get_native()
        if backend is None:
            self._set_finished("Native macOS input is unavailable", count)
            return

        settings = self._settings_snapshot()
        if settings.start_delay and stop_event.wait(settings.start_delay):
            self._set_finished("Stopped during start delay", count)
            return

        start_time = time.perf_counter()
        next_action_at = start_time
        while not stop_event.is_set():
            if self._wait_until(stop_event, next_action_at):
                self._set_finished("Stopped", count)
                return

            settings = self._settings_snapshot()
            elapsed = time.perf_counter() - start_time

            if settings.stop_after_seconds and elapsed >= settings.stop_after_seconds:
                self._set_finished("Stopped by time limit", count)
                return

            if settings.max_clicks and count >= settings.max_clicks:
                self._set_finished("Stopped by click limit", count)
                return

            clicks_this_action = 1
            if settings.output_mode == "mouse":
                clicks_this_action = 2 if settings.click_type == "double" else 1
            if settings.max_clicks:
                clicks_this_action = min(clicks_this_action, settings.max_clicks - count)

            if clicks_this_action <= 0:
                self._set_finished("Stopped by click limit", count)
                return

            try:
                self._perform_action(backend, settings, clicks_this_action, stop_event)
            except NativeInputError as exc:
                self._set_finished(str(exc), count)
                return

            count += clicks_this_action
            with self.lock:
                self.clicks_sent = count
                self.message = "Pressing keys" if settings.output_mode == "keyboard" else "Clicking"

            interval = self._action_interval(settings)
            next_action_at += interval
            now = time.perf_counter()
            if next_action_at < now:
                next_action_at = now

        self._set_finished("Stopped", count)

    def _action_interval(self, settings: ClickSettings) -> float:
        interval = 1.0 / settings.cps
        jitter = settings.jitter_percent / 100.0
        if jitter:
            interval *= random.uniform(1.0 - jitter, 1.0 + jitter)
        return max(0.001, interval)

    def _wait_until(self, stop_event: threading.Event, deadline: float) -> bool:
        while True:
            if stop_event.is_set():
                return True

            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return False

            if remaining > 0.004:
                if stop_event.wait(remaining - 0.0015):
                    return True
            elif remaining > 0.00075:
                time.sleep(0)

    def _perform_action(
        self,
        backend: MacNativeInput,
        settings: ClickSettings,
        action_count: int,
        stop_event: threading.Event,
    ) -> None:
        if settings.output_mode == "keyboard":
            self._perform_key_press(backend, settings, stop_event)
            return

        self._perform_click(backend, settings, action_count, stop_event)

    def _perform_key_press(
        self,
        backend: MacNativeInput,
        settings: ClickSettings,
        stop_event: threading.Event,
    ) -> None:
        if settings.key_hold_seconds > 0:
            backend.key_down(settings.key_name)
            stop_event.wait(settings.key_hold_seconds)
            backend.key_up(settings.key_name)
        else:
            backend.tap_key(settings.key_name)

    def _perform_click(
        self,
        backend: MacNativeInput,
        settings: ClickSettings,
        click_count: int,
        stop_event: threading.Event,
    ) -> None:
        original_position = None
        if settings.position_mode == "fixed":
            original_position = backend.position
            backend.position = (settings.fixed_x, settings.fixed_y)

        try:
            for index in range(click_count):
                if stop_event.is_set():
                    return

                if settings.mouse_down_seconds > 0:
                    backend.mouse_down(settings.mouse_button)
                    stop_event.wait(settings.mouse_down_seconds)
                    backend.mouse_up(settings.mouse_button)
                else:
                    backend.click(settings.mouse_button)

                if index + 1 < click_count and stop_event.wait(0.04):
                    return
        finally:
            if original_position is not None and settings.restore_position:
                backend.position = original_position

    def _macro_loop(
        self,
        macro: MacroDefinition,
        stop_event: threading.Event,
    ) -> None:
        backend = self._get_native()
        if backend is None:
            self._set_macro_finished(macro.id, "Native macOS input is unavailable")
            return

        if not macro.events:
            self._set_macro_finished(macro.id, f"{macro.name} has no events")
            return

        message = f"{macro.name} finished"
        completed = 0
        try:
            while not stop_event.is_set() and (
                macro.repeat_count == 0 or completed < macro.repeat_count
            ):
                for event in macro.events:
                    if stop_event.is_set():
                        break
                    self._perform_macro_event(backend, event, stop_event)
                completed += 1

            if stop_event.is_set():
                message = f"{macro.name} stopped"
        except NativeInputError as exc:
            message = str(exc)
        finally:
            self._set_macro_finished(macro.id, message)

    def _perform_macro_event(
        self,
        backend: MacNativeInput,
        event: dict,
        stop_event: threading.Event,
    ) -> None:
        kind = event.get("kind")
        if kind == "delay":
            stop_event.wait(float(event.get("duration", 0.0)))
            return

        if kind == "key_down":
            backend.key_down(str(event.get("key", "space")))
            return

        if kind == "key_up":
            backend.key_up(str(event.get("key", "space")))
            return

        if kind == "mouse_down":
            backend.mouse_down(str(event.get("mouse_button", "left")))
            return

        if kind == "mouse_up":
            backend.mouse_up(str(event.get("mouse_button", "left")))
            return

        if kind == "mouse_move":
            x = coerce_int(event.get("x", 0), 0)
            y = coerce_int(event.get("y", 0), 0)
            if event.get("move_mode") == "relative":
                current_x, current_y = backend.position
                backend.position = (current_x + x, current_y + y)
            else:
                backend.position = (x, y)

    def _set_macro_finished(self, macro_id: str, message: str) -> None:
        with self.lock:
            self.macro_stop_events.pop(macro_id, None)
            self.macro_threads.pop(macro_id, None)
            self.message = message

    def _handle_position_pick_event(self, event: dict) -> None:
        if event.get("kind") != "mouse_down":
            return

        with self.lock:
            if not self.position_pick_active:
                return

            x = int(round(coerce_float(event.get("x", 0), 0.0)))
            y = int(round(coerce_float(event.get("y", 0), 0.0)))
            self.position_pick_active = False
            self.position_pick_result = {
                "token": self.position_pick_token,
                "x": x,
                "y": y,
            }
            self.message = f"Picked macro move point {x}, {y}"

    def _handle_macro_recording_event(self, event: dict, now: float) -> bool:
        stop_requested = False
        with self.lock:
            if not self.recording_active:
                return False

            if event.get("kind") == "down" and event.get("key") == "esc":
                stop_requested = True
            elif now < self.recording_start_at:
                return True
            else:
                self._record_native_event_locked(event, now)

        if stop_requested:
            self.stop_macro_recording("Recording stopped with Esc")
        return True

    def _record_native_event_locked(self, event: dict, now: float) -> None:
        kind = event.get("kind")

        if kind == "flags":
            modifiers = frozenset(event.get("modifiers", frozenset()))
            previous = self.recording_modifier_keys

            def modifier_order(key: str) -> int:
                if key in MODIFIER_DISPLAY_ORDER:
                    return MODIFIER_DISPLAY_ORDER.index(key)
                return len(MODIFIER_DISPLAY_ORDER)

            for key in sorted(modifiers - previous, key=modifier_order):
                if self._append_recorded_event_locked({"kind": "key_down", "key": key}, now):
                    self.recording_keys_down.add(key)
            for key in sorted(previous - modifiers, key=modifier_order, reverse=True):
                if self._append_recorded_event_locked({"kind": "key_up", "key": key}, now):
                    self.recording_keys_down.discard(key)
            self.recording_modifier_keys = modifiers
            return

        if kind in ("down", "up"):
            key = sanitize_key_name(event.get("key", ""), fallback="")
            if not key:
                return
            recorded = self._append_recorded_event_locked(
                {"kind": "key_down" if kind == "down" else "key_up", "key": key},
                now,
            )
            if not recorded:
                return
            if kind == "down":
                self.recording_keys_down.add(key)
            else:
                self.recording_keys_down.discard(key)
            return

        if kind == "mouse_move":
            x = int(round(coerce_float(event.get("x", 0), 0.0)))
            y = int(round(coerce_float(event.get("y", 0), 0.0)))
            previous = self.recording_last_move_position
            if previous is not None:
                dx = x - previous[0]
                dy = y - previous[1]
                elapsed = now - self.recording_last_move_at
                if elapsed < 0.025 or (dx * dx + dy * dy < 16 and elapsed < 0.08):
                    return
            self._record_mouse_position_locked(x, y, now, force=False)
            return

        if kind in ("mouse_down", "mouse_up"):
            x = int(round(coerce_float(event.get("x", 0), 0.0)))
            y = int(round(coerce_float(event.get("y", 0), 0.0)))
            self._record_mouse_position_locked(x, y, now, force=True)
            recorded = self._append_recorded_event_locked(
                {
                    "kind": kind,
                    "mouse_button": str(event.get("button", "left")),
                },
                now,
            )
            if not recorded:
                return
            button = str(event.get("button", "left"))
            if kind == "mouse_down":
                self.recording_mouse_buttons_down.add(button)
            else:
                self.recording_mouse_buttons_down.discard(button)

    def _record_mouse_position_locked(
        self, x: int, y: int, now: float, force: bool
    ) -> None:
        previous = self.recording_last_move_position
        if previous == (x, y):
            return
        if not force and previous is not None:
            dx = x - previous[0]
            dy = y - previous[1]
            if dx * dx + dy * dy < 4:
                return

        recorded = self._append_recorded_event_locked(
            {"kind": "mouse_move", "move_mode": "absolute", "x": x, "y": y},
            now,
        )
        if recorded:
            self.recording_last_move_position = (x, y)
            self.recording_last_move_at = now

    def _append_recorded_event_locked(self, event: dict, now: float) -> bool:
        remaining_slots = self.recording_max_events - len(self.recording_events)
        if remaining_slots <= 0:
            self.message = "Recording reached the 300-event limit; press Esc or Stop"
            return False

        delay = max(0.0, now - self.recording_last_event_at)
        if delay >= 0.01 and remaining_slots >= 2:
            self.recording_events.append(
                sanitize_macro_event({"kind": "delay", "duration": round(delay, 3)})
            )
            remaining_slots -= 1

        if remaining_slots <= 0:
            self.message = "Recording reached the 300-event limit; press Esc or Stop"
            return False

        self.recording_events.append(sanitize_macro_event(event))
        self.recording_last_event_at = now
        if len(self.recording_events) >= self.recording_max_events:
            self.message = "Recording reached the 300-event limit; press Esc or Stop"
        return True

    def _on_native_input_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "error":
            with self.lock:
                self.global_hotkeys_enabled = False
                self.global_hotkey_status = "Blocked"
                self.message = "Enable Input Monitoring and Accessibility for Terminal/Python"
            return

        now = time.monotonic()
        self._handle_position_pick_event(event)
        if self._handle_macro_recording_event(event, now):
            return

        if kind not in ("down", "up", "flags"):
            return

        key_name = event.get("key")
        modifiers = frozenset(event.get("modifiers", frozenset()))

        with self.hotkey_lock:
            self.pressed_keys.difference_update(MODIFIER_KEYS)
            self.pressed_keys.update(modifiers)

            if kind == "down" and key_name:
                self.pressed_keys.add(key_name)
                self._handle_global_key_down_locked()
            elif kind == "up" and key_name:
                self.pressed_keys.discard(key_name)
                self._handle_global_key_up_locked()
            elif kind == "flags":
                self._handle_global_flags_changed_locked()

    def _handle_global_key_down_locked(self) -> None:
        settings = self._settings_snapshot()
        toggle_hotkey = parse_hotkey(settings.toggle_hotkey)
        exit_hotkey = parse_hotkey(settings.exit_hotkey)

        if exit_hotkey and exit_hotkey <= self.pressed_keys:
            if exit_hotkey not in self.fired_combos:
                self.fired_combos.add(exit_hotkey)
                self.stop("Exit hotkey pressed")
                if self.shutdown_callback:
                    threading.Thread(target=self.shutdown_callback, daemon=True).start()
            return

        if toggle_hotkey and toggle_hotkey <= self.pressed_keys:
            if settings.activation_mode == "hold":
                if toggle_hotkey not in self.fired_combos:
                    self.fired_combos.add(toggle_hotkey)
                    self.start()
            elif toggle_hotkey not in self.fired_combos:
                self.fired_combos.add(toggle_hotkey)
                self.toggle_from_hotkey()
            return

        self._handle_macro_hotkeys_locked()

    def _handle_macro_hotkeys_locked(self) -> None:
        with self.lock:
            macros = list(self.macros)

        for macro in macros:
            hotkey = parse_hotkey(macro.hotkey)
            if not hotkey or not hotkey <= self.pressed_keys:
                continue
            if hotkey in self.fired_combos:
                continue
            self.fired_combos.add(hotkey)
            self.run_macro_from_hotkey(macro.id)
            return

    def _stop_released_hold_macros_locked(self) -> None:
        with self.lock:
            macros = list(self.macros)
            running_macro_ids = set(self.macro_stop_events)

        for macro in macros:
            if macro.run_mode != "hold" or macro.id not in running_macro_ids:
                continue

            hotkey = parse_hotkey(macro.hotkey)
            if hotkey and not hotkey <= self.pressed_keys:
                self.stop_macro(macro.id)

    def _handle_global_key_up_locked(self) -> None:
        self.fired_combos = {
            combo for combo in self.fired_combos if combo <= self.pressed_keys
        }

        settings = self._settings_snapshot()
        toggle_hotkey = parse_hotkey(settings.toggle_hotkey)
        if (
            settings.activation_mode == "hold"
            and toggle_hotkey
            and not toggle_hotkey <= self.pressed_keys
        ):
            self.stop("Stopped")

        self._stop_released_hold_macros_locked()

    def _handle_global_flags_changed_locked(self) -> None:
        self.fired_combos = {
            combo for combo in self.fired_combos if combo <= self.pressed_keys
        }
        self._handle_global_key_up_locked()

    def shutdown(self) -> None:
        self.stop("Application shutting down")
        self.stop_all_macros("Application shutting down")
        backend = self.native
        if backend:
            backend.stop_keyboard_events()
