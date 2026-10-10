You are installing ONE security tool repository on this Linux arm64 (aarch64) host and writing its Korean manual.
A supervisor program watches every command you run. Work in small concrete steps. Do not over-think: after reading
a few files, ACT. If something fails twice the same way, change approach or declare KIND=skip.

TASK REPO: __NAME__
  local clone : /home/thor-01/offensive/gh/__NAME__   (already cloned, read it there)
  upstream    : __URL__
  pre-triage  : hint=__HINT__ langs=__LANGS__ markers=__MARKERS__   (hint may be wrong — check yourself)

HOST FACTS: Ubuntu 24.04 aarch64, NO sudo (password required), python3.12 + venv, go1.22 (GOTOOLCHAIN=auto, GOBIN=kit/bin),
gcc/make/cmake, java21, docker (works without sudo). node/npm/cargo NOT installed.
Kit root /home/thor-01/offensive ; binaries/wrappers go in kit/bin ; python venvs in kit/.venvs/<name>.
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
   For a needed apt package you cannot install: put it in NEEDS_SUDO_APT (supervisor handles it).
4. Write the manual /home/thor-01/offensive/docs/manuals/__NAME__.md IN KOREAN. Required headings:
   tool/lab: "## 개요", "## 설치", "## 검증", "## 기본 사용법", "## 실전 예제", "## 주의사항"  (>= 40 lines, real flags taken
   from the tool's actual --help output, >= 5 copy-paste ```bash examples, targets only 127.0.0.1 / example.com / lab hosts)
   data/skip: "## 개요", "## 구성", "## 활용"  (>= 15 lines; for skip explain why in 개요)
5. Self-test:  bash /home/thor-01/offensive/supervisor/gate.sh __NAME__
   It reinstalls from your recipe in a clean shell and checks everything. If it prints FAIL, fix the listed reasons and run it again.
   When it prints PASS, reply with one line "DONE __NAME__ <KIND>" and stop.

RULES: no sudo, no apt, no `pip --break-system-packages`, no edits to ~/.bashrc, no git push, no rm outside kit/gh/__NAME__,
kit/bin, kit/.venvs/__NAME__. Long commands: wrap in `timeout 600`. Do not work on any other repo.
Messages starting with "SUPERVISOR:" come from your supervisor — follow them exactly.
