#!/bin/bash
# MTalk — undo the mic/F5 key remap and restore the default (Dictation) behavior.
set -euo pipefail

LABEL="com.mtalk.keyremap"
DST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "→ Clearing the key remap…"
/usr/bin/hidutil property --set '{"UserKeyMapping":[]}' >/dev/null

echo "→ Removing the LaunchAgent…"
launchctl unload "$DST_PLIST" 2>/dev/null || true
rm -f "$DST_PLIST"

echo "✓ Done. The mic/F5 key is back to its default (macOS Dictation) behavior."
echo "  (The remap is also cleared until next login; a reboot fully resets it.)"
