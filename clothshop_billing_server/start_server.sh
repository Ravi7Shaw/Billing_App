#!/usr/bin/env bash
# Double-click, or run `./start_server.sh` from a terminal.
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Setting up for the first time, this may take a minute..."
    python3 -m venv venv
    source venv/bin/activate
    pip install --quiet -r backend/requirements.txt
else
    source venv/bin/activate
fi

python3 run_server.py
