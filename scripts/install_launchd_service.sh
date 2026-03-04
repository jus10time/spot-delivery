#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.justingeiger.spot-delivery"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ "${SPOT_DELIVERY_ALLOW_PERSISTENCE:-0}" != "1" ]; then
  echo "Refusing to install launchd persistence by default."
  echo "If you really want it, run:"
  echo "  SPOT_DELIVERY_ALLOW_PERSISTENCE=1 ./scripts/install_launchd_service.sh"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${APP_DIR}/.venv/bin/python</string>
    <string>${APP_DIR}/app.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${APP_DIR}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>RunAtLoad</key>
  <false/>

  <key>KeepAlive</key>
  <false/>

  <key>StandardOutPath</key>
  <string>/tmp/spot-delivery.launchd.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/spot-delivery.launchd.err</string>
</dict>
</plist>
PLIST

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" || true
fi

launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Installed and started ${LABEL}"
echo "Plist: $PLIST_PATH"
echo "Persistence was explicitly enabled via SPOT_DELIVERY_ALLOW_PERSISTENCE=1"
