#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -m pip install -r requirements.txt
( sleep 3; open "http://127.0.0.1:8765" ) &
python -m peleng3d serve
