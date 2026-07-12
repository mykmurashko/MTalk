#!/usr/bin/env python3
"""MTalk — Whisper push-to-talk dictation for macOS, with on-the-fly Italian.

Hold F5, speak, release: your speech is transcribed locally with faster-whisper
and pasted into the focused app. Hold F5 + I while speaking and the transcription
is translated to Italian (via the `claude` CLI) before it's pasted — only the
Italian lands on your clipboard.

The console stays clean: one line per result.
    12:34:56  the transcribed english text
    12:34:56  EN  the english you spoke
              IT  la traduzione italiana

Configure via environment variables:
    MTALK_MODEL      whisper model name (default: small.en)
    MTALK_HOTKEY     push-to-talk key (default: f5)
    MTALK_ITALIAN    modifier key that switches to Italian (default: i)
    MTALK_PASTE      1 = auto-paste, 0 = clipboard only (default: 1)
    MTALK_DEVICE     input device index/name for sounddevice (default: system)
    MTALK_CLAUDE     path to the claude CLI (default: found on PATH)
    MTALK_PROMPT     path to the Italian translation prompt (default: italian_prompt.txt)
"""

import os
import sys
import shutil
import subprocess
import threading
from datetime import datetime

import numpy as np
import sounddevice as sd
from pynput import keyboard

SAMPLE_RATE = 16000  # whisper expects 16 kHz mono
CHANNELS = 1

HERE = os.path.dirname(os.path.abspath(__file__))

MODEL_NAME = os.environ.get("MTALK_MODEL", "small.en")
HOTKEY_NAME = os.environ.get("MTALK_HOTKEY", "f5")
ITALIAN_KEY = os.environ.get("MTALK_ITALIAN", "i").strip().lower()[:1] or "i"
AUTO_PASTE = os.environ.get("MTALK_PASTE", "1") != "0"
DEVICE = os.environ.get("MTALK_DEVICE") or None
CLAUDE_BIN = os.environ.get("MTALK_CLAUDE") or shutil.which("claude")
PROMPT_PATH = os.environ.get("MTALK_PROMPT", os.path.join(HERE, "italian_prompt.txt"))

# ANSI styling for a tidy console.
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def now():
    return datetime.now().strftime("%H:%M:%S")


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
    """Captures microphone audio into memory while active."""

    def __init__(self):
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._frames.append(indata.copy())

    def start(self):
        with self._lock:
            self._frames = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=DEVICE,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._frames, axis=0)
        return audio.reshape(-1).astype(np.float32)


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
    """Best-effort virtual keycode for a pynput key (for suppression)."""
    return getattr(key, "vk", None)


# US ANSI virtual keycodes for letters, used to suppress the Italian modifier
# at the event-tap level (darwin_intercept only gives us a raw keycode).
_LETTER_VK = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35,
    "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}


def main():
    from faster_whisper import WhisperModel
    import logging

    logging.getLogger("faster_whisper").setLevel(logging.ERROR)

    print(f"{DIM}MTalk — loading whisper '{MODEL_NAME}'…{RESET}")
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")

    prompt = load_italian_prompt()
    hotkey = resolve_hotkey(HOTKEY_NAME)
    hotkey_vk = keycode_of(hotkey)
    italian_vk = _LETTER_VK.get(ITALIAN_KEY)
    recorder = Recorder()

    recording = threading.Event()
    italian = threading.Event()

    def transcribe_and_emit(audio, to_italian):
        if audio.size < SAMPLE_RATE * 0.2:  # under ~0.2s, ignore
            return
        segments, _ = model.transcribe(audio, language="en", beam_size=1)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            return
        if to_italian:
            it = translate_to_italian(text, prompt)
            if it is None:
                # fall back to English so nothing is lost
                copy_to_clipboard(text)
                print(f"{DIM}{now()}{RESET}  {text}  {DIM}(translation failed){RESET}")
                if AUTO_PASTE:
                    paste()
                return
            copy_to_clipboard(it)  # only Italian on the clipboard
            print(f"{DIM}{now()}{RESET}")
            print(f"  {DIM}EN{RESET}  {text}")
            print(f"  {CYAN}IT{RESET}  {it}")
            if AUTO_PASTE:
                paste()
        else:
            copy_to_clipboard(text)
            print(f"{DIM}{now()}{RESET}  {text}")
            if AUTO_PASTE:
                paste()

    def on_press(key):
        # switch to Italian mode if the modifier is pressed while recording
        if recording.is_set():
            ch = getattr(key, "char", None)
            if ch and ch.lower() == ITALIAN_KEY:
                italian.set()
                return
        if _is_hotkey(key) and not recording.is_set():
            italian.clear()
            recording.set()
            recorder.start()

    def on_release(key):
        if _is_hotkey(key) and recording.is_set():
            recording.clear()
            to_italian = italian.is_set()
            audio = recorder.stop()
            threading.Thread(
                target=transcribe_and_emit, args=(audio, to_italian), daemon=True
            ).start()

    def _is_hotkey(key):
        if key == hotkey:
            return True
        kv = keycode_of(key)
        return kv is not None and hotkey_vk is not None and kv == hotkey_vk

    def darwin_intercept(event_type, event):
        """Suppress the hotkey and the Italian modifier so they don't leak into
        the focused app (e.g. F5 refreshing a browser, or a stray 'i')."""
        import Quartz

        kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        # swallow the push-to-talk key entirely
        if hotkey_vk is not None and kc == hotkey_vk:
            return None
        # swallow the Italian modifier only while recording
        if recording.is_set() and italian_vk is not None and kc == italian_vk:
            return None
        return event

    paste_mode = "auto-paste" if AUTO_PASTE else "clipboard only"
    tr = "on" if CLAUDE_BIN else f"{RED}unavailable{RESET}"
    print(
        f"{GREEN}MTalk ready{RESET} {DIM}·{RESET} hold [{HOTKEY_NAME.upper()}] to dictate "
        f"{DIM}·{RESET} hold [{HOTKEY_NAME.upper()}+{ITALIAN_KEY.upper()}] for Italian ({tr}) "
        f"{DIM}·{RESET} {paste_mode} {DIM}·{RESET} Ctrl+C to quit"
    )
    listener = keyboard.Listener(
        on_press=on_press,
        on_release=on_release,
        darwin_intercept=darwin_intercept,
    )
    with listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            print(f"\n{DIM}bye.{RESET}")


if __name__ == "__main__":
    main()
