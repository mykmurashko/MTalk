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

import CoreText
import objc
import Quartz
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSButton,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSInsetRect,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSPasteboard,
    NSPasteboardTypeString,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSStatusBar,
    NSTimer,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingMouseEnteredAndExited,
    NSVariableStatusItemLength,
    NSView,
    NSViewMaxXMargin,
    NSViewMinXMargin,
    NSViewWidthSizable,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/MTalk")
HISTORY_PATH = os.path.join(SUPPORT_DIR, "history.jsonl")
MAX_HISTORY = 500  # entries kept on disk and listed in the menu
TITLE_CHARS = 60   # menu entries are truncated to this; the tooltip has it all

# The icon is an "M" on a circle.
# state -> (circle colour, letter colour, alpha). A letter colour of None cuts
# the M out of the circle; those icons are template images, so macOS draws
# them in the menu bar's own colour (white on dark, black on light).
STATES = {
    "loading": ("blackColor", None, 0.4),
    "idle": ("blackColor", None, 1.0),
    "recording": ("systemRedColor", "whiteColor", 1.0),
    "working": ("blackColor", None, 1.0),  # plus a spinning ring, see SPIN_*
    "error": ("blackColor", None, 0.3),
}
ICON_SIZE = 18  # points; menu bar icons are ~16–18pt tall
SPIN_FRAMES = 12  # the "working" icon: an arc circling a slightly smaller M
SPIN_INTERVAL = 0.07  # seconds per frame


def _make_icon(circle, letter, alpha, spin=None):
    """The M logo. With `spin` (degrees) the circle shrinks a little and an
    arc at that angle runs around it: one frame of the loading animation."""
    size = NSMakeSize(ICON_SIZE, ICON_SIZE)
    inset = 1 if spin is None else 3.5
    font_size = 10.5 * (ICON_SIZE - 2 * inset) / (ICON_SIZE - 2)

    def draw(rect):
        colour = getattr(NSColor, circle)()
        colour.colorWithAlphaComponent_(alpha).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSInsetRect(rect, inset, inset)).fill()

        if spin is not None:
            centre, radius = NSMakePoint(ICON_SIZE / 2, ICON_SIZE / 2), ICON_SIZE / 2 - 0.9
            track = NSBezierPath.bezierPathWithOvalInRect_(NSInsetRect(rect, 0.9, 0.9))
            track.setLineWidth_(1.5)
            colour.colorWithAlphaComponent_(0.25).setStroke()
            track.stroke()
            arc = NSBezierPath.bezierPath()
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                centre, radius, spin, spin - 110, True
            )
            arc.setLineWidth_(1.5)
            arc.setLineCapStyle_(1)  # round
            colour.setStroke()
            arc.stroke()

        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(font_size, NSFontWeightBold),
            NSForegroundColorAttributeName: getattr(NSColor, letter or "blackColor")(),
        }
        text = NSAttributedString.alloc().initWithString_attributes_("M", attrs)
        line = CoreText.CTLineCreateWithAttributedString(text)
        # centre the glyph's actual outline, not its text box (which includes
        # side bearings and descender space and sits visibly off-centre)
        bounds = CoreText.CTLineGetBoundsWithOptions(
            line, CoreText.kCTLineBoundsUseGlyphPathBounds
        )
        x = (ICON_SIZE - bounds.size.width) / 2 - bounds.origin.x
        y = (ICON_SIZE - bounds.size.height) / 2 - bounds.origin.y

        ctx = NSGraphicsContext.currentContext().CGContext()
        Quartz.CGContextSaveGState(ctx)
        if letter is None:  # punch the M out of the circle
            Quartz.CGContextSetBlendMode(ctx, Quartz.kCGBlendModeDestinationOut)
        Quartz.CGContextSetTextPosition(ctx, x, y)
        CoreText.CTLineDraw(line, ctx)
        Quartz.CGContextRestoreGState(ctx)
        return True

    image = NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    image.setTemplate_(letter is None)
    image.setAccessibilityDescription_("MTalk")
    return image


LANG_TAGS = {"it": "IT  ", "ru": "RU  "}


def write_iconset(path):
    """Write the app icon (the idle logo, large) as a .iconset for iconutil."""
    from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep, NSGraphicsContext

    os.makedirs(path, exist_ok=True)
    icon = _make_icon(*STATES["idle"])
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, px, px, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0
            )
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(
                NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
            )
            icon.drawInRect_(((0, 0), (px, px)))
            NSGraphicsContext.restoreGraphicsState()
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None).writeToFile_atomically_(
                os.path.join(path, name), True
            )


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


