from __future__ import annotations

import copy
import signal
from typing import Optional

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFont,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSMiniaturizableWindowMask,
    NSPopUpButton,
    NSScrollView,
    NSStatusBar,
    NSTabView,
    NSTabViewItem,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTitledWindowMask,
    NSClosableWindowMask,
    NSView,
    NSVariableStatusItemLength,
    NSWindow,
)
from Foundation import NSNotFound, NSObject, NSTimer

from config import APP_TITLE
from models import KEYCODE_TO_NAME, format_hotkey, sanitize_macro_event
from service import AutoClickerService


WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 820

ACTIVATION_MODES = ("toggle", "hold")
OUTPUT_MODES = ("mouse", "keyboard")
MOUSE_BUTTONS = ("left", "right", "middle")
CLICK_TYPES = ("single", "double")
RUN_MODES = ("once", "repeat", "toggle", "hold")
MOVE_MODES = ("absolute", "relative")

EVENT_KINDS = (
    "key_down",
    "key_up",
    "mouse_down",
    "mouse_up",
    "mouse_move",
    "delay",
)
EVENT_LABELS = {
    "key_down": "Key Down",
    "key_up": "Key Up",
    "mouse_down": "Mouse Down",
    "mouse_up": "Mouse Up",
    "mouse_move": "Mouse Move",
    "delay": "Delay",
}
RUN_MODE_LABELS = {
    "once": "Run Once",
    "repeat": "Repeat Count",
    "toggle": "Toggle Loop",
    "hold": "Loop While Held",
}
KEYCODE_MODIFIERS = {55: "cmd", 56: "shift", 58: "alt", 59: "ctrl"}


def _frame(x: float, y: float, width: float, height: float):
    return NSMakeRect(x, y, width, height)


def _label(
    text: str,
    x: float,
    y: float,
    width: float,
    height: float = 20,
    *,
    size: float = 13,
    bold: bool = False,
) -> NSTextField:
    control = NSTextField.alloc().initWithFrame_(_frame(x, y, width, height))
    control.setStringValue_(text)
    control.setEditable_(False)
    control.setSelectable_(False)
    control.setBezeled_(False)
    control.setDrawsBackground_(False)
    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    control.setFont_(font)
    return control


def _field(
    x: float,
    y: float,
    width: float,
    value: str = "",
    *,
    editable: bool = True,
) -> NSTextField:
    control = NSTextField.alloc().initWithFrame_(_frame(x, y, width, 24))
    control.setStringValue_(value)
    control.setEditable_(editable)
    control.setSelectable_(True)
    if not editable:
        control.setDrawsBackground_(True)
        control.setBackgroundColor_(NSColor.controlBackgroundColor())
    return control


def _button(title: str, x: float, y: float, width: float, action: str) -> NSButton:
    control = NSButton.alloc().initWithFrame_(_frame(x, y, width, 28))
    control.setTitle_(title)
    control.setBezelStyle_(NSBezelStyleRounded)
    control.setAction_(action)
    return control


def _popup(
    titles: list[str], x: float, y: float, width: float, action: str
) -> NSPopUpButton:
    control = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        _frame(x, y, width, 26), False
    )
    control.addItemsWithTitles_(titles)
    control.setAction_(action)
    return control


def _checkbox(title: str, x: float, y: float, width: float, action: str) -> NSButton:
    control = NSButton.alloc().initWithFrame_(_frame(x, y, width, 24))
    control.setButtonType_(NSButtonTypeSwitch)
    control.setTitle_(title)
    control.setAction_(action)
    return control


def _box(title: str, x: float, y: float, width: float, height: float) -> NSBox:
    control = NSBox.alloc().initWithFrame_(_frame(x, y, width, height))
    control.setTitle_(title)
    return control


