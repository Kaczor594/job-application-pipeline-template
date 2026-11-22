#!/usr/bin/env bash
# Wrapper the scheduler (launchd on macOS, systemd on Linux) invokes for the
# nightly run. Schedulers start with a bare environment, so we set PATH (incl.
# the TeX bin dirs so apply.py's xelatex resolves). The API key and all other
# settings come from the repo's .env, which the agent loads itself.
# Extra args pass through (e.g. --no-pipeline).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEX_DIRS="/Library/TeX/texbin"
for d in /usr/local/texlive/*/bin/*; do [ -d "$d" ] && TEX_DIRS="$TEX_DIRS:$d"; done
export PATH="$TEX_DIRS:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

echo "===== $(date '+%F %T') nightly_agent start ($*) ====="
"$REPO/.venv/bin/python" "$REPO/scripts/nightly_agent.py" "$@"
code=$?
echo "===== $(date '+%F %T') nightly_agent end (exit $code) ====="
exit $code
