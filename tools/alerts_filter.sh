#!/bin/bash
# Stream attach_state/alerts.log for a supervisor (e.g. Claude Code Monitor).
# Drops ATTACH lines, and drops STALL/THINK while ollama is idle (no decoding in the last 60s):
# those are false alarms from an idle session or hermes' background self-review.
LOG="${1:-$(dirname "$0")/../attach_state/alerts.log}"
tail -n0 -F "$LOG" | while IFS= read -r line; do
  case "$line" in
    *"[ATTACH]"*) continue ;;
    *"[STALL]"*|*"[THINK]"*)
      journalctl -u ollama --since "-60s" --no-pager 2>/dev/null | grep -q "n_decoded\|prompt processing" || continue ;;
  esac
  echo "$line"
done
