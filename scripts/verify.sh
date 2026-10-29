#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=$(command -v python3)
else
  printf 'Python 3 was not found. Create .venv or install Python 3.\n' >&2
  exit 1
fi
"$PYTHON" -m pytest -q
"$PYTHON" -m compileall -q peleng3d
printf '\nPython checks completed.\n'
if command -v xcodebuild >/dev/null 2>&1; then
  printf 'Xcode detected; validate the project on your Mac with:\n'
  printf 'xcodebuild -project ios/PELENGCamera/PELENGCamera.xcodeproj -scheme PELENGCamera -sdk iphoneos -configuration Debug CODE_SIGNING_ALLOWED=NO build\n'
else
  printf 'xcodebuild unavailable; iOS compilation requires Xcode on macOS.\n'
fi
