#!/usr/bin/env bash
# Acceptance gate wrapper (serialised: only one gate run at a time).
exec flock /home/thor-01/offensive/supervisor/.gate.lock python3 /home/thor-01/offensive/supervisor/gate.py "$@"