class _PillButton(NSButton):
    """A capsule-shaped button for the bottom row of the menu."""

    lang = None

    def initWithFrame_(self, frame):
        self = objc.super(_PillButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setBordered_(False)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(frame.size.height / 2)
        self.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightMedium))
        self.paintHover_(False)
        return self

    def paintHover_(self, hover):
        alpha = 0.22 if hover else 0.1

        def paint():  # resolve the dynamic colour for this view's appearance
            self.layer().setBackgroundColor_(
                NSColor.labelColor().colorWithAlphaComponent_(alpha).CGColor()
            )

        self.effectiveAppearance().performAsCurrentDrawingAppearance_(paint)

    def viewDidChangeEffectiveAppearance(self):
        self.paintHover_(False)

    def setEnabled_(self, enabled):
        objc.super(_PillButton, self).setEnabled_(enabled)
        self.setAlphaValue_(1.0 if enabled else 0.4)

    def updateTrackingAreas(self):
        objc.super(_PillButton, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways,
                self,
                None,
            )
        )

    def mouseEntered_(self, event):
        self.paintHover_(self.isEnabled())

    def mouseExited_(self, event):
        self.paintHover_(False)


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

    def startPill_(self, sender):
        # buttons inside a menu don't close it, so close it ourselves
        self.owner.menu.cancelTracking()
        if self.owner.on_start is not None:
            self.owner.on_start(sender.lang, by_click=True)

    def spinTick_(self, timer):
        self.owner._spin_tick()

    def iconClicked_(self, sender):
        # only fires while the menu is detached, i.e. during a click recording
        if self.owner.on_stop is not None:
            self.owner.on_stop()

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
        self._icons = {}
        self._ready = False
        self._spin_timer = None
        self._spin_frame = 0
        # set by the engine: on_start(lang, by_click=True), on_stop()
        self.on_start = None
        self.on_stop = None

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
        self.item.button().setTarget_(self.target)
        self.item.button().setAction_("iconClicked:")

        self._apply_state("loading")
        self._rebuild_menu()

    # --- thread-safe API -------------------------------------------------

    def set_state(self, state, status_line=None, by_click=False):
        AppHelper.callAfter(self._apply_state, state, status_line, by_click)

    def set_ready(self):
        """The model is loaded: enable Start Dictation in the menu."""
        def ready():
            self._ready = True
            self._rebuild_menu()
        AppHelper.callAfter(ready)

    def history_changed(self):
        AppHelper.callAfter(self._rebuild_menu)

    def quit(self):
        AppHelper.callAfter(self.app.terminate_, None)

    def run(self):
        # installInterrupt lets Ctrl-C quit when run from a terminal
        AppHelper.runEventLoop(installInterrupt=True)

    # --- main thread only -------------------------------------------------

    def _apply_state(self, state, status_line=None, by_click=False):
        if state == "working":
            self._start_spinner()
        else:
            self._stop_spinner()
            if state not in self._icons:
                self._icons[state] = _make_icon(*STATES.get(state, STATES["idle"]))
            self.item.button().setImage_(self._icons[state])
        if state == "recording" and by_click:
            # detach the menu so one click on the red icon stops recording
            self.item.setMenu_(None)
        elif self.item.menu() is None:
            self.item.setMenu_(self.menu)
        if status_line is not None:
            self._status_line = status_line
            self._rebuild_menu()
        elif state == "idle" and self._status_line != self.hint:
            self._status_line = self.hint
            self._rebuild_menu()

    def _start_spinner(self):
        if self._spin_timer is not None:
            return
        if "spin" not in self._icons:
            # clockwise, starting at 12 o'clock
            self._icons["spin"] = [
                _make_icon(*STATES["working"], spin=90 - i * 360 / SPIN_FRAMES)
                for i in range(SPIN_FRAMES)
            ]
        self._spin_frame = 0
        self._spin_tick()
        self._spin_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            SPIN_INTERVAL, self.target, "spinTick:", None, True
        )
        # common modes: keep spinning while the menu is open
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._spin_timer, NSRunLoopCommonModes)

    def _stop_spinner(self):
        if self._spin_timer is not None:
            self._spin_timer.invalidate()
            self._spin_timer = None

    def _spin_tick(self):
        frames = self._icons["spin"]
        self.item.button().setImage_(frames[self._spin_frame % len(frames)])
        self._spin_frame += 1

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
        self.menu.addItem_(NSMenuItem.separatorItem())
        self.menu.addItem_(self._pills_item())

    def _pills_item(self):
        """A row of pill buttons that start a click recording: dictation
        starts at once, and a click on the red icon stops it."""
        width, height, pad, gap = 300, 40, 12, 8
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        view.setAutoresizingMask_(NSViewWidthSizable)  # stretch with the menu

        pills = (
            ("English", "en", "Dictate in English"),
            ("Italian", "it", "Speak English, get Italian"),
            ("Russian", "ru", "Dictate in Russian"),
        )
        pill_w = (width - 2 * pad - gap * (len(pills) - 1)) / len(pills)
        for i, (title, lang, tip) in enumerate(pills):
            frame = NSMakeRect(pad + i * (pill_w + gap), 7, pill_w, height - 14)
            button = _PillButton.alloc().initWithFrame_(frame)
            button.setTitle_(title)
            button.setToolTip_(tip)
            button.setEnabled_(self._ready)
            button.setTarget_(self.target)
            button.setAction_("startPill:")
            button.lang = lang
            # keep the pills evenly spread when the menu is wider than `width`
            button.setAutoresizingMask_(
                NSViewWidthSizable | NSViewMinXMargin | NSViewMaxXMargin
            )
            view.addSubview_(button)

        item = NSMenuItem.alloc().init()
        item.setView_(view)
        return item
