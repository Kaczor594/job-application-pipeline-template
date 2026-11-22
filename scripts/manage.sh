#!/usr/bin/env bash
# Run the job agent on a schedule: a nightly run + an always-on review dashboard.
# macOS uses launchd (LaunchAgents); Linux uses systemd user units.
#
# Usage:
#   ./scripts/manage.sh install     # render + install the scheduler files (does not start)
#   ./scripts/manage.sh start       # start the dashboard + schedule the nightly run
#   ./scripts/manage.sh stop        # stop both
#   ./scripts/manage.sh restart
#   ./scripts/manage.sh status      # what's installed/running + dashboard port
#   ./scripts/manage.sh run-now     # run the nightly job immediately (in this terminal)
#   ./scripts/manage.sh logs        # follow the log files
#   ./scripts/manage.sh authorize   # (Gmail add-on) connect your Gmail account
#   ./scripts/manage.sh uninstall   # stop + remove the scheduler files
#   ./scripts/manage.sh render      # only write the rendered files to deploy/rendered/
#
# Settings (in .env): JOBAGENT_RUN_AT=HH:MM (default 05:30), JOBAGENT_WEB_PORT (default 8055)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
RENDERED="$REPO/deploy/rendered"
LABEL="com.jobagent"
OS="$(uname -s)"

# Read a KEY=VALUE from .env (environment wins).
env_get() {
  local key="$1" default="$2" val="${!1:-}"
  if [ -z "$val" ] && [ -f "$REPO/.env" ]; then
    val="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$REPO/.env" | tail -1 \
           | sed -E "s/^[^=]*=//; s/^['\"]//; s/['\"][[:space:]]*$//")"
  fi
  echo "${val:-$default}"
}

