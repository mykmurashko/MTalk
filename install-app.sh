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

# Apps launched from Finder don't get your shell's PATH, so resolve claude now.
CLAUDE="${MTALK_CLAUDE:-$(command -v claude || true)}"
LAUNCH_PATH="$(dirname "${CLAUDE:-/usr/bin/true}"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PYHOME="$("$PY" -c 'import sys; print(sys.base_prefix)')"  # …/Python3.framework/Versions/3.x
SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
LIBPY="$PYHOME/Python3"; [ -f "$LIBPY" ] || LIBPY="$PYHOME/Python"
INCLUDE="$PYHOME/Headers"
FRAMEWORKS="$(cd "$PYHOME/../../.." && pwd)"  # dir holding Python3.framework (its @rpath)

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
    <key>CFBundleIconFile</key><string>MTalk</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>MTalk records your voice while you hold the dictation key.</string>
</dict>
</plist>
EOF

mkdir -p "$APP/Contents/Resources"

# Native launcher: Python runs inside MTalk's own process (see launcher.c).
clang -O2 -o "$APP/Contents/MacOS/MTalk" "$SRC/launcher.c" \
    -I "$INCLUDE" "$LIBPY" -Wl,-rpath,"$FRAMEWORKS" \
    -DMTALK_DIR="\"$REPO\"" -DMTALK_SITE="\"$SITE\"" \
    -DMTALK_CLAUDE="\"$CLAUDE\"" -DMTALK_PATH="\"$LAUNCH_PATH\""

# App icon: the same "M" logo as the menu bar.
ICONSET="$(mktemp -d)/MTalk.iconset"
(cd "$REPO" && "$PY" -c "import sys, menubar; menubar.write_iconset(sys.argv[1])" "$ICONSET")
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/MTalk.icns"

# A stable (ad-hoc) signature so macOS remembers the permissions you grant.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "Installed $APP"
