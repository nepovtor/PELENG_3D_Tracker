#!/bin/zsh
set -euo pipefail

LABEL="com.nepovtor.peleng3d.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
if [[ -f "$PLIST" ]]; then
  mv "$PLIST" "$HOME/.Trash/$LABEL.plist"
fi
print "PELENG background server stopped and its LaunchAgent moved to Trash."