class AutoClickerAppDelegate(NSObject):
    def initWithService_(self, service: AutoClickerService):
        self = objc.super(AutoClickerAppDelegate, self).init()
        if self is None:
            return None
        self.service = service
        self.window: Optional[NSWindow] = None
        self.state_payload: dict = {}
        self.loading_controls = False
        self.selected_macro_id: Optional[str] = None
        self.macro_draft: Optional[dict] = None
        self.macro_dirty = False
        self.macro_signature: tuple = ()
        self.last_recorded_macro_id = ""
        self.position_pick_event_index: Optional[int] = None
        self.position_pick_token: Optional[int] = None
        self.repeater_position_pick_token: Optional[int] = None
        self.capture_target: Optional[str] = None
        self.capture_display: Optional[NSTextField] = None
        self.capture_monitor = None
        self.capture_timer = None
        self.capture_pending_parts: frozenset[str] = frozenset()
        self.capture_restore_hotkeys = False
        self.refresh_timer = None
        self.status_item = None
        self.status_toggle_menu_item = None
        return self

    def applicationDidFinishLaunching_(self, _notification) -> None:
        self._build_window()
        self._build_status_item()
        self.service.set_shutdown_callback(self.requestTerminate)
        self.state_payload = self.service.state()
        self._apply_settings(self.state_payload["settings"])
        self._refresh_from_state(force_macros=True)
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.2, self, "refreshTimerFired:", None, True
        )
        self.service.enable_global_hotkeys()

    def applicationShouldTerminateAfterLastWindowClosed_(self, _application) -> bool:
        return True

    def applicationWillTerminate_(self, _notification) -> None:
        self._end_capture(restore_hotkeys=False)
        if self.refresh_timer is not None:
            self.refresh_timer.invalidate()
            self.refresh_timer = None
        self.service.shutdown()
        if self.status_item is not None:
            NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
            self.status_item = None

    def requestTerminate(self) -> None:
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "terminateApplication:", None, False
        )

    def terminateApplication_(self, _sender) -> None:
        NSApplication.sharedApplication().terminate_(None)

    def _build_window(self) -> None:
        style = NSTitledWindowMask | NSClosableWindowMask | NSMiniaturizableWindowMask
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            _frame(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(APP_TITLE)
        self.window.setMinSize_(NSMakeSize(WINDOW_WIDTH, WINDOW_HEIGHT))
        self.window.setMaxSize_(NSMakeSize(WINDOW_WIDTH, WINDOW_HEIGHT))
        self.window.setReleasedWhenClosed_(False)
        self.window.center()

        content = self.window.contentView()
        content.addSubview_(_label(APP_TITLE, 22, 776, 500, 30, size=22, bold=True))
        self.message_label = _label("Ready", 22, 752, 760, 20, size=12)
        self.message_label.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(self.message_label)

        self.status_label = _label("Stopped", 975, 778, 180, 24, size=17, bold=True)
        self.status_label.setAlignment_(2)
        content.addSubview_(self.status_label)
        self.count_label = _label("0 actions", 975, 756, 180, 18, size=12)
        self.count_label.setAlignment_(2)
        self.count_label.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(self.count_label)

        self.toggle_button = _button("Start", 22, 710, 96, "toggleClicker:")
        self.stop_all_button = _button("Stop All", 126, 710, 96, "emergencyStop:")
        self.global_button = _button(
            "Enable Global Hotkeys", 230, 710, 176, "toggleGlobalHotkeys:"
        )
        self.cursor_button = _button(
            "Capture Cursor Position", 414, 710, 180, "useCursorPosition:"
        )
        self.quit_button = _button("Quit", 1064, 710, 94, "quitApplication:")
        for button in (
            self.toggle_button,
            self.stop_all_button,
            self.global_button,
            self.cursor_button,
            self.quit_button,
        ):
            button.setTarget_(self)
            content.addSubview_(button)

        self.tab_view = NSTabView.alloc().initWithFrame_(_frame(18, 18, 1144, 674))
        content.addSubview_(self.tab_view)
        self._build_repeater_tab()
        self._build_macros_tab()

    def _build_status_item(self) -> None:
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        status_button = self.status_item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "cursorarrow.click", APP_TITLE
        )
        if image is None:
            status_button.setTitle_("AC")
        else:
            image.setTemplate_(True)
            status_button.setImage_(image)
        status_button.setToolTip_(APP_TITLE)

        menu = NSMenu.alloc().initWithTitle_(APP_TITLE)
        show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show Window", "showWindow:", ""
        )
        self.status_toggle_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start", "toggleClicker:", ""
        )
        stop_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Stop All", "emergencyStop:", ""
        )
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", "quitApplication:", ""
        )
        for item in (show_item, self.status_toggle_menu_item, stop_item, quit_item):
            item.setTarget_(self)
        menu.addItem_(show_item)
        menu.addItem_(self.status_toggle_menu_item)
        menu.addItem_(stop_item)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(quit_item)
        self.status_item.setMenu_(menu)

    def showWindow_(self, _sender) -> None:
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build_repeater_tab(self) -> None:
        item = NSTabViewItem.alloc().initWithIdentifier_("repeater")
        item.setLabel_("Repeater")
        view = NSView.alloc().initWithFrame_(_frame(0, 0, 1124, 632))
        item.setView_(view)
        self.tab_view.addTabViewItem_(item)

        hotkeys = _box("Hotkeys", 12, 323, 544, 292)
        repeater = _box("Repeater", 568, 323, 544, 292)
        position = _box("Position", 12, 12, 544, 299)
        timing = _box("Timing and Limits", 568, 12, 544, 299)
        for box in (hotkeys, repeater, position, timing):
            view.addSubview_(box)

        hotkey_view = hotkeys.contentView()
        self.activation_popup = _popup(
            ["Toggle", "Hold"], 170, 222, 220, "settingsChanged:"
        )
        self.toggle_hotkey_field = _field(170, 178, 155, "F6", editable=False)
        self.stop_hotkey_field = _field(170, 134, 155, "F7", editable=False)
        self.exit_hotkey_field = _field(170, 90, 155, "F8", editable=False)
        self.toggle_hotkey_button = _button("Set", 334, 176, 70, "captureHotkey:")
        self.stop_hotkey_button = _button("Set", 334, 132, 70, "captureHotkey:")
        self.exit_hotkey_button = _button("Set", 334, 88, 70, "captureHotkey:")
        self.toggle_hotkey_button.setTag_(1)
        self.stop_hotkey_button.setTag_(6)
        self.exit_hotkey_button.setTag_(2)
        for label in (
            _label("Activation", 18, 225, 140),
            _label("Clicker hotkey", 18, 181, 140),
            _label("Stop-all hotkey", 18, 137, 140),
            _label("Exit hotkey", 18, 93, 140),
        ):
            hotkey_view.addSubview_(label)
        for control in (
            self.activation_popup,
            self.toggle_hotkey_field,
            self.stop_hotkey_field,
            self.exit_hotkey_field,
            self.toggle_hotkey_button,
            self.stop_hotkey_button,
            self.exit_hotkey_button,
        ):
            control.setTarget_(self)
            hotkey_view.addSubview_(control)
        hotkey_note = _label(
            "Global shortcuts are enabled when the app starts.", 18, 48, 460, 20, size=11
        )
        hotkey_note.setTextColor_(NSColor.secondaryLabelColor())
        hotkey_view.addSubview_(hotkey_note)

        repeater_view = repeater.contentView()
        self.output_popup = _popup(
            ["Mouse Click", "Key Press"], 170, 218, 220, "settingsChanged:"
        )
        self.cps_field = _field(170, 178, 220, "10")
        self.mouse_button_popup = _popup(
            ["Left", "Right", "Middle"], 170, 138, 220, "settingsChanged:"
        )
        self.click_type_popup = _popup(
            ["Single", "Double"], 170, 98, 220, "settingsChanged:"
        )
        self.mouse_down_field = _field(170, 58, 220, "0")
        self.key_name_field = _field(170, 18, 150, "Space", editable=False)
        self.key_name_button = _button("Set", 329, 16, 61, "captureHotkey:")
        self.key_name_button.setTag_(3)
        rows = (
            ("Action", 221),
            ("Actions/sec", 181),
            ("Mouse button", 141),
            ("Click type", 101),
            ("Mouse hold sec", 61),
            ("Key / shortcut", 21),
        )
        for title, y in rows:
            repeater_view.addSubview_(_label(title, 18, y, 140))
        for control in (
            self.output_popup,
            self.cps_field,
            self.mouse_button_popup,
            self.click_type_popup,
            self.mouse_down_field,
            self.key_name_field,
            self.key_name_button,
        ):
            control.setTarget_(self)
            repeater_view.addSubview_(control)

        position_view = position.contentView()
        self.fixed_position_checkbox = _checkbox(
            "Move cursor to fixed point before clicking",
            18,
            224,
            360,
            "settingsChanged:",
        )
        self.fixed_x_field = _field(170, 178, 102, "0")
        self.fixed_y_field = _field(288, 178, 102, "0")
        self.restore_checkbox = _checkbox(
            "Return cursor after fixed click", 170, 128, 270, "settingsChanged:"
        )
        position_view.addSubview_(_label("Fixed point", 18, 181, 140))
        position_view.addSubview_(_label("X", 170, 207, 80, 16, size=10))
        position_view.addSubview_(_label("Y", 288, 207, 80, 16, size=10))
        for control in (
            self.fixed_position_checkbox,
            self.fixed_x_field,
            self.fixed_y_field,
            self.restore_checkbox,
        ):
            control.setTarget_(self)
            position_view.addSubview_(control)

        timing_view = timing.contentView()
        self.start_delay_field = _field(170, 224, 220, "0")
        self.jitter_field = _field(170, 178, 220, "0")
        self.max_clicks_field = _field(170, 132, 220, "0")
        self.stop_after_field = _field(170, 86, 220, "0")
        self.key_hold_field = _field(170, 40, 220, "0")
        rows = (
            ("Start delay sec", 227),
            ("Random jitter %", 181),
            ("Max actions", 135),
            ("Stop after sec", 89),
            ("Key hold sec", 43),
        )
        for title, y in rows:
            timing_view.addSubview_(_label(title, 18, y, 140))
        for control in (
            self.start_delay_field,
            self.jitter_field,
            self.max_clicks_field,
            self.stop_after_field,
            self.key_hold_field,
        ):
            timing_view.addSubview_(control)

        self.setting_text_fields = {
            self.cps_field,
            self.mouse_down_field,
            self.fixed_x_field,
            self.fixed_y_field,
            self.start_delay_field,
            self.jitter_field,
            self.max_clicks_field,
            self.stop_after_field,
            self.key_hold_field,
        }
        for field in self.setting_text_fields:
            field.setDelegate_(self)

    def _build_macros_tab(self) -> None:
        item = NSTabViewItem.alloc().initWithIdentifier_("macros")
        item.setLabel_("Macros")
        view = NSView.alloc().initWithFrame_(_frame(0, 0, 1124, 632))
        item.setView_(view)
        self.tab_view.addTabViewItem_(item)

        macro_box = _box("Macros", 12, 12, 270, 603)
        editor_box = _box("Macro Editor", 294, 12, 818, 603)
        view.addSubview_(macro_box)
        view.addSubview_(editor_box)
        list_view = macro_box.contentView()
        editor = editor_box.contentView()

        self.macro_table = NSTableView.alloc().initWithFrame_(_frame(0, 0, 238, 480))
        name_column = NSTableColumn.alloc().initWithIdentifier_("name")
        name_column.setTitle_("Name")
        name_column.setWidth_(145)
        hotkey_column = NSTableColumn.alloc().initWithIdentifier_("hotkey")
        hotkey_column.setTitle_("Hotkey")
        hotkey_column.setWidth_(82)
        self.macro_table.addTableColumn_(name_column)
        self.macro_table.addTableColumn_(hotkey_column)
        self.macro_table.setHeaderView_(self.macro_table.headerView())
        self.macro_table.setDataSource_(self)
        self.macro_table.setDelegate_(self)
        self.macro_table.setAllowsMultipleSelection_(False)
        scroll = NSScrollView.alloc().initWithFrame_(_frame(12, 68, 238, 486))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)
        scroll.setDocumentView_(self.macro_table)
        list_view.addSubview_(scroll)

        new_button = _button("New", 12, 24, 67, "newMacro:")
        duplicate_button = _button("Copy", 87, 24, 67, "duplicateMacro:")
        delete_button = _button("Delete", 162, 24, 80, "deleteMacro:")
        for button in (new_button, duplicate_button, delete_button):
            button.setTarget_(self)
            list_view.addSubview_(button)

        self.macro_name_field = _field(78, 530, 225, "")
        self.macro_hotkey_field = _field(410, 530, 120, "Unassigned", editable=False)
        self.macro_hotkey_button = _button("Set", 538, 528, 60, "captureHotkey:")
        self.macro_hotkey_button.setTag_(4)
        self.macro_run_popup = _popup(
            [RUN_MODE_LABELS[value] for value in RUN_MODES],
            78,
            488,
            225,
            "macroDraftChanged:",
        )
        self.macro_repeat_field = _field(410, 488, 120, "1")
        editor.addSubview_(_label("Name", 18, 533, 55))
        editor.addSubview_(_label("Hotkey", 342, 533, 64))
        editor.addSubview_(_label("Run mode", 18, 491, 60))
        editor.addSubview_(_label("Repeats", 342, 491, 64))
        for control in (
            self.macro_name_field,
            self.macro_hotkey_field,
            self.macro_hotkey_button,
            self.macro_run_popup,
            self.macro_repeat_field,
        ):
            control.setTarget_(self)
            editor.addSubview_(control)
        self.macro_name_field.setDelegate_(self)
        self.macro_repeat_field.setDelegate_(self)

        self.run_macro_button = _button("Run", 618, 528, 78, "runMacro:")
        stop_macro_button = _button("Stop", 704, 528, 78, "stopMacro:")
        self.save_macro_button = _button("Save", 618, 488, 78, "saveMacro:")
        for button in (self.run_macro_button, stop_macro_button, self.save_macro_button):
            button.setTarget_(self)
            editor.addSubview_(button)

        self.record_button = _button("Record", 18, 446, 92, "startRecording:")
        self.stop_record_button = _button(
            "Stop Recording", 118, 446, 125, "stopRecording:"
        )
        self.record_status_label = _label("Recorder idle", 254, 450, 520, 20, size=12)
        self.record_status_label.setTextColor_(NSColor.secondaryLabelColor())
        for control in (self.record_button, self.stop_record_button):
            control.setTarget_(self)
            editor.addSubview_(control)
        editor.addSubview_(self.record_status_label)

        add_x = 18
        for index, kind in enumerate(EVENT_KINDS):
            widths = (92, 82, 102, 92, 98, 72)
            button = _button(
                EVENT_LABELS[kind], add_x, 404, widths[index], "addMacroEvent:"
            )
            button.setTag_(index)
            button.setTarget_(self)
            editor.addSubview_(button)
            add_x += widths[index] + 7

        self.event_table = NSTableView.alloc().initWithFrame_(_frame(0, 0, 770, 190))
        columns = (
            ("index", "#", 38),
            ("type", "Event", 135),
            ("target", "Input / Position", 370),
            ("duration", "Delay", 105),
        )
        for identifier, title, width in columns:
            column = NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.setTitle_(title)
            column.setWidth_(width)
            self.event_table.addTableColumn_(column)
        self.event_table.setDataSource_(self)
        self.event_table.setDelegate_(self)
        self.event_table.setAllowsMultipleSelection_(True)
        event_scroll = NSScrollView.alloc().initWithFrame_(_frame(18, 194, 764, 198))
        event_scroll.setHasVerticalScroller_(True)
        event_scroll.setBorderType_(2)
        event_scroll.setDocumentView_(self.event_table)
        editor.addSubview_(event_scroll)

        selection_buttons = (
            ("All", 18, 62, "selectAllEvents:"),
            ("Clear", 87, 70, "clearEventSelection:"),
            ("Duplicate", 164, 92, "duplicateEvents:"),
            ("Up", 263, 60, "moveEventsUp:"),
            ("Down", 330, 70, "moveEventsDown:"),
            ("Delete", 407, 78, "deleteEvents:"),
        )
        x = 18
        for title, _unused, width, action in selection_buttons:
            button = _button(title, x, 157, width, action)
            button.setTarget_(self)
            editor.addSubview_(button)
            x += width + 7

        editor.addSubview_(_label("Selected Event", 18, 124, 180, 20, bold=True))
        self.event_kind_popup = _popup(
            [EVENT_LABELS[kind] for kind in EVENT_KINDS],
            18,
            86,
            145,
            "eventInspectorChanged:",
        )
        self.event_key_label = _label("Key", 177, 91, 32)
        self.event_key_field = _field(211, 87, 120, "Space", editable=False)
        self.event_key_button = _button("Set", 338, 85, 55, "captureHotkey:")
        self.event_key_button.setTag_(5)
        self.event_button_label = _label("Button", 177, 91, 48)
        self.event_button_popup = _popup(
            ["Left", "Right", "Middle"], 232, 86, 120, "eventInspectorChanged:"
        )
        self.event_move_label = _label("Move", 177, 91, 42)
        self.event_move_popup = _popup(
            ["Absolute", "Relative"], 222, 86, 112, "eventInspectorChanged:"
        )
        self.event_x_label = _label("X", 345, 91, 15)
        self.event_x_field = _field(362, 87, 70, "0")
        self.event_y_label = _label("Y", 440, 91, 15)
        self.event_y_field = _field(457, 87, 70, "0")
        self.pick_position_button = _button("Pick", 536, 85, 58, "pickEventPosition:")
        self.event_duration_label = _label("Seconds", 177, 91, 58)
        self.event_duration_field = _field(239, 87, 105, "0.05")
        self.event_empty_label = _label(
            "Select an event to edit it.", 177, 91, 360, 20, size=12
        )
        self.event_empty_label.setTextColor_(NSColor.secondaryLabelColor())
        self.event_inspector_controls = (
            self.event_kind_popup,
            self.event_key_label,
            self.event_key_field,
            self.event_key_button,
            self.event_button_label,
            self.event_button_popup,
            self.event_move_label,
            self.event_move_popup,
            self.event_x_label,
            self.event_x_field,
            self.event_y_label,
            self.event_y_field,
            self.pick_position_button,
            self.event_duration_label,
            self.event_duration_field,
            self.event_empty_label,
        )
        for control in self.event_inspector_controls:
            if hasattr(control, "setTarget_"):
                control.setTarget_(self)
            editor.addSubview_(control)
        self.event_x_field.setDelegate_(self)
        self.event_y_field.setDelegate_(self)
        self.event_duration_field.setDelegate_(self)
        self._show_event_inspector(None)

    def _popup_value(self, popup: NSPopUpButton, values: tuple[str, ...]) -> str:
        index = popup.indexOfSelectedItem()
        if index < 0 or index >= len(values):
            return values[0]
        return values[index]

    def _select_popup(
        self, popup: NSPopUpButton, values: tuple[str, ...], value: str
    ) -> None:
        try:
            index = values.index(value)
        except ValueError:
            index = 0
        popup.selectItemAtIndex_(index)

    def _apply_settings(self, settings: dict) -> None:
        self.loading_controls = True
        self._select_popup(
            self.activation_popup, ACTIVATION_MODES, settings["activation_mode"]
        )
        self._select_popup(self.output_popup, OUTPUT_MODES, settings["output_mode"])
        self._select_popup(
            self.mouse_button_popup, MOUSE_BUTTONS, settings["mouse_button"]
        )
        self._select_popup(self.click_type_popup, CLICK_TYPES, settings["click_type"])
        self.toggle_hotkey_field.setStringValue_(settings["toggle_hotkey"])
        self.stop_hotkey_field.setStringValue_(settings["stop_hotkey"])
        self.exit_hotkey_field.setStringValue_(settings["exit_hotkey"])
        self.key_name_field.setStringValue_(settings["key_name"])
        self.cps_field.setStringValue_(str(settings["cps"]))
        self.mouse_down_field.setStringValue_(str(settings["mouse_down_seconds"]))
        self.key_hold_field.setStringValue_(str(settings["key_hold_seconds"]))
        self.fixed_x_field.setStringValue_(str(settings["fixed_x"]))
        self.fixed_y_field.setStringValue_(str(settings["fixed_y"]))
        self.start_delay_field.setStringValue_(str(settings["start_delay"]))
        self.jitter_field.setStringValue_(str(settings["jitter_percent"]))
        self.max_clicks_field.setStringValue_(str(settings["max_clicks"]))
        self.stop_after_field.setStringValue_(str(settings["stop_after_seconds"]))
        self.restore_checkbox.setState_(
            NSControlStateValueOn if settings["restore_position"] else NSControlStateValueOff
        )
        self.fixed_position_checkbox.setState_(
            NSControlStateValueOn
            if settings["position_mode"] == "fixed"
            else NSControlStateValueOff
        )
        self._update_position_controls()
        self.loading_controls = False

    def _update_position_controls(self) -> None:
        enabled = self.fixed_position_checkbox.state() == NSControlStateValueOn
        self.fixed_x_field.setEnabled_(enabled)
        self.fixed_y_field.setEnabled_(enabled)
        self.restore_checkbox.setEnabled_(enabled)

    def _settings_payload(self) -> dict:
        return {
            "activation_mode": self._popup_value(
                self.activation_popup, ACTIVATION_MODES
            ),
            "toggle_hotkey": self.toggle_hotkey_field.stringValue(),
            "stop_hotkey": self.stop_hotkey_field.stringValue(),
            "exit_hotkey": self.exit_hotkey_field.stringValue(),
            "output_mode": self._popup_value(self.output_popup, OUTPUT_MODES),
            "cps": self.cps_field.stringValue(),
            "mouse_button": self._popup_value(
                self.mouse_button_popup, MOUSE_BUTTONS
            ),
            "click_type": self._popup_value(self.click_type_popup, CLICK_TYPES),
            "key_name": self.key_name_field.stringValue(),
            "key_hold_seconds": self.key_hold_field.stringValue(),
            "position_mode": (
                "fixed"
                if self.fixed_position_checkbox.state() == NSControlStateValueOn
                else "follow"
            ),
            "fixed_x": self.fixed_x_field.stringValue(),
            "fixed_y": self.fixed_y_field.stringValue(),
            "restore_position": self.restore_checkbox.state()
            == NSControlStateValueOn,
            "start_delay": self.start_delay_field.stringValue(),
            "jitter_percent": self.jitter_field.stringValue(),
            "max_clicks": self.max_clicks_field.stringValue(),
            "stop_after_seconds": self.stop_after_field.stringValue(),
            "mouse_down_seconds": self.mouse_down_field.stringValue(),
        }

    def settingsChanged_(self, _sender) -> None:
        if self.loading_controls:
            return
        self._update_position_controls()
        self.state_payload = self.service.update_settings(self._settings_payload())
        self._apply_settings(self.state_payload["settings"])
        self._refresh_from_state()

    def controlTextDidEndEditing_(self, notification) -> None:
        control = notification.object()
        if control in self.setting_text_fields:
            self.settingsChanged_(control)
        elif control in (self.macro_name_field, self.macro_repeat_field):
            self.macroDraftChanged_(control)
        elif control in (
            self.event_x_field,
            self.event_y_field,
            self.event_duration_field,
        ):
            self.eventInspectorChanged_(control)

    def toggleClicker_(self, _sender) -> None:
        self.state_payload = self.service.toggle()
        self._refresh_from_state()

    def emergencyStop_(self, _sender) -> None:
        self.state_payload = self.service.emergency_stop()
        self._refresh_from_state()

    def toggleGlobalHotkeys_(self, _sender) -> None:
        if self.state_payload.get("global_hotkeys_enabled"):
            self.state_payload = self.service.disable_global_hotkeys()
        else:
            self.state_payload = self.service.enable_global_hotkeys()
        self._refresh_from_state()

    def useCursorPosition_(self, _sender) -> None:
        self.position_pick_event_index = None
        self.position_pick_token = None
        self.state_payload = self.service.start_position_pick()
        pick = self.state_payload.get("position_pick", {})
        self.repeater_position_pick_token = pick.get("token") if pick.get("active") else None
        self._refresh_from_state()

    def quitApplication_(self, _sender) -> None:
        NSApplication.sharedApplication().terminate_(None)

    def refreshTimerFired_(self, _timer) -> None:
        self.state_payload = self.service.state()
        self._refresh_from_state()

    def _macro_state_signature(self, macros: list[dict]) -> tuple:
        return tuple(
            (
                macro["id"],
                macro["name"],
                macro["hotkey"],
                macro["run_mode"],
                macro["repeat_count"],
                len(macro.get("events", [])),
            )
            for macro in macros
        )

    def _refresh_from_state(self, force_macros: bool = False) -> None:
        state = self.state_payload
        self.status_label.setStringValue_(state.get("status", "Stopped"))
        self.count_label.setStringValue_(f'{state.get("clicks_sent", 0)} actions')
        self.message_label.setStringValue_(state.get("message", ""))
        self.toggle_button.setTitle_("Stop" if state.get("running") else "Start")
        if self.status_toggle_menu_item is not None:
            self.status_toggle_menu_item.setTitle_(
                "Stop Clicker" if state.get("running") else "Start Clicker"
            )
        global_enabled = state.get("global_hotkeys_enabled", False)
        self.global_button.setTitle_(
            "Disable Global Hotkeys" if global_enabled else "Enable Global Hotkeys"
        )

        recorder = state.get("recorder", {})
        phase = recorder.get("phase", "idle")
        if phase == "countdown":
            recorder_text = f'Recording starts in {recorder.get("remaining", 0):.1f}s'
        elif phase == "recording":
            recorder_text = f'Recording: {recorder.get("event_count", 0)} events'
        else:
            recorder_text = "Recorder idle"
        self.record_status_label.setStringValue_(recorder_text)
        self.record_button.setEnabled_(phase == "idle")
        self.stop_record_button.setEnabled_(phase != "idle")

        macros = state.get("macros", [])
        signature = self._macro_state_signature(macros)
        recorded_id = state.get("last_recorded_macro_id", "")
        if recorded_id and recorded_id != self.last_recorded_macro_id:
            self._select_macro_id(recorded_id, save_current=False)
            self.last_recorded_macro_id = recorded_id
            force_macros = True

        if force_macros or signature != self.macro_signature:
            self.macro_signature = signature
            self.macro_table.reloadData()
            if self.selected_macro_id is None and macros:
                self._select_macro_id(macros[0]["id"], save_current=False)
            elif self.selected_macro_id and not any(
                macro["id"] == self.selected_macro_id for macro in macros
            ):
                self._select_macro_id(macros[0]["id"] if macros else None, save_current=False)
            self._restore_macro_table_selection()

        running = set(state.get("running_macros", []))
        selected_running = self.selected_macro_id in running
        self.run_macro_button.setTitle_("Running" if selected_running else "Run")
        self.run_macro_button.setEnabled_(bool(self.selected_macro_id) and not selected_running)

        pick = state.get("position_pick", {})
        result = pick.get("result")
        repeater_pick_active = (
            self.repeater_position_pick_token is not None and pick.get("active", False)
        )
        self.cursor_button.setTitle_(
            "Waiting for Click..." if repeater_pick_active else "Capture Cursor Position"
        )
        self.cursor_button.setEnabled_(not pick.get("active", False))
        if (
            self.repeater_position_pick_token is not None
            and result
            and pick.get("token") == self.repeater_position_pick_token
        ):
            self.repeater_position_pick_token = None
            self.service.clear_position_pick()
            self.state_payload = self.service.set_fixed_position(result["x"], result["y"])
            self._apply_settings(self.state_payload["settings"])
            self._refresh_from_state()
            return

        if (
            self.position_pick_event_index is not None
            and result
            and pick.get("token") == self.position_pick_token
        ):
            index = self.position_pick_event_index
            self.position_pick_event_index = None
            self.position_pick_token = None
            if self.macro_draft and index < len(self.macro_draft.get("events", [])):
                event = self.macro_draft["events"][index]
                event["x"] = result["x"]
                event["y"] = result["y"]
                event["move_mode"] = "absolute"
                self._save_macro_draft()
                self.event_table.selectRowIndexes_byExtendingSelection_(
                    self._index_set(index), False
                )
                self._show_event_inspector(event)
            self.service.clear_position_pick()

    def _macro_by_id(self, macro_id: Optional[str]) -> Optional[dict]:
        if not macro_id:
            return None
        return next(
            (
                macro
                for macro in self.state_payload.get("macros", [])
                if macro["id"] == macro_id
            ),
            None,
        )

    def _select_macro_id(
        self, macro_id: Optional[str], *, save_current: bool = True
    ) -> None:
        if save_current and self.macro_dirty:
            self._save_macro_draft()
        self.selected_macro_id = macro_id
        macro = self._macro_by_id(macro_id)
        self.macro_draft = copy.deepcopy(macro) if macro else None
        self.macro_dirty = False
        self._load_macro_editor()

    def _restore_macro_table_selection(self) -> None:
        macros = self.state_payload.get("macros", [])
        row = next(
            (
                index
                for index, macro in enumerate(macros)
                if macro["id"] == self.selected_macro_id
            ),
            -1,
        )
        if row >= 0:
            self.macro_table.selectRowIndexes_byExtendingSelection_(
                self._index_set(row), False
            )

    def _load_macro_editor(self) -> None:
        self.loading_controls = True
        draft = self.macro_draft
        if draft is None:
            self.macro_name_field.setStringValue_("")
            self.macro_hotkey_field.setStringValue_("Unassigned")
            self.macro_repeat_field.setStringValue_("1")
            self._select_popup(self.macro_run_popup, RUN_MODES, "once")
        else:
            self.macro_name_field.setStringValue_(draft["name"])
            self.macro_hotkey_field.setStringValue_(draft.get("hotkey", "Unassigned"))
            self.macro_repeat_field.setStringValue_(str(draft.get("repeat_count", 1)))
            self._select_popup(
                self.macro_run_popup, RUN_MODES, draft.get("run_mode", "once")
            )
        self.loading_controls = False
        self.save_macro_button.setTitle_("Save")
        self.event_table.reloadData()
        self.event_table.deselectAll_(None)
        self._show_event_inspector(None)

    def _collect_macro_header(self) -> None:
        if self.macro_draft is None:
            return
        self.macro_draft["name"] = self.macro_name_field.stringValue()
        self.macro_draft["hotkey"] = self.macro_hotkey_field.stringValue()
        self.macro_draft["run_mode"] = self._popup_value(
            self.macro_run_popup, RUN_MODES
        )
        self.macro_draft["repeat_count"] = self.macro_repeat_field.stringValue()

    def macroDraftChanged_(self, _sender) -> None:
        if self.loading_controls or self.macro_draft is None:
            return
        self._collect_macro_header()
        self.macro_dirty = True
        self.save_macro_button.setTitle_("Save*")
        self._save_macro_draft()

    def _save_macro_draft(self) -> None:
        if self.macro_draft is None:
            return
        self._collect_macro_header()
        selected_rows = self._selected_event_rows()
        self.state_payload = self.service.update_macro(self.macro_draft)
        refreshed = self._macro_by_id(self.selected_macro_id)
        self.macro_draft = copy.deepcopy(refreshed) if refreshed else None
        self.macro_dirty = False
        self.save_macro_button.setTitle_("Save")
        self.macro_signature = self._macro_state_signature(
            self.state_payload.get("macros", [])
        )
        self.macro_table.reloadData()
        self._restore_macro_table_selection()
        self.event_table.reloadData()
        self._select_event_rows(selected_rows)
        self._refresh_from_state()

    def saveMacro_(self, _sender) -> None:
        self._save_macro_draft()

    def newMacro_(self, _sender) -> None:
        if self.macro_dirty:
            self._save_macro_draft()
        self.state_payload = self.service.create_macro()
        macros = self.state_payload.get("macros", [])
        self._select_macro_id(macros[-1]["id"] if macros else None, save_current=False)
        self.macro_signature = ()
        self._refresh_from_state(force_macros=True)

    def duplicateMacro_(self, _sender) -> None:
        if not self.selected_macro_id:
            return
        if self.macro_dirty:
            self._save_macro_draft()
        self.state_payload = self.service.duplicate_macro(self.selected_macro_id)
        duplicate_id = self.state_payload.get("last_recorded_macro_id")
        self._select_macro_id(duplicate_id, save_current=False)
        self.last_recorded_macro_id = duplicate_id or self.last_recorded_macro_id
        self._refresh_from_state(force_macros=True)

    def deleteMacro_(self, _sender) -> None:
        if not self.selected_macro_id:
            return
        self.state_payload = self.service.delete_macro(self.selected_macro_id)
        macros = self.state_payload.get("macros", [])
        self._select_macro_id(macros[0]["id"] if macros else None, save_current=False)
        self._refresh_from_state(force_macros=True)

    def runMacro_(self, _sender) -> None:
        if not self.selected_macro_id:
            return
        if self.macro_dirty:
            self._save_macro_draft()
        self.state_payload = self.service.run_macro(self.selected_macro_id)
        self._refresh_from_state()

    def stopMacro_(self, _sender) -> None:
        if not self.selected_macro_id:
            return
        self.state_payload = self.service.stop_macro(self.selected_macro_id)
        self._refresh_from_state()

    def startRecording_(self, _sender) -> None:
        if self.macro_dirty:
            self._save_macro_draft()
        self.state_payload = self.service.start_macro_recording(3.0)
        self._refresh_from_state()

    def stopRecording_(self, _sender) -> None:
        self.state_payload = self.service.stop_macro_recording()
        recorded_id = self.state_payload.get("last_recorded_macro_id", "")
        if recorded_id:
            self._select_macro_id(recorded_id, save_current=False)
            self.last_recorded_macro_id = recorded_id
        self._refresh_from_state(force_macros=True)

    def addMacroEvent_(self, sender) -> None:
        if self.macro_draft is None:
            return
        tag = sender.tag()
        if tag < 0 or tag >= len(EVENT_KINDS):
            return
        event = sanitize_macro_event({"kind": EVENT_KINDS[tag]})
        events = self.macro_draft.setdefault("events", [])
        events.append(event)
        new_index = len(events) - 1
        self.macro_dirty = True
        self._save_macro_draft()
        self.event_table.selectRowIndexes_byExtendingSelection_(
            self._index_set(new_index), False
        )
        self.event_table.scrollRowToVisible_(new_index)

    def _selected_event_rows(self) -> list[int]:
        indexes = self.event_table.selectedRowIndexes()
        rows = []
        index = indexes.firstIndex()
        while index != NSNotFound:
            rows.append(int(index))
            index = indexes.indexGreaterThanIndex_(index)
        return rows

    def _index_set(self, index: int):
        from Foundation import NSIndexSet

        return NSIndexSet.indexSetWithIndex_(index)

    def _select_event_rows(self, rows: list[int]) -> None:
        if not rows:
            self.event_table.deselectAll_(None)
            return
        from Foundation import NSMutableIndexSet

        indexes = NSMutableIndexSet.indexSet()
        count = len(self.macro_draft.get("events", [])) if self.macro_draft else 0
        for row in rows:
            if 0 <= row < count:
                indexes.addIndex_(row)
        self.event_table.selectRowIndexes_byExtendingSelection_(indexes, False)

    def selectAllEvents_(self, _sender) -> None:
        self.event_table.selectAll_(None)

    def clearEventSelection_(self, _sender) -> None:
        self.event_table.deselectAll_(None)

    def duplicateEvents_(self, _sender) -> None:
        if self.macro_draft is None:
            return
        rows = self._selected_event_rows()
        if not rows:
            return
        events = self.macro_draft["events"]
        copies = [copy.deepcopy(events[row]) for row in rows]
        insert_at = rows[-1] + 1
        events[insert_at:insert_at] = copies
        self.macro_dirty = True
        self._save_macro_draft()
        self._select_event_rows(list(range(insert_at, insert_at + len(copies))))

    def deleteEvents_(self, _sender) -> None:
        if self.macro_draft is None:
            return
        rows = set(self._selected_event_rows())
        if not rows:
            return
        self.macro_draft["events"] = [
            event
            for index, event in enumerate(self.macro_draft["events"])
            if index not in rows
        ]
        self.macro_dirty = True
        self._save_macro_draft()

    def _move_selected_events(self, direction: int) -> None:
        if self.macro_draft is None:
            return
        rows = self._selected_event_rows()
        if not rows:
            return
        events = self.macro_draft["events"]
        selected = set(rows)
        next_rows = set()
        ordered = rows if direction < 0 else list(reversed(rows))
        for index in ordered:
            target = index + direction
            if target < 0 or target >= len(events) or target in selected:
                next_rows.add(index)
                continue
            events[index], events[target] = events[target], events[index]
            selected.remove(index)
            selected.add(target)
            next_rows.add(target)
        self.macro_dirty = True
        self._save_macro_draft()
        self._select_event_rows(sorted(next_rows))

    def moveEventsUp_(self, _sender) -> None:
        self._move_selected_events(-1)

    def moveEventsDown_(self, _sender) -> None:
        self._move_selected_events(1)

    def _current_event_index(self) -> int:
        row = self.event_table.selectedRow()
        if self.macro_draft is None or row < 0:
            return -1
        if row >= len(self.macro_draft.get("events", [])):
            return -1
        return int(row)

    def _current_event(self) -> Optional[dict]:
        index = self._current_event_index()
        if index < 0:
            return None
        return self.macro_draft["events"][index]

    def _show_event_inspector(self, event: Optional[dict]) -> None:
        self.loading_controls = True
        for control in self.event_inspector_controls:
            control.setHidden_(True)
        self.event_kind_popup.setHidden_(False)
        if event is None:
            self.event_kind_popup.setEnabled_(False)
            self.event_empty_label.setHidden_(False)
            self.loading_controls = False
            return

        self.event_kind_popup.setEnabled_(True)
        kind = event.get("kind", "delay")
        self._select_popup(self.event_kind_popup, EVENT_KINDS, kind)
        if kind in ("key_down", "key_up"):
            for control in (
                self.event_key_label,
                self.event_key_field,
                self.event_key_button,
            ):
                control.setHidden_(False)
            self.event_key_field.setStringValue_(event.get("key", "Space"))
        elif kind in ("mouse_down", "mouse_up"):
            self.event_button_label.setHidden_(False)
            self.event_button_popup.setHidden_(False)
            self._select_popup(
                self.event_button_popup,
                MOUSE_BUTTONS,
                event.get("mouse_button", "left"),
            )
        elif kind == "mouse_move":
            for control in (
                self.event_move_label,
                self.event_move_popup,
                self.event_x_label,
                self.event_x_field,
                self.event_y_label,
                self.event_y_field,
                self.pick_position_button,
            ):
                control.setHidden_(False)
            self._select_popup(
                self.event_move_popup, MOVE_MODES, event.get("move_mode", "absolute")
            )
            self.event_x_field.setStringValue_(str(event.get("x", 0)))
            self.event_y_field.setStringValue_(str(event.get("y", 0)))
            self.pick_position_button.setEnabled_(event.get("move_mode") == "absolute")
        else:
            self.event_duration_label.setHidden_(False)
            self.event_duration_field.setHidden_(False)
            self.event_duration_field.setStringValue_(str(event.get("duration", 0.05)))
        self.loading_controls = False

    def eventInspectorChanged_(self, _sender) -> None:
        if self.loading_controls:
            return
        event = self._current_event()
        if event is None:
            return
        kind = self._popup_value(self.event_kind_popup, EVENT_KINDS)
        event["kind"] = kind
        if kind in ("key_down", "key_up"):
            event["key"] = self.event_key_field.stringValue()
        elif kind in ("mouse_down", "mouse_up"):
            event["mouse_button"] = self._popup_value(
                self.event_button_popup, MOUSE_BUTTONS
            )
        elif kind == "mouse_move":
            event["move_mode"] = self._popup_value(self.event_move_popup, MOVE_MODES)
            event["x"] = self.event_x_field.stringValue()
            event["y"] = self.event_y_field.stringValue()
        else:
            event["duration"] = self.event_duration_field.stringValue()
        selected = self._selected_event_rows()
        self.macro_dirty = True
        self._save_macro_draft()
        self._select_event_rows(selected)
        self._show_event_inspector(self._current_event())

    def pickEventPosition_(self, _sender) -> None:
        index = self._current_event_index()
        event = self._current_event()
        if index < 0 or event is None or event.get("kind") != "mouse_move":
            return
        self.position_pick_event_index = index
        self.repeater_position_pick_token = None
        self.state_payload = self.service.start_position_pick()
        pick = self.state_payload.get("position_pick", {})
        self.position_pick_token = pick.get("token")
        self._refresh_from_state()

    def captureHotkey_(self, sender) -> None:
        targets = {
            1: ("toggle_hotkey", self.toggle_hotkey_field),
            2: ("exit_hotkey", self.exit_hotkey_field),
            3: ("key_name", self.key_name_field),
            4: ("macro_hotkey", self.macro_hotkey_field),
            5: ("event_key", self.event_key_field),
            6: ("stop_hotkey", self.stop_hotkey_field),
        }
        target = targets.get(sender.tag())
        if target is None:
            return
        if target[0] == "macro_hotkey" and self.macro_draft is None:
            return
        if target[0] == "event_key" and self._current_event() is None:
            return
        self._begin_capture(*target)

    def _begin_capture(self, target: str, display: NSTextField) -> None:
        self._end_capture(restore_hotkeys=True)
        self.capture_target = target
        self.capture_display = display
        self.capture_restore_hotkeys = bool(
            self.state_payload.get("global_hotkeys_enabled")
        )
        if self.capture_restore_hotkeys:
            self.state_payload = self.service.disable_global_hotkeys()
        display.setStringValue_("Press shortcut...")

        mask = NSEventMaskKeyDown | NSEventMaskFlagsChanged

        def handler(event):
            self._handle_capture_event(event)
            return None

        self.capture_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, handler
        )

    def _parts_from_event(self, event) -> frozenset[str]:
        flags = int(event.modifierFlags())
        parts = set()
        if flags & NSEventModifierFlagCommand:
            parts.add("cmd")
        if flags & NSEventModifierFlagControl:
            parts.add("ctrl")
        if flags & NSEventModifierFlagOption:
            parts.add("alt")
        if flags & NSEventModifierFlagShift:
            parts.add("shift")
        key_code = int(event.keyCode())
        key = KEYCODE_TO_NAME.get(key_code) or KEYCODE_MODIFIERS.get(key_code)
        if key and key not in ("cmd", "ctrl", "alt", "shift"):
            parts.add(key)
        return frozenset(parts)

    def _handle_capture_event(self, event) -> None:
        if self.capture_target is None:
            return
        parts = self._parts_from_event(event)
        if not parts:
            return
        self.capture_display.setStringValue_(format_hotkey(parts))
        key_code = int(event.keyCode())
        is_regular = key_code in KEYCODE_TO_NAME
        if is_regular:
            self._finish_capture(parts)
            return

        if self.capture_target not in ("key_name", "event_key"):
            return
        self.capture_pending_parts = parts
        if self.capture_timer is not None:
            self.capture_timer.invalidate()
        self.capture_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.45, self, "captureModifierTimerFired:", None, False
        )

    def captureModifierTimerFired_(self, _timer) -> None:
        self.capture_timer = None
        if self.capture_pending_parts:
            self._finish_capture(self.capture_pending_parts)

    def _finish_capture(self, parts: frozenset[str]) -> None:
        target = self.capture_target
        value = format_hotkey(parts)
        if target == "toggle_hotkey":
            self.toggle_hotkey_field.setStringValue_(value)
            self._end_capture(restore_hotkeys=True)
            self.settingsChanged_(self.toggle_hotkey_field)
        elif target == "stop_hotkey":
            self.stop_hotkey_field.setStringValue_(value)
            self._end_capture(restore_hotkeys=True)
            self.settingsChanged_(self.stop_hotkey_field)
        elif target == "exit_hotkey":
            self.exit_hotkey_field.setStringValue_(value)
            self._end_capture(restore_hotkeys=True)
            self.settingsChanged_(self.exit_hotkey_field)
        elif target == "key_name":
            self.key_name_field.setStringValue_(value)
            self._end_capture(restore_hotkeys=True)
            self.settingsChanged_(self.key_name_field)
        elif target == "macro_hotkey":
            self.macro_hotkey_field.setStringValue_(value)
            self._end_capture(restore_hotkeys=True)
            self.macroDraftChanged_(self.macro_hotkey_field)
        elif target == "event_key":
            event = self._current_event()
            if event is not None:
                event["key"] = value
                self.event_key_field.setStringValue_(value)
                self.macro_dirty = True
            self._end_capture(restore_hotkeys=True)
            self._save_macro_draft()

    def _end_capture(self, *, restore_hotkeys: bool) -> None:
        if self.capture_monitor is not None:
            NSEvent.removeMonitor_(self.capture_monitor)
            self.capture_monitor = None
        if self.capture_timer is not None:
            self.capture_timer.invalidate()
            self.capture_timer = None
        should_restore = restore_hotkeys and self.capture_restore_hotkeys
        self.capture_target = None
        self.capture_display = None
        self.capture_pending_parts = frozenset()
        self.capture_restore_hotkeys = False
        if should_restore:
            self.state_payload = self.service.enable_global_hotkeys()

    def numberOfRowsInTableView_(self, table_view) -> int:
        if table_view == self.macro_table:
            return len(self.state_payload.get("macros", []))
        if table_view == self.event_table and self.macro_draft:
            return len(self.macro_draft.get("events", []))
        return 0

    def tableView_objectValueForTableColumn_row_(
        self, table_view, table_column, row: int
    ):
        identifier = str(table_column.identifier())
        if table_view == self.macro_table:
            macros = self.state_payload.get("macros", [])
            if row >= len(macros):
                return ""
            macro = macros[row]
            return macro["name"] if identifier == "name" else macro.get("hotkey", "")

        if table_view != self.event_table or not self.macro_draft:
            return ""
        events = self.macro_draft.get("events", [])
        if row >= len(events):
            return ""
        event = events[row]
        kind = event.get("kind", "delay")
        if identifier == "index":
            return str(row + 1)
        if identifier == "type":
            return EVENT_LABELS.get(kind, kind)
        if identifier == "duration":
            return f'{event.get("duration", 0):g}s' if kind == "delay" else ""
        if kind in ("key_down", "key_up"):
            return event.get("key", "Space")
        if kind in ("mouse_down", "mouse_up"):
            return str(event.get("mouse_button", "left")).title()
        if kind == "mouse_move":
            mode = str(event.get("move_mode", "absolute")).title()
            return f'{mode}: {event.get("x", 0)}, {event.get("y", 0)}'
        return "Wait"

    def tableViewSelectionDidChange_(self, notification) -> None:
        table = notification.object()
        if table == self.macro_table:
            row = self.macro_table.selectedRow()
            macros = self.state_payload.get("macros", [])
            if 0 <= row < len(macros):
                macro_id = macros[row]["id"]
                if macro_id != self.selected_macro_id:
                    self._select_macro_id(macro_id)
        elif table == self.event_table:
            self._show_event_inspector(self._current_event())


def _install_application_menu(app: NSApplication) -> None:
    main_menu = NSMenu.alloc().initWithTitle_(APP_TITLE)
    app_item = NSMenuItem.alloc().init()
    main_menu.addItem_(app_item)
    app_menu = NSMenu.alloc().initWithTitle_(APP_TITLE)
    app_item.setSubmenu_(app_menu)
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"Quit {APP_TITLE}", "terminate:", "q"
    )
    app_menu.addItem_(quit_item)

    edit_item = NSMenuItem.alloc().init()
    main_menu.addItem_(edit_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_item.setSubmenu_(edit_menu)
    for title, action, key in (
        ("Undo", "undo:", "z"),
        ("Redo", "redo:", "Z"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    ):
        edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
    app.setMainMenu_(main_menu)


def run_native_app() -> int:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _install_application_menu(app)
    service = AutoClickerService()
    delegate = AutoClickerAppDelegate.alloc().initWithService_(service)
    app.setDelegate_(delegate)

    def handle_signal(_signum, _frame) -> None:
        delegate.requestTerminate()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    app.run()
    return 0
