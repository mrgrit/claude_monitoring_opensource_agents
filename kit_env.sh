# Shared environment for recipes, gate and install_all.sh. Source, don't execute.
export KIT="${KIT:-/home/thor-01/offensive}"
export BIN="$KIT/bin" VENVS="$KIT/.venvs" GHD="$KIT/gh"
export GOBIN="$BIN" GOTOOLCHAIN=auto GOFLAGS="-buildvcs=false"
export PATH="$BIN:$HOME/go/bin:$HOME/.local/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$BIN" "$VENVS"
