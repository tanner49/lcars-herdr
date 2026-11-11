#!/usr/bin/env bash
# Start the LCARS herdr console and open it as a chromeless Chrome app window.
#   ./run.sh            server + app window
#   ./run.sh --no-open  server only (open http://127.0.0.1:7477/ yourself)
# Run it from a herdr pane (or anywhere HERDR_SOCKET_PATH / ~/.config/herdr/herdr.sock is reachable).
set -euo pipefail
cd "$(dirname "$0")"
PORT=${LCARS_PORT:-7477}

[[ -x .venv/bin/python ]] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[[ -d node_modules/@xterm ]] || npm install --silent
[[ -f models/ggml-base.en.bin ]] || {
  echo "fetching whisper base.en model (141 MB) into models/"
  mkdir -p models
  curl -fL -o models/ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
}

open_window() {
  open -na "Google Chrome" --args --app="http://127.0.0.1:$PORT/" \
    --user-data-dir="$PWD/.chrome-app" --no-first-run --no-default-browser-check --window-size=1600,1000 --autoplay-policy=no-user-gesture-required
}

if curl -fs -o /dev/null "http://127.0.0.1:$PORT/"; then
  echo "server already running on 127.0.0.1:$PORT"
  [[ ${1:-} == --no-open ]] || open_window
  exit 0
fi

if [[ ${1:-} != --no-open ]]; then
  (
    for _ in $(seq 50); do curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.2; done
    # Separate Chrome profile in the project: its own window, its own site permissions (mic).
    open_window
  ) &
fi
exec .venv/bin/python server.py
