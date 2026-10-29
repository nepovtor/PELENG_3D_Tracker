#!/bin/zsh
set -euo pipefail

LABEL="com.nepovtor.peleng3d.server"
SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
RUNNER="$SCRIPT_DIR/run_server.sh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/PELENG3D"
DOMAIN="gui/$(id -u)"
CAMERA_INDEXES=${PELENG_CAMERA_INDEXES:-0}

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  print -u2 "PELENG: .venv is not ready. Run scripts/start_mac.command once."
  exit 1
fi

"$PROJECT_DIR/.venv/bin/python" -c 'import cv2, fastapi, uvicorn' >/dev/null
mkdir -p "${PLIST:h}" "$LOG_DIR" "$HOME/Documents/PELENG3D"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$RUNNER</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PELENG_CAMERA_INDEXES</key>
    <string>$CAMERA_INDEXES</string>
    <key>PELENG_SERVER_PORT</key>
    <string>8765</string>
    <key>PELENG_DATA_DIR</key>
    <string>$HOME/Documents/PELENG3D</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/server.error.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

print "PELENG background server installed."
print "Dashboard: http://127.0.0.1:8765"
print "Status:    scripts/autostart_status.sh"
print "Logs:      $LOG_DIR"
