#!/bin/bash
# usage: silentgen.sh <session_id> [threshold_sec=480]
# Prints one line when ollama is decoding but the hermes session has shown no event for threshold_sec.
# With ollama, tool-call arguments (e.g. a long write_file body) do not stream, and hermes kills the
# request after 900s with no chunks ("Stream stale") — so this is the moment to tell it to write in pieces.
SID="$1"; TH="${2:-480}"
EV="${CC_WATCH_DIR:-$HOME/.hermes/cc_watch}/$SID/events.jsonl"
PID=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['pid'])" "$(dirname "$EV")/meta.json")
fired=0
while kill -0 "$PID" 2>/dev/null; do
  age=$(( $(date +%s) - $(stat -c %Y "$EV") ))
  if journalctl -u ollama --since "-30s" --no-pager 2>/dev/null | grep -q n_decoded; then
    if [ "$age" -ge "$TH" ] && [ $fired = 0 ]; then
      echo "SILENTGEN $(date +%T): ollama decoding but no hermes event for ${age}s (stale kill at 900s)"; fired=1
    fi
  fi
  [ "$age" -lt 60 ] && fired=0
  sleep 30
done
echo "hermes pid $PID exited $(date +%T)"
