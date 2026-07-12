#!/bin/bash
# MTalk — make the built-in mic/F5 key behave like a real F5.
#
# Newer Macs ship the F5 key with a microphone (Dictation) icon. Pressing it on
# the built-in keyboard triggers macOS Dictation instead of sending F5, so the
# MTalk helper never sees it. This remaps that key's HID usage (Voice Command,
# 0xC000000CF) to F5 (0x70000003E) at the hardware layer — below macOS Dictation
# — so it becomes a clean F5 on every keyboard, and Dictation no longer fires.
#
# External keyboards already send a real F5 and are unaffected by this remap.
set -euo pipefail

LABEL="com.mtalk.keyremap"
SRC_PLIST="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"
DST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REMAP='{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0xC000000CF,"HIDKeyboardModifierMappingDst":0x70000003E}]}'

echo "→ Applying remap now (mic/F5 key → F5)…"
/usr/bin/hidutil property --set "$REMAP" >/dev/null

echo "→ Installing LaunchAgent so it persists across reboots…"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC_PLIST" "$DST_PLIST"
launchctl unload "$DST_PLIST" 2>/dev/null || true
launchctl load "$DST_PLIST"

echo "✓ Done. Your built-in mic/F5 key now acts as F5."
echo "  Verify with:  hidutil property --get UserKeyMapping"
echo "  Undo with:    ./uninstall-fnkey-fix.sh"