RUN_AT="$(env_get JOBAGENT_RUN_AT 05:30)"
PORT="$(env_get JOBAGENT_WEB_PORT 8055)"
if ! [[ "$RUN_AT" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "✗ JOBAGENT_RUN_AT must look like 05:30 (got '$RUN_AT')"; exit 1
fi
HOUR=$((10#${BASH_REMATCH[1]})); MINUTE=$((10#${BASH_REMATCH[2]}))
HOUR2=$(printf '%02d' "$HOUR"); MINUTE2=$(printf '%02d' "$MINUTE")

TEX_DIRS="/Library/TeX/texbin"
for d in /usr/local/texlive/*/bin/*; do [ -d "$d" ] && TEX_DIRS="$TEX_DIRS:$d"; done
SCHED_PATH="$TEX_DIRS:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

render() {
  mkdir -p "$RENDERED" "$LOG_DIR"
  local f out
  for f in "$REPO"/deploy/templates/*.tmpl; do
    out="$RENDERED/$(basename "${f%.tmpl}")"
    sed -e "s|{{REPO}}|$REPO|g" -e "s|{{LOG_DIR}}|$LOG_DIR|g" -e "s|{{LABEL}}|$LABEL|g" \
        -e "s|{{PATH}}|$SCHED_PATH|g" -e "s|{{HOUR}}|$HOUR|g" -e "s|{{MINUTE}}|$MINUTE|g" \
        -e "s|{{HOUR2}}|$HOUR2|g" -e "s|{{MINUTE2}}|$MINUTE2|g" -e "s|{{PORT}}|$PORT|g" \
        "$f" > "$out"
  done
  # launchd plists are named after their label
  mv -f "$RENDERED/jobagent.nightly.plist" "$RENDERED/$LABEL.nightly.plist"
  mv -f "$RENDERED/jobagent.web.plist" "$RENDERED/$LABEL.web.plist"
}

need_venv() {
  if [ ! -x "$REPO/.venv/bin/python" ]; then
    echo "✗ No virtualenv at .venv — run setup first (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-agent.txt)"
    exit 1
  fi
}

# ── macOS (launchd) ──────────────────────────────────────────────────────────
LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

mac() {
  case "$1" in
    install)
      render; mkdir -p "$LA"
      cp "$RENDERED/$LABEL.nightly.plist" "$RENDERED/$LABEL.web.plist" "$LA/"
      echo "→ installed to $LA (nightly at $HOUR2:$MINUTE2). Next: $0 start" ;;
    start)
      [ -f "$LA/$LABEL.web.plist" ] || mac install
      launchctl bootstrap "gui/$UID_NUM" "$LA/$LABEL.web.plist" 2>/dev/null \
        || launchctl kickstart -k "gui/$UID_NUM/$LABEL.web"
      launchctl bootstrap "gui/$UID_NUM" "$LA/$LABEL.nightly.plist" 2>/dev/null || true
      echo "→ started. Dashboard: http://127.0.0.1:$PORT  (nightly run at $HOUR2:$MINUTE2)" ;;
    stop)
      launchctl bootout "gui/$UID_NUM/$LABEL.web" 2>/dev/null || true
      launchctl bootout "gui/$UID_NUM/$LABEL.nightly" 2>/dev/null || true
      echo "→ stopped" ;;
    status)
      for j in web nightly; do
        if launchctl print "gui/$UID_NUM/$LABEL.$j" >/dev/null 2>&1; then
          echo "$j: loaded ($(launchctl print "gui/$UID_NUM/$LABEL.$j" | grep -E 'state =' | head -1 | xargs))"
        else
          echo "$j: not loaded"
        fi
      done ;;
    uninstall)
      mac stop; rm -f "$LA/$LABEL.nightly.plist" "$LA/$LABEL.web.plist"; echo "→ removed" ;;
  esac
}

# ── Linux (systemd --user) ───────────────────────────────────────────────────
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

linux() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "✗ systemd not found. Add this line to 'crontab -e' instead:"
    echo "  $MINUTE $HOUR * * * /bin/bash $REPO/scripts/run_nightly.sh >> $LOG_DIR/nightly.log 2>&1"
    echo "  and start the dashboard with: $REPO/.venv/bin/python $REPO/web/app.py"
    exit 1
  fi
  case "$1" in
    install)
      render; mkdir -p "$UNIT_DIR"
      cp "$RENDERED"/jobagent-nightly.service "$RENDERED"/jobagent-nightly.timer \
         "$RENDERED"/jobagent-web.service "$UNIT_DIR/"
      systemctl --user daemon-reload
      echo "→ installed to $UNIT_DIR (nightly at $HOUR2:$MINUTE2). Next: $0 start"
      echo "  Tip: 'loginctl enable-linger $USER' keeps it running while you're logged out." ;;
    start)
      [ -f "$UNIT_DIR/jobagent-web.service" ] || linux install
      systemctl --user enable --now jobagent-web.service jobagent-nightly.timer
      echo "→ started. Dashboard: http://127.0.0.1:$PORT  (nightly run at $HOUR2:$MINUTE2)" ;;
    stop)
      systemctl --user disable --now jobagent-web.service jobagent-nightly.timer 2>/dev/null || true
      echo "→ stopped" ;;
    status)
      systemctl --user --no-pager status jobagent-web.service 2>/dev/null | head -3 || echo "web: not installed"
      systemctl --user --no-pager list-timers jobagent-nightly.timer 2>/dev/null | head -2 || echo "nightly: not installed" ;;
    uninstall)
      linux stop
      rm -f "$UNIT_DIR"/jobagent-nightly.service "$UNIT_DIR"/jobagent-nightly.timer "$UNIT_DIR"/jobagent-web.service
      systemctl --user daemon-reload; echo "→ removed" ;;
  esac
}

sched() {
  case "$OS" in
    Darwin) mac "$1" ;;
    Linux)  linux "$1" ;;
    *) echo "✗ Unsupported OS '$OS'. On Windows, use WSL (Ubuntu) and run this there."; exit 1 ;;
  esac
}

case "${1:-}" in
  render)    render; echo "→ rendered to $RENDERED" ;;
  install)   need_venv; sched install ;;
  start)     need_venv; sched start ;;
  stop)      sched stop ;;
  restart)   sched stop; sleep 1; need_venv; sched start ;;
  status)
    sched status
    if (echo > "/dev/tcp/127.0.0.1/$PORT") >/dev/null 2>&1; then
      echo "dashboard: listening on http://127.0.0.1:$PORT"
    else
      echo "dashboard: nothing listening on port $PORT"
    fi ;;
  run-now)   need_venv; mkdir -p "$LOG_DIR"; /bin/bash "$REPO/scripts/run_nightly.sh" "${@:2}" ;;
  logs)
    mkdir -p "$LOG_DIR"
    touch "$LOG_DIR"/nightly.log "$LOG_DIR"/nightly.err.log "$LOG_DIR"/web.log "$LOG_DIR"/web.err.log
    tail -F "$LOG_DIR"/nightly.log "$LOG_DIR"/nightly.err.log "$LOG_DIR"/web.log "$LOG_DIR"/web.err.log ;;
  authorize) need_venv; "$REPO/.venv/bin/python" "$REPO/scripts/authorize.py" ;;
  uninstall) sched uninstall ;;
  *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
