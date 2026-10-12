# recipe: __NAME__   (copy this file to install/recipes/__NAME__.sh and fill it in)
# Available vars: $KIT (kit root) $BIN (kit/bin, on PATH) $VENVS (kit/.venvs) $GHD (kit/gh)
# NEVER hardcode /home/... paths — ALWAYS use $KIT $BIN $VENVS $GHD $SRC (the gate rejects any "/home/thor-01" in this file).
# NEVER use sudo. Everything must be idempotent.
NAME="__NAME__"
KIND="tool"            # tool | data | lab | skip
REPO="__URL__"
SRC="$GHD/__NAME__"
NEEDS_SUDO_APT=""      # apt packages that must exist (space separated); the master script installs them with sudo
SKIP_REASON=""         # required when KIND=skip (why it cannot/should not be installed on linux/arm64)

do_install() {
  # example (python):  python3 -m venv "$VENVS/$NAME" && "$VENVS/$NAME/bin/pip" install -q -r "$SRC/requirements.txt" || return 1
  #                    printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$VENVS/$NAME/bin/python" "$SRC/tool.py" > "$BIN/tool" && chmod +x "$BIN/tool"
  # example (go):      (cd "$SRC" && go build -o "$BIN/tool" ./cmd/tool) || return 1
  :
}

do_verify() {
  # Must actually RUN the tool (e.g. `tool --help`) and fail (non-zero) if it is broken. Local only, no external targets.
  # The gate REJECTS do_verify that masks failure: NO "|| true", NO "exit 0", NO "return 0", NO bare ":".
  # Every check ends in "|| return 1" (fail) — never "|| true". Be fast & non-blocking: never start a server and wait.
  # example:  "$BIN/tool" --help 2>&1 | grep -qi usage || return 1
  false
}
