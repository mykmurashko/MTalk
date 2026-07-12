#!/bin/bash
# Double-click launcher for MTalk. Keep this window open while dictating.
cd "$(dirname "$0")"
source .venv/bin/activate
MTALK_MODEL="${MTALK_MODEL:-small.en}" python mtalk.py
