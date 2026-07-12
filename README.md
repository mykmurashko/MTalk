# MTalk — Whisper push-to-talk dictation (macOS), with on-the-fly Italian

Hold **F5**, speak, release — your words are transcribed locally with Whisper and
pasted into the focused app. Hold **F5 + I** while you speak and the text is
translated to **Italian** before it's pasted (only the Italian lands on your
clipboard). Transcription is fully local; translation uses the `claude` CLI you're
already logged in to (no API key needed).

The console stays clean — one entry per result:

```
14:02:11  Let's ship the update on Friday.
14:03:40
  EN  can you confirm the meeting at 3 pm with the team
  IT  Può confermare la riunione alle 15:00 con il team?
```

---

## What's new vs. a plain Whisper dictation script

1. **The built-in F5 key works now.** On newer Macs the F5 key has a 🎤 (Dictation)
   icon and pressing it on the *built-in* keyboard fires macOS Dictation instead of
   sending F5 — so the helper never saw it (external keyboards were unaffected).
   MTalk ships a one-command fix (see step 3).
2. **Hold F5 + I to translate to Italian** as you dictate.
3. **A clean CLI** — just a timestamp and the text.

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
4. Hit Enter to send.

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

# Change the hotkey or the Italian modifier
MTALK_HOTKEY=f6 python mtalk.py
MTALK_ITALIAN=j python mtalk.py

# Clipboard only, no auto-paste
MTALK_PASTE=0 python mtalk.py
```

| Variable        | Default              | Meaning                                       |
| --------------- | -------------------- | --------------------------------------------- |
| `MTALK_MODEL`   | `small.en`           | Whisper model (`tiny.en`→`large-v3`)          |
| `MTALK_HOTKEY`  | `f5`                 | Push-to-talk key (`f1`–`f20` or a character)  |
| `MTALK_ITALIAN` | `i`                  | Key held with the hotkey to translate         |
| `MTALK_PASTE`   | `1`                  | `1` auto-paste, `0` clipboard only            |
| `MTALK_DEVICE`  | system default       | Input device index/name for sounddevice       |
| `MTALK_CLAUDE`  | `claude` on PATH     | Path to the `claude` CLI                       |
| `MTALK_PROMPT`  | `italian_prompt.txt` | Path to the translation prompt                 |

Models fastest → most accurate: `tiny.en`, `base.en`, `small.en`, `medium.en`,
`large-v3`. On Apple Silicon, `small.en` transcribes a sentence in well under a
second. English-only models (`.en`) are fine since you dictate in English.

## Troubleshooting

- **Built-in F5 still triggers Dictation** → run `./install-fnkey-fix.sh`, then
  check `hidutil property --get UserKeyMapping`. If empty after a reboot, confirm
  `~/Library/LaunchAgents/com.mtalk.keyremap.plist` exists.
- **Hotkey does nothing** → Accessibility + Input Monitoring not granted to your
  terminal app. Re-check step 2 and fully restart the terminal.
- **A stray "i" gets typed / F5 refreshes the page** → those keys are suppressed
  while MTalk is running; make sure MTalk is the process in focus of the tap
  (restart it if you changed terminals).
- **Italian mode does nothing** → run the `claude -p` check in step 1;
  `claude` must be installed and logged in.
- **Paste doesn't land** → some apps block synthetic Cmd+V; run with
  `MTALK_PASTE=0` and paste manually.
- **PortAudioError** → `brew install portaudio`, then
  `pip install --force-reinstall sounddevice`.
- **First run is slow** → it's downloading the Whisper model once; later runs are fast.
