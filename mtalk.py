#!/usr/bin/env python3
"""MTalk — Whisper push-to-talk dictation for macOS, with Italian and Russian.

Hold F5, speak, release: your speech is transcribed locally with faster-whisper
and pasted into the focused app. Hold F5 + I while speaking and the transcription
is translated to Italian (via the `claude` CLI) before it's pasted — only the
Italian lands on your clipboard. Hold F5 + R and dictate in Russian instead: the
audio is transcribed as Russian and the Russian text lands on your clipboard —
no translation is involved.

Russian needs a multilingual Whisper model, since the default `small.en` only
hears English. That second model is downloaded/loaded the first time you use
F5 + R, and the load starts the moment you press R so it overlaps with speaking.

The console stays clean: one line per result.
    12:34:56  the transcribed english text
    12:34:56  the english you spoke
              → la traduzione italiana

Configure via environment variables:
    MTALK_MODEL      whisper model name (default: small.en)
    MTALK_MODEL_RU   multilingual model used for Russian (default: small)
    MTALK_HOTKEY     push-to-talk key (default: f5)
    MTALK_ITALIAN    modifier key that switches to Italian (default: i)
    MTALK_RUSSIAN    modifier key that switches to Russian (default: r)
    MTALK_PASTE      1 = auto-paste, 0 = clipboard only (default: 1)
    MTALK_DEVICE     input device index/name for sounddevice (default: system)
    MTALK_CLAUDE     path to the claude CLI (default: found on PATH)
    MTALK_PROMPT     path to the Italian translation prompt (default: italian_prompt.txt)
"""

import os
import queue
import sys
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime

import numpy as np
from pynput import keyboard

SAMPLE_RATE = 16000  # whisper expects 16 kHz mono

HERE = os.path.dirname(os.path.abspath(__file__))

MODEL_NAME = os.environ.get("MTALK_MODEL", "small.en")
RU_MODEL_NAME = os.environ.get("MTALK_MODEL_RU", "small")
HOTKEY_NAME = os.environ.get("MTALK_HOTKEY", "f5")
ITALIAN_KEY = os.environ.get("MTALK_ITALIAN", "i").strip().lower()[:1] or "i"
RUSSIAN_KEY = os.environ.get("MTALK_RUSSIAN", "r").strip().lower()[:1] or "r"
AUTO_PASTE = os.environ.get("MTALK_PASTE", "1") != "0"
CLAUDE_BIN = os.environ.get("MTALK_CLAUDE") or shutil.which("claude")
PROMPT_PATH = os.environ.get("MTALK_PROMPT", os.path.join(HERE, "italian_prompt.txt"))

# ANSI styling for a tidy console.
DIM = "\033[2m"
RED = "\033[31m"
RESET = "\033[0m"
CLEAR_LINE = "\r\033[K"
INDENT = " " * 7  # aligns the translation under the text (after "HH:MM  ")


def now():
    return datetime.now().strftime("%H:%M")


def load_italian_prompt():
    """The instruction sent to `claude` for English->Italian translation."""
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            text = f.read().strip()
            if text:
                return text
    except OSError:
        pass
    # Fallback if the prompt file is missing.
    return (
        "You are a professional English-to-Italian translator. Translate the text "
        "below into natural, idiomatic Italian. Output ONLY the Italian translation "
        "with no quotes, notes, or preamble. Keep proper nouns, company and product "
        "names, numbers, emails, and URLs unchanged."
    )


