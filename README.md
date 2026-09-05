# MTalk — Whisper push-to-talk dictation (macOS), with Italian and Russian

Hold **F5**, speak, release — your words are transcribed locally with Whisper and
pasted into the focused app. Three modes, all on one key:

| Hold | You speak | You get |
| ---- | --------- | ------- |
| **F5** | English | the English transcription |
| **F5 + I** | English | the **Italian** translation (only the Italian is on the clipboard) |
| **F5 + R** | **Russian** | the Russian transcription — no translation involved |

Transcription is fully local; the Italian translation uses the `claude` CLI you're
already logged in to (no API key needed).

The console stays clean — one entry per result:

```
14:02:11  Let's ship the update on Friday.
14:03:40  can you confirm the meeting at 3 pm with the team
          → Può confermare la riunione alle 15:00 con il team?
14:05:02  Привет, давай отправим обновление в пятницу.
```

---

## What's new vs. a plain Whisper dictation script

1. **The built-in F5 key works now.** On newer Macs the F5 key has a 🎤 (Dictation)
   icon and pressing it on the *built-in* keyboard fires macOS Dictation instead of
   sending F5 — so the helper never saw it (external keyboards were unaffected).
   MTalk ships a one-command fix (see step 3).
2. **Hold F5 + I to translate to Italian** as you dictate.
3. **Hold F5 + R to dictate in Russian** — transcribed as Russian, straight to
   the clipboard, with no translation step in between.
4. **A clean CLI** — just a timestamp and the text.

---

## 1. Install

You need **Python 3.9+**, **Homebrew**, and the **`claude` CLI** (logged in).

```bash
# system audio library used by sounddevice
brew install portaudio

cd MTalk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm the `claude` CLI is available and authenticated (used for Italian):

```bash
claude -p "Translate to Italian, output only: hello" </dev/null
# → ciao
```

## 2. Grant macOS permissions (required)

MTalk listens for a global hotkey and types into other apps, so macOS must trust
the **terminal app you run it from** (Terminal.app or iTerm). Open
**System Settings → Privacy & Security** and add your terminal to:

- **Accessibility** — for the global hotkey + auto-paste
- **Input Monitoring** — to detect the hotkey
- **Microphone** — to record audio

After granting, **fully quit and reopen the terminal**. (If the hotkey does
nothing, this is almost always the cause.)

## 3. Fix the built-in F5 / mic key (one time)

Newer Macs map the built-in F5 key to Dictation. This script remaps that key's
hardware signal (`0xC000000CF`, "Voice Command") to a real **F5**
(`0x70000003E`) *below* the layer where macOS Dictation runs — so it becomes a
clean F5 on every keyboard and Dictation no longer hijacks it. It also installs a
LaunchAgent so the remap survives reboots. External keyboards already send a real
F5 and are unaffected.

```bash
./install-fnkey-fix.sh
```

Verify / undo:

```bash
hidutil property --get UserKeyMapping   # shows the active remap
./uninstall-fnkey-fix.sh                # removes it and restores Dictation
```

> **Do I also need to disable macOS Dictation?** No. The remap above intercepts the
> key before Dictation can react, so native Dictation never fires on it. If your
> team *prefers* to also turn Dictation off entirely (optional), do it manually:
> **System Settings → Keyboard → Dictation → turn the toggle Off**, or set
> **Dictation → Shortcut → Off**. This is not required for MTalk to work.

## 4. Run

```bash
cd MTalk
source .venv/bin/activate
python mtalk.py
```

Or double-click **`start.command`** (opens a terminal window — it still needs the
permissions from step 2).

Leave it running, then:

1. Click into Claude Code (or any app).
2. **Hold F5, speak, release** → English transcription appears at your cursor.
3. **Hold F5 + I, speak, release** → the Italian translation appears instead.
4. **Hold F5 + R and speak Russian, release** → the Russian text appears.
5. Hit Enter to send.

> **First time you use F5 + R** MTalk downloads a second, *multilingual* Whisper
> model (`small`, ~500 MB), because the default `small.en` is English-only and
> hears Russian as phonetic nonsense. The download starts the moment you press R,
> so it overlaps with you speaking; it happens once, then it's cached. Set
> `MTALK_MODEL` to a multilingual model (e.g. `small`, `medium`) and MTalk uses
> that one model for everything instead of loading a second.

## 5. The Italian translation prompt (edit to taste)

The translation rules live in **`italian_prompt.txt`** — edit that file to change
tone, register, or terminology. The default uses a professional **"Lei"** register
for business text and switches to **"tu"** when the text is clearly casual, keeps
proper nouns/numbers/URLs unchanged, and outputs only the Italian.

## 6. Configure (optional)

Set environment variables before launching:

```bash
# Bigger model = more accurate, slower. small.en (default) is a good balance.
MTALK_MODEL=medium.en python mtalk.py

