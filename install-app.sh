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

# Build into a scratch bundle first. macOS ties the permissions you grant to
# the app's exact signature, so the installed app is only replaced when its
# contents actually change; updating the Python code alone never resets them.
BUILD="$(mktemp -d)/MTalk.app"
mkdir -p "$BUILD/Contents/MacOS"

cat > "$BUILD/Contents/Info.plist" <<EOF
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

mkdir -p "$BUILD/Contents/Resources"

# Native launcher: Python runs inside MTalk's own process (see launcher.c).
clang -O2 -o "$BUILD/Contents/MacOS/MTalk" "$SRC/launcher.c" \
    -I "$INCLUDE" "$LIBPY" -Wl,-rpath,"$FRAMEWORKS" \
    -DMTALK_DIR="\"$REPO\"" -DMTALK_SITE="\"$SITE\"" \
    -DMTALK_CLAUDE="\"$CLAUDE\"" -DMTALK_PATH="\"$LAUNCH_PATH\""

# App icon: the same "M" logo as the menu bar.
ICONSET="$(mktemp -d)/MTalk.iconset"
(cd "$REPO" && "$PY" -c "import sys, menubar; menubar.write_iconset(sys.argv[1])" "$ICONSET")
iconutil -c icns "$ICONSET" -o "$BUILD/Contents/Resources/MTalk.icns"

STAMP="$(cd "$BUILD/Contents" && cat Info.plist MacOS/MTalk Resources/MTalk.icns | shasum | cut -d' ' -f1)"
if [ -f "$APP/Contents/Resources/build.stamp" ] && [ "$(cat "$APP/Contents/Resources/build.stamp")" = "$STAMP" ]; then
    echo "Updated MTalk's code; $APP unchanged (permissions kept). Quit and reopen MTalk."
    exit 0
fi
echo "$STAMP" > "$BUILD/Contents/Resources/build.stamp"
codesign --force --sign - "$BUILD" >/dev/null 2>&1 || true
rm -rf "$APP"
mkdir -p "$(dirname "$APP")"
mv "$BUILD" "$APP"
echo "Installed $APP"
echo "If MTalk was allowed in Accessibility / Input Monitoring before, remove it there and add it again."
