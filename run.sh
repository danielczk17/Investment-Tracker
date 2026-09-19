#!/usr/bin/env bash
# macOS / Linux launcher: sets up a local virtualenv on first run, then starts
# the app in a native window. Use `./run.sh --browser` for the dev server instead.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

# Port 5000 is used by AirPlay Receiver on recent macOS, so default to 5050.
export PORT="${PORT:-5050}"

if [ "$1" = "--browser" ]; then
  (sleep 2 && open "http://127.0.0.1:$PORT" 2>/dev/null || true) &
  exec .venv/bin/python app.py
else
  exec .venv/bin/python app.py --window
fi
