#!/bin/bash
# Build ~/Applications/MTalk.app: MTalk as a menu bar app, no Terminal window.
#
# macOS doesn't let apps opened from Finder read ~/Documents, ~/Desktop or
# ~/Downloads without extra permission, so the app doesn't run from this
# checkout. Instead the code is copied to ~/Library/Application Support/MTalk
# with its own virtualenv. Re-run this script after changing the code.
# Output goes to ~/Library/Logs/MTalk.log.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/MTalk.app"
REPO="$HOME/Library/Application Support/MTalk/app"
VENV="$REPO/.venv"
PY="$VENV/bin/python"

mkdir -p "$REPO"
cp "$SRC/mtalk.py" "$SRC/menubar.py" "$SRC/italian_prompt.txt" "$SRC/requirements.txt" "$REPO/"

if [ ! -x "$PY" ]; then
    echo "Creating virtualenv (one time)…"
    /usr/bin/python3 -m venv "$VENV"
fi
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$REPO/requirements.txt"

# A script-based app can be started under Rosetta; force native arm64 on
# Apple Silicon so the installed (arm64) wheels load.
ARCH=""
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
    ARCH="arch -arm64"
fi

# Apps launched from Finder don't get your shell's PATH, so resolve claude now.
CLAUDE="${MTALK_CLAUDE:-$(command -v claude || true)}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>MTalk</string>
    <key>CFBundleDisplayName</key><string>MTalk</string>
    <key>CFBundleIdentifier</key><string>com.mtalk.app</string>
    <key>CFBundleExecutable</key><string>MTalk</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>MTalk records your voice while you hold the dictation key.</string>
</dict>
</plist>
EOF

cat > "$APP/Contents/MacOS/MTalk" <<EOF
#!/bin/bash
cd "$REPO"
export MTALK_CLAUDE="$CLAUDE"
export PATH="$(dirname "${CLAUDE:-/usr/bin/true}"):/opt/homebrew/bin:/usr/local/bin:\$PATH"
exec $ARCH "$PY" -u mtalk.py >> "\$HOME/Library/Logs/MTalk.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/MTalk"

# A stable (ad-hoc) signature so macOS remembers the permissions you grant.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "Installed $APP"
