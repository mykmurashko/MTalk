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
RU_MODEL_NAME = os.environ.get("MTALK_MODEL_RU", "small")
HOTKEY_NAME = os.environ.get("MTALK_HOTKEY", "f5")
ITALIAN_KEY = os.environ.get("MTALK_ITALIAN", "i").strip().lower()[:1] or "i"
RUSSIAN_KEY = os.environ.get("MTALK_RUSSIAN", "r").strip().lower()[:1] or "r"
AUTO_PASTE = os.environ.get("MTALK_PASTE", "1") != "0"
DEVICE = os.environ.get("MTALK_DEVICE") or None
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


def main():
    import logging
    import warnings

    # Keep the console silent — no library warnings or HF hub notices.
    warnings.filterwarnings("ignore")
    for _name in ("faster_whisper", "huggingface_hub", "transformers", "ctranslate2", "urllib3"):
        logging.getLogger(_name).setLevel(logging.ERROR)

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
                if AUTO_PASTE:
                    paste()
                return
            # translation failed — fall through and keep the English
        copy_to_clipboard(text)
        print(f"{DIM}{stamp}{RESET}  {text}")
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
        if _is_hotkey(key) and not recording.is_set():
            mode["lang"] = "en"
            recording.set()
            recorder.start()

    def on_release(key):
        if _is_hotkey(key) and recording.is_set():
            recording.clear()
            lang = mode["lang"]
            audio = recorder.stop()
            threading.Thread(
                target=transcribe_and_emit, args=(audio, lang), daemon=True
            ).start()

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
