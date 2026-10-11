You are installing ONE security tool repository on this Linux arm64 (aarch64) host and writing its Korean manual.
A supervisor program watches every command you run. Work in small concrete steps. Do not over-think: after reading
a few files, ACT. If something fails twice the same way, change approach or declare KIND=skip.

SCOPE / SAFETY (MANDATORY): This kit is for authorized lab use. Some repos must NOT be installed or run, only
documented. Set KIND=skip (with SKIP_REASON) and write the short skip manual — do NOT install or functionally
verify — for any repo whose PRIMARY purpose is: DoS/DDoS flooding or "booter/stresser" (HTTP flood, slowloris,
UDP/TCP flood), botnet/C2 for real victims, ransomware/wiper/destructive malware, a credential/token stealer or
RAT whose only use is attacking third parties, or mass-exploitation of arbitrary internet hosts. Dual-use
scanners/exploit-PoCs/labs are fine (install normally). When unsure, skip and say why.

TASK REPO: __NAME__
  local clone : /home/thor-01/offensive/gh/__NAME__   (already cloned, read it there)
  upstream    : __URL__
  pre-triage  : hint=__HINT__ langs=__LANGS__ markers=__MARKERS__   (hint may be wrong — check yourself)

HOST FACTS: Ubuntu 24.04 aarch64, NO sudo (password required), python3.12 + venv, go1.22 (GOTOOLCHAIN=auto, GOBIN=$BIN),
gcc/make/cmake, java21, docker (works without sudo). node/npm/cargo NOT installed (for Rust, install rustup into $HOME/.cargo — no sudo).
PATHS — use the env vars from supervisor/kit_env.sh, do NOT invent directories:
  kit root  = /home/thor-01/offensive            (this IS the root; there is NO "kit/" subdirectory under it — never create offensive/kit/)
  $BIN      = /home/thor-01/offensive/bin         (binaries/wrappers go HERE; it is already on PATH)
  $VENVS    = /home/thor-01/offensive/.venvs      (python venv per tool: $VENVS/<name>)
  $GHD      = /home/thor-01/offensive/gh          (the clones)
Always write the venv to $VENVS/<name> and the wrapper to $BIN/<name>, by those variable names.
Optional local test target: WebBank victim app http://127.0.0.1:4561 (may be down). NEVER touch external hosts.

STEPS
1. Read README / build files of the repo (briefly). Decide KIND:
   tool = runnable program (CLI/script/service) ; data = wordlists/payloads/cheatsheets/docs/templates (nothing to run) ;
   lab  = vulnerable app / training environment (docker) ; skip = cannot work here (windows-only, needs GUI/hardware,
   android-only, needs paid API keys to even start, abandoned/broken beyond repair, or malware builder with no lab value).
2. Install it for real (no sudo). Then RUN it (e.g. --help) and confirm it works.
3. Write the recipe: copy /home/thor-01/offensive/supervisor/recipe_template.sh to
   /home/thor-01/offensive/install/recipes/__NAME__.sh and fill it in. do_install must reproduce your working install
   from a fresh clone; do_verify must run the tool and return non-zero if broken. For data: do_verify checks the key files exist.
   do_verify MUST be fast (<240s) and NON-BLOCKING: never start a long-running server/daemon/web-UI (streamlit, flask,
   uvicorn, node, a GUI) and wait on it — that hangs until the gate times out. For a server/UI tool, verify it is
   installed and importable instead: check module import plus `<tool> --version` or `--help`, not by booting the service.
   For a needed apt package you cannot install: put it in NEEDS_SUDO_APT (supervisor handles it).
4. Write the manual /home/thor-01/offensive/docs/manuals/__NAME__.md IN KOREAN. Required headings:
   tool/lab: "## 개요", "## 설치", "## 검증", "## 기본 사용법", "## 실전 예제", "## 주의사항"  (>= 40 lines, real flags taken
   from the tool's actual --help output, >= 5 copy-paste ```bash examples, targets only 127.0.0.1 / example.com / lab hosts)
   data/skip: "## 개요", "## 구성", "## 활용"  (>= 15 lines; for skip explain why in 개요)
5. Self-test, ALWAYS in the FOREGROUND (never background it, never use `&`):
     timeout 580 bash /home/thor-01/offensive/supervisor/gate.sh __NAME__ 2>&1 | tail -40
   It reinstalls from your recipe in a clean shell and checks everything. If it prints FAIL, fix the listed reasons and run it again.
   When it prints PASS (or NEEDS_SUDO), reply with one line "DONE __NAME__ <KIND>" and stop.
   HEAVY BUILDS: if your do_install compiles from scratch and takes longer than ~8 minutes (e.g. a Rust `cargo build
   --release`, or a large C/C++ project), the 580s foreground limit will KILL the gate mid-build so it can never finish
   for you. In that case do NOT keep re-running the full gate. Instead prove the recipe yourself with two quick checks —
   (a) run do_install's build once (you already did), and (b) run just the do_verify body by hand and confirm it exits 0 —
   then reply "DONE __NAME__ <KIND>". The supervisor re-runs the FULL gate itself with a much longer budget after you
   finish; that authoritative run does the clean-shell rebuild, so you do not need to complete it within your 580s limit.

RULES: no sudo, no apt, no `pip --break-system-packages`, no edits to ~/.bashrc, no git push, no rm outside kit/gh/__NAME__,
kit/bin, kit/.venvs/__NAME__. Keep EVERY command short enough to stay in the foreground: the shell `timeout` value AND
the terminal tool's own `timeout` parameter must BOTH be <= 580 (anything above 600 is auto-promoted to a BACKGROUND
process and you lose its output and process id). Never use `&` or background a command. A command that needs more than
~580s is a sign to simplify, not to background it. Do not work on any other repo.
Messages starting with "SUPERVISOR:" come from your supervisor — follow them exactly.
