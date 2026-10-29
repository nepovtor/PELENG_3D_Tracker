#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  print -u2 "PELENG: Python environment is missing: $PYTHON"
  print -u2 "Run scripts/start_mac.command once or create .venv from requirements.txt."
  exit 78
fi

CAMERA_INDEXES=${PELENG_CAMERA_INDEXES:-${PELENG_CAMERA_INDEX:-0}}
SERVER_PORT=${PELENG_SERVER_PORT:-8765}
DATA_DIR=${PELENG_DATA_DIR:-"$HOME/Documents/PELENG3D"}

cd "$PROJECT_DIR"
camera_args=()
for camera_index in "${(@s:,:)CAMERA_INDEXES}"; do
  if [[ ! "$camera_index" =~ '^[0-9]+$' ]]; then
    print -u2 "PELENG: invalid camera index: $camera_index"
    exit 64
  fi
  camera_args+=(--camera "$camera_index")
done
exec "$PYTHON" -m peleng3d serve "${camera_args[@]}" --port "$SERVER_PORT" --data "$DATA_DIR"
