#!/bin/zsh
set -euo pipefail

LABEL="com.nepovtor.peleng3d.server"
DOMAIN="gui/$(id -u)"

if launchctl print "$DOMAIN/$LABEL" 2>/dev/null; then
  print "\nDashboard: http://127.0.0.1:8765"
  exit 0
fi

print -u2 "PELENG background server is not installed or not loaded."
exit 1