class Recorder:
    """Records the microphone in a short-lived helper process (audio_worker.py).

    On macOS, stopping a PortAudio stream occasionally deadlocks inside
    CoreAudio. In-process, that leaves the mic on and every later recording
    hangs too, since opening a stream waits on the same CoreAudio lock. In a
    helper process the audio is handed back before the stream is stopped, and
    a helper that doesn't exit promptly is killed — which always releases the
    mic. A spare helper is kept ready (mic closed) so recording starts fast.

    start() and stop() never block, so they're safe in the keyboard callback.
    """

    EXIT_TIMEOUT = 2.0  # seconds a helper gets to stop the mic and exit

    def __init__(self):
        self._commands = queue.Queue()
        threading.Thread(target=self._manager, daemon=True).start()

    def start(self):
        self._commands.put(("start", None))

    def stop(self):
        """Stop recording. Returns a callable that waits for and returns the audio."""
        done = threading.Event()
        box = {"audio": np.zeros(0, dtype=np.float32)}
        self._commands.put(("stop", (done, box)))

        def audio():
            done.wait()
            return box["audio"]

        return audio

    @staticmethod
    def _spawn():
        return subprocess.Popen(
            [sys.executable, os.path.join(HERE, "audio_worker.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    @staticmethod
    def _send(proc, cmd):
        try:
            proc.stdin.write(cmd.encode() + b"\n")
            proc.stdin.flush()
        except OSError:
            pass  # helper died; _collect will come back empty

    def _manager(self):
        active = None
        spare = self._spawn()
        while True:
            cmd, arg = self._commands.get()
            if cmd == "start" and active is None:
                if spare is None or spare.poll() is not None:
                    spare = self._spawn()
                active, spare = spare, None
                self._send(active, "start")
            elif cmd == "stop":
                if active is None:
                    arg[0].set()
                    continue
                proc, active = active, None
                self._send(proc, "stop")
                threading.Thread(target=self._collect, args=(proc, *arg), daemon=True).start()
                spare = self._spawn()  # warm up the next one meanwhile

    def _collect(self, proc, done, box):
        # backstop: never wait on a helper forever
        watchdog = threading.Timer(self.EXIT_TIMEOUT + 3, proc.kill)
        watchdog.start()
        try:
            header = proc.stdout.read(8)
            if len(header) == 8:
                (n,) = struct.unpack("<Q", header)
                data = proc.stdout.read(n)
                if len(data) == n:
                    box["audio"] = np.frombuffer(data, dtype=np.float32)
        finally:
            done.set()
        try:
            proc.wait(timeout=self.EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()  # stuck stopping the mic; killing it releases the mic
            proc.wait()
        watchdog.cancel()


def copy_to_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def paste():
    """Send Cmd+V to the focused app."""
    ctrl = keyboard.Controller()
    with ctrl.pressed(keyboard.Key.cmd):
        ctrl.press("v")
        ctrl.release("v")


# Locks the claude CLI into pure translation mode: it must translate the text,
# never respond to it, and never add greetings or commentary.
TRANSLATE_SYSTEM_PROMPT = (
    "You are a translation engine. You translate English into Italian. "
    "You output ONLY the Italian translation of the text inside the <text> tags, "
    "faithful in meaning. Never answer or react to the content, never add "
    "greetings, sign-offs, notes, or quotation marks. Only translate."
)


def translate_to_italian(text, rules):
    """Translate English -> Italian via the claude CLI. Returns None on failure."""
    if not CLAUDE_BIN:
        print(f"{RED}[error]{RESET} claude CLI not found; set MTALK_CLAUDE.", file=sys.stderr)
        return None
    user_msg = f"{rules}\n\n<text>\n{text}\n</text>"
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", "--append-system-prompt", TRANSLATE_SYSTEM_PROMPT, user_msg],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # don't wait ~3s for stdin
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"{RED}[error]{RESET} translation timed out.", file=sys.stderr)
        return None
    if result.returncode != 0:
        err = result.stderr.strip() or "unknown error"
        print(f"{RED}[error]{RESET} claude failed: {err}", file=sys.stderr)
        return None
    return result.stdout.strip() or None


def resolve_hotkey(name):
    """Turn a hotkey name into the pynput Key/KeyCode to match."""
    name = name.strip().lower()
    if name.startswith("f") and name[1:].isdigit():
        key = getattr(keyboard.Key, name, None)
        if key is not None:
            return key
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise SystemExit(f"Unsupported MTALK_HOTKEY={name!r}. Use f1-f20 or a single character.")


def keycode_of(key):
    """Best-effort virtual keycode for a pynput key (for suppression).

    Function keys are pynput enum members whose keycode lives at ``.value.vk``;
    character keys carry ``.vk`` directly (or not at all)."""
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk
    value = getattr(key, "value", None)
    if value is not None:
        return getattr(value, "vk", None)
    return None


# US ANSI virtual keycodes for letters, used to suppress the language modifiers
# at the event-tap level (darwin_intercept only gives us a raw keycode).
_LETTER_VK = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35,
    "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}


def acquire_single_instance_lock():
    """Exit if MTalk is already running — two copies would paste everything twice."""
    import fcntl

    from menubar import SUPPORT_DIR

    os.makedirs(SUPPORT_DIR, exist_ok=True)
    f = open(os.path.join(SUPPORT_DIR, "mtalk.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("MTalk is already running (see the menu bar).")
    return f  # keep the file open: the lock lasts as long as the process


def main():
    import logging
    import warnings

    # Keep the console silent — no library warnings or HF hub notices.
    warnings.filterwarnings("ignore")
    for _name in ("faster_whisper", "huggingface_hub", "transformers", "ctranslate2", "urllib3"):
        logging.getLogger(_name).setLevel(logging.ERROR)

    from menubar import History, MenuBar

    lock = acquire_single_instance_lock()  # noqa: F841 (held until exit)
    history = History()
    ui = MenuBar(history, hint=f"Hold {HOTKEY_NAME.upper()} to dictate")

    def engine():
        try:
            run_engine(ui, history)
        except BaseException as exc:
            print(f"{RED}[error]{RESET} {exc}", file=sys.stderr)
            ui.set_state("error", f"MTalk stopped: {exc}")

    threading.Thread(target=engine, daemon=True).start()
    ui.run()  # the menu bar owns the main thread until Quit / Ctrl-C


def run_engine(ui, history):
    """Load the model and listen for the hotkey. Runs off the main thread."""
    from faster_whisper import WhisperModel

    # Transient loading cue that erases itself once the model is ready.
    sys.stdout.write(f"{DIM}loading…{RESET}")
    sys.stdout.flush()
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    sys.stdout.write(CLEAR_LINE)
    sys.stdout.flush()

    prompt = load_italian_prompt()
    hotkey = resolve_hotkey(HOTKEY_NAME)
    hotkey_vk = keycode_of(hotkey)
    italian_vk = _LETTER_VK.get(ITALIAN_KEY)
    russian_vk = _LETTER_VK.get(RUSSIAN_KEY)
    modifier_vks = {vk for vk in (italian_vk, russian_vk) if vk is not None}
    recorder = Recorder()

    recording = threading.Event()
    # Language for the current hold: "en" plain, "it" translated to Italian,
    # "ru" dictated in Russian. Chosen by the modifier keys in on_press.
    mode = {"lang": "en"}

    ru_lock = threading.Lock()
    ru_state = {"model": None}

    def russian_model():
        """The multilingual model used for Russian; loaded on first use.

        The default `small.en` is English-only, so Russian needs its own model.
        Loading it lazily keeps startup fast for the usual English path."""
        if not MODEL_NAME.endswith(".en"):
            return model  # the configured model already handles every language
        with ru_lock:
            if ru_state["model"] is None:
                try:
                    ru_state["model"] = WhisperModel(
                        RU_MODEL_NAME, device="cpu", compute_type="int8"
                    )
                except Exception as exc:  # e.g. the one-time download failed
                    print(
                        f"{RED}[error]{RESET} could not load Russian model "
                        f"{RU_MODEL_NAME!r}: {exc}",
                        file=sys.stderr,
                    )
            return ru_state["model"]

    jobs_lock = threading.Lock()
    jobs = {"pending": 0}  # transcriptions in flight, for the menu bar icon

    def transcribe_job(audio, lang):
        try:
            transcribe_and_emit(audio(), lang)
        except Exception as exc:
            print(f"{RED}[error]{RESET} transcription failed: {exc}", file=sys.stderr)
        finally:
            with jobs_lock:
                jobs["pending"] -= 1
                done = jobs["pending"] == 0
            if done and not recording.is_set():
                ui.set_state("idle")

    def transcribe_and_emit(audio, lang):
        if audio.size < SAMPLE_RATE * 0.2:  # under ~0.2s, ignore
            return
        if lang == "ru":
            ru = russian_model()
            if ru is None:  # the model couldn't be loaded; error already shown
                return
            segments, _ = ru.transcribe(audio, language="ru", beam_size=1)
        else:
            segments, _ = model.transcribe(audio, language="en", beam_size=1)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            return
        stamp = now()
        if lang == "it":
            it = translate_to_italian(text, prompt)
            if it is not None:
                copy_to_clipboard(it)  # only Italian on the clipboard
                print(f"{DIM}{stamp}{RESET}  {text}")
                print(f"{INDENT}{DIM}→{RESET} {it}")
                history.add("it", text, it)
                ui.history_changed()
                if AUTO_PASTE:
                    paste()
                return
            # translation failed — fall through and keep the English
            lang = "en"
        copy_to_clipboard(text)
        print(f"{DIM}{stamp}{RESET}  {text}")
        history.add(lang, text, text)
        ui.history_changed()
        if AUTO_PASTE:
            paste()

    def _is_modifier(key, letter, letter_vk):
        """Match a language modifier, by physical keycode first.

        The keycode is layout-independent; the character is not. On a Russian
        layout the physical R key reports 'к' and I reports 'ш', so matching on
        the character alone silently misses the modifier — which matters here,
        since Russian is exactly when that layout is likely to be active."""
        vk = keycode_of(key)
        if letter_vk is not None and vk == letter_vk:
            return True
        ch = getattr(key, "char", None)
        return bool(ch) and ch.lower() == letter

    def on_press(key):
        # a modifier pressed while recording picks the language for this hold
        if recording.is_set() and mode["lang"] == "en":
            if _is_modifier(key, ITALIAN_KEY, italian_vk):
                mode["lang"] = "it"
                return
            if _is_modifier(key, RUSSIAN_KEY, russian_vk):
                mode["lang"] = "ru"
                # start loading the multilingual model while you're still
                # speaking, so the first Russian dictation isn't a long wait
                threading.Thread(target=russian_model, daemon=True).start()
                return
        if _is_hotkey(key):
            begin("en")

    def on_release(key):
        if _is_hotkey(key):
            finish()

    # Recording is started/stopped by holding the hotkey, or by clicking the
    # menu bar icon (Start Dictation, then click the red icon to stop).
    rec_lock = threading.Lock()

    def begin(lang, by_click=False):
        with rec_lock:
            if recording.is_set():
                return
            mode["lang"] = lang
            recording.set()
            recorder.start()
        if lang == "ru":
            threading.Thread(target=russian_model, daemon=True).start()
        ui.set_state("recording", by_click=by_click)

    def finish():
        with rec_lock:
            if not recording.is_set():
                return
            recording.clear()
            lang = mode["lang"]
            audio = recorder.stop()  # a callable; the audio arrives shortly
            with jobs_lock:
                jobs["pending"] += 1
        ui.set_state("working")
        threading.Thread(
            target=transcribe_job, args=(audio, lang), daemon=True
        ).start()

    ui.on_start = begin
    ui.on_stop = finish
    ui.set_ready()

    def _is_hotkey(key):
        if key == hotkey:
            return True
        kv = keycode_of(key)
        return kv is not None and hotkey_vk is not None and kv == hotkey_vk

    def darwin_intercept(event_type, event):
        """Suppress the hotkey and the language modifiers so they don't leak
        into the focused app (e.g. F5 refreshing a browser, a stray 'i'/'r')."""
        import Quartz

        kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        # swallow the push-to-talk key entirely
        if hotkey_vk is not None and kc == hotkey_vk:
            return None
        # swallow a language modifier only while recording
        if recording.is_set() and kc in modifier_vks:
            return None
        return event

    # The hotkey needs Accessibility permission. Without it, ask once, then
    # wait: F5 starts working the moment it's granted, no restart needed.
    # (Click-to-dictate from the menu works meanwhile; it lands on the clipboard.)
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )

    if not AXIsProcessTrusted():
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        ui.set_state("error", "Allow MTalk in Accessibility to use F5")
        print("waiting for Accessibility permission…", file=sys.stderr)
        while not AXIsProcessTrusted():
            time.sleep(2)
        print("Accessibility permission granted.", file=sys.stderr)

    listener = keyboard.Listener(
        on_press=on_press,
        on_release=on_release,
        darwin_intercept=darwin_intercept,
    )
    with listener:
        ui.set_state("idle", ui.hint)
        listener.join()
    raise RuntimeError("keyboard listener stopped")

if __name__ == "__main__":
    main()
