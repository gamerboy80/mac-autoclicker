# Mac AutoClicker

A native macOS mouse, keyboard, and macro automation tool written in Python.
The interface uses AppKit through PyObjC, with no browser and no Tkinter.

Settings and macros are saved locally in:

```text
autoclicker_settings.json
```

The file is created next to `autoclicker.py` the first time settings or macros
are changed. Existing settings from the earlier web version remain compatible.

## Setup

Create or activate the project virtual environment and install the native UI
dependency there:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python autoclicker.py
```

Closing the window, choosing Quit, pressing `Cmd+Q`, or using the configured
exit hotkey stops the clicker and all running macros before quitting.

## Project Layout

```text
autoclicker.py       native application entrypoint
native_ui.py         AppKit window, controls, and macro editor
config.py            app defaults and saved-settings path
models.py            settings, macro data, and validation helpers
native_input.py      macOS CoreGraphics input and event tap
service.py           clicker, macro, recorder, hotkey, and persistence logic
```

## macOS Permissions

Automated input, recording, position picking, and global hotkeys require macOS
privacy permissions. Open:

```text
System Settings -> Privacy & Security
```

Grant the app used to launch the script, such as Terminal, iTerm, or Python,
access to:

- Accessibility
- Input Monitoring

After changing permissions, quit and reopen the terminal before launching the
application again.

## Repeater

- Start and Stop control repeated mouse clicks or keyboard shortcuts.
- The clicker hotkey defaults to `F6`; the exit hotkey defaults to `F8`.
- Activation can toggle on each hotkey press or run only while held.
- Mouse controls include button, single/double click, hold time, cursor mode,
  fixed position, and cursor restoration.
- Keyboard controls accept a key or multi-key shortcut through press-to-capture.
- Timing controls include actions per second, start delay, random jitter, action
  limit, time limit, and key hold time.

## Macros

- Each macro has its own press-to-capture hotkey.
- Run modes include once, repeat count, toggle loop, and loop while held.
- Events include separate key down, key up, mouse down, mouse up, absolute or
  relative mouse movement, and delays.
- The event table supports multiple selection, duplication, reordering, and
  deletion.
- Absolute move events have a screen position picker.
- Recording starts after a three-second countdown and creates a fully editable
  macro from captured mouse and keyboard events.
- Changes are saved directly through the application service to
  `autoclicker_settings.json`.
