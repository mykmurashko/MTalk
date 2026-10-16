"""Menu bar icon and dictation history for MTalk.

The icon shows what MTalk is doing (loading, idle, recording, transcribing).
Clicking it lists everything you've dictated, newest first; click an entry to
copy it to the clipboard. History is kept in
~/Library/Application Support/MTalk/history.jsonl so it survives restarts.

All AppKit calls happen on the main thread: other threads go through
`AppHelper.callAfter`.
"""

import json
import os
import threading
from datetime import datetime

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSColor,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSPasteboard,
    NSPasteboardTypeString,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/MTalk")
HISTORY_PATH = os.path.join(SUPPORT_DIR, "history.jsonl")
MAX_HISTORY = 500  # entries kept on disk and listed in the menu
TITLE_CHARS = 60   # menu entries are truncated to this; the tooltip has it all

# state -> (SF Symbol, tint). None tint = normal menu bar colour.
STATES = {
    "loading": ("hourglass", None),
    "idle": ("mic", None),
    "recording": ("mic.fill", "systemRedColor"),
    "working": ("waveform", None),
    "error": ("mic.slash", None),
}

LANG_TAGS = {"it": "IT  ", "ru": "RU  "}


class History:
    """Append-only list of dictations, persisted as JSON lines."""

    def __init__(self, path=HISTORY_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.entries = self._load()

    def _load(self):
        entries = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        pass  # skip a corrupt line rather than lose the rest
        except OSError:
            pass
        return entries[-MAX_HISTORY:]

    def add(self, lang, text, output):
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "lang": lang,
            "text": text,      # what was heard
            "output": output,  # what was copied (the Italian, for F5 + I)
        }
        with self._lock:
            self.entries.append(entry)
            trimmed = len(self.entries) > MAX_HISTORY
            self.entries = self.entries[-MAX_HISTORY:]
            try:
                os.makedirs(SUPPORT_DIR, exist_ok=True)
                if trimmed:
                    self._rewrite()
                else:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass  # history is a convenience; never break dictation over it

    def clear(self):
        with self._lock:
            self.entries = []
            try:
                self._rewrite()
            except OSError:
                pass

    def snapshot(self):
        with self._lock:
            return list(self.entries)

    def _rewrite(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)


def _entry_title(entry):
    try:
        t = datetime.fromisoformat(entry["time"])
    except (KeyError, ValueError):
        t = None
    if t is None:
        stamp = ""
    elif t.date() == datetime.now().date():
        stamp = t.strftime("%H:%M")
    else:
        stamp = t.strftime("%b %d  %H:%M")
    text = " ".join(entry.get("output", "").split())  # flatten newlines
    if len(text) > TITLE_CHARS:
        text = text[: TITLE_CHARS - 1].rstrip() + "…"
    return f"{stamp}   {LANG_TAGS.get(entry.get('lang'), '')}{text}"


def _entry_tooltip(entry):
    output = entry.get("output", "")
    text = entry.get("text", "")
    if text and text != output:
        return f"{output}\n\n({text})"
    return output


class _MenuTarget(NSObject):
    """Receives menu clicks and forwards them to the MenuBar."""

    def initWithOwner_(self, owner):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def copyEntry_(self, sender):
        text = sender.representedObject()
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

    def clearHistory_(self, sender):
        self.owner.history.clear()
        self.owner._rebuild_menu()

    def quit_(self, sender):
        NSApplication.sharedApplication().terminate_(None)


class MenuBar:
    def __init__(self, history, hint):
        self.history = history
        self.hint = hint  # e.g. "Hold F5 to dictate"
        self._status_line = "Loading model…"

        self.app = NSApplication.sharedApplication()
        # menu bar only: no Dock icon, no app switcher entry
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.target = _MenuTarget.alloc().initWithOwner_(self)
        self.menu = NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.item.setMenu_(self.menu)

        self._apply_state("loading")
        self._rebuild_menu()

    # --- thread-safe API -------------------------------------------------

    def set_state(self, state, status_line=None):
        AppHelper.callAfter(self._apply_state, state, status_line)

    def history_changed(self):
        AppHelper.callAfter(self._rebuild_menu)

    def quit(self):
        AppHelper.callAfter(self.app.terminate_, None)

    def run(self):
        # installInterrupt lets Ctrl-C quit when run from a terminal
        AppHelper.runEventLoop(installInterrupt=True)

    # --- main thread only -------------------------------------------------

    def _apply_state(self, state, status_line=None):
        symbol, tint = STATES.get(state, STATES["idle"])
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "MTalk")
        button = self.item.button()
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
        else:  # very old macOS without SF Symbols
            button.setTitle_("MTalk")
        button.setContentTintColor_(getattr(NSColor, tint)() if tint else None)
        if status_line is not None:
            self._status_line = status_line
            self._rebuild_menu()
        elif state == "idle" and self._status_line != self.hint:
            self._status_line = self.hint
            self._rebuild_menu()

    def _add(self, title, action=None, enabled=True, key=""):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        mi.setEnabled_(enabled)
        if action is not None:
            mi.setTarget_(self.target)
        self.menu.addItem_(mi)
        return mi

    def _rebuild_menu(self):
        self.menu.removeAllItems()
        self._add(self._status_line, enabled=False)
        self.menu.addItem_(NSMenuItem.separatorItem())

        entries = self.history.snapshot()
        if not entries:
            self._add("No recordings yet", enabled=False)
        else:
            self._add("Click an entry to copy it", enabled=False)
            # newest first; the menu scrolls once it's taller than the screen
            for entry in reversed(entries):
                mi = self._add(_entry_title(entry), "copyEntry:")
                mi.setRepresentedObject_(entry.get("output", ""))
                mi.setToolTip_(_entry_tooltip(entry))

        self.menu.addItem_(NSMenuItem.separatorItem())
        if entries:
            self._add("Clear History", "clearHistory:")
        self._add("Quit MTalk", "quit:", key="q")