# More accurate Russian (the second model, only used by F5 + R)
MTALK_MODEL_RU=medium python mtalk.py

# One multilingual model for everything — no second model to load
MTALK_MODEL=small python mtalk.py

# Change the hotkey or the language modifiers
MTALK_HOTKEY=f6 python mtalk.py
MTALK_ITALIAN=j python mtalk.py
MTALK_RUSSIAN=k python mtalk.py

# Clipboard only, no auto-paste
MTALK_PASTE=0 python mtalk.py
```

| Variable         | Default              | Meaning                                       |
| ---------------- | -------------------- | --------------------------------------------- |
| `MTALK_MODEL`    | `small.en`           | Whisper model (`tiny.en`→`large-v3`)          |
| `MTALK_MODEL_RU` | `small`              | Multilingual model used for Russian           |
| `MTALK_HOTKEY`   | `f5`                 | Push-to-talk key (`f1`–`f20` or a character)  |
| `MTALK_ITALIAN`  | `i`                  | Key held with the hotkey to translate         |
| `MTALK_RUSSIAN`  | `r`                  | Key held with the hotkey to dictate Russian   |
| `MTALK_PASTE`    | `1`                  | `1` auto-paste, `0` clipboard only            |
| `MTALK_DEVICE`   | system default       | Input device index/name for sounddevice       |
| `MTALK_CLAUDE`   | `claude` on PATH     | Path to the `claude` CLI                       |
| `MTALK_PROMPT`   | `italian_prompt.txt` | Path to the translation prompt                 |

Models fastest → most accurate: `tiny.en`, `base.en`, `small.en`, `medium.en`,
`large-v3`. On Apple Silicon, `small.en` transcribes a sentence in well under a
second. The `.en` models are English-only, which is why Russian uses its own
multilingual model — drop the `.en` suffix for the multilingual variant.

## Troubleshooting

- **Built-in F5 still triggers Dictation** → run `./install-fnkey-fix.sh`, then
  check `hidutil property --get UserKeyMapping`. If empty after a reboot, confirm
  `~/Library/LaunchAgents/com.mtalk.keyremap.plist` exists.
- **Hotkey does nothing** → Accessibility + Input Monitoring not granted to your
  terminal app. Re-check step 2 and fully restart the terminal.
- **A stray "i"/"r" gets typed / F5 refreshes the page** → those keys are
  suppressed while MTalk is running; make sure MTalk is the process in focus of
  the tap (restart it if you changed terminals).
- **Russian comes out as phonetic English** ("Prevet, devayat…") → you're on an
  English-only model. Leave `MTALK_MODEL_RU` unset, or point `MTALK_MODEL` at a
  multilingual model (no `.en` suffix).
- **First F5 + R hangs for a while** → it's the one-time ~500 MB download of the
  multilingual model. Pre-fetch it with
  `python -c "from faster_whisper import WhisperModel; WhisperModel('small')"`.
- **Italian mode does nothing** → run the `claude -p` check in step 1;
  `claude` must be installed and logged in.
- **Paste doesn't land** → some apps block synthetic Cmd+V; run with
  `MTALK_PASTE=0` and paste manually.
- **PortAudioError** → `brew install portaudio`, then
  `pip install --force-reinstall sounddevice`.
- **First run is slow** → it's downloading the Whisper model once; later runs are fast.
