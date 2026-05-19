#!/usr/bin/env bash
# Canonical entry: venv, deps, optional host checks, launch app.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN=""
RECREATE_VENV=0
SKIP_HOST_CHECK=0
DEV_INSTALL=0
CONFIG_OVERRIDE=""
NO_OPEN_BROWSER=0

usage() {
  echo "Usage: $0 [--python /path/to/python3] [--recreate-venv] [--skip-host-check] [--dev] [--config /path/to.yaml] [--no-open-browser]" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 ]] || usage
      PYTHON_BIN="$2"
      shift 2
      ;;
    --recreate-venv)
      RECREATE_VENV=1
      shift
      ;;
    --skip-host-check)
      SKIP_HOST_CHECK=1
      shift
      ;;
    --dev)
      DEV_INSTALL=1
      shift
      ;;
    --config)
      [[ $# -ge 2 ]] || usage
      CONFIG_OVERRIDE="$2"
      shift 2
      ;;
    --no-open-browser)
      NO_OPEN_BROWSER=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$PYTHON_BIN" ]]; then
  if ! PYTHON_BIN="$(command -v python3)"; then
    echo "python3 not found on PATH. Pass --python /full/path/to/python3" >&2
    exit 1
  fi
fi

VENV_DIR="$REPO_ROOT/.venv"
if [[ "$RECREATE_VENV" -eq 1 && -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
"$PIP" install --upgrade pip wheel >/dev/null
"$PIP" install -r "$REPO_ROOT/requirements.txt"
"$PIP" install -e "$REPO_ROOT"
if [[ "$DEV_INSTALL" -eq 1 && -f "$REPO_ROOT/requirements-dev.txt" ]]; then
  "$PIP" install -r "$REPO_ROOT/requirements-dev.txt"
fi

if [[ "$SKIP_HOST_CHECK" -eq 0 ]]; then
  "$REPO_ROOT/scripts/check_host_prerequisites.sh" --repo-root "$REPO_ROOT" || true
fi

APP_PYTHON="$VENV_DIR/bin/python"
RUN_SESSION_ID="$(date +%Y%m%d_%H%M%S)_$$"
export IPHONE_CLEANUP_RUN_ID="$RUN_SESSION_ID"
mkdir -p "$REPO_ROOT/data"
RUN_SESSION_FILE="$REPO_ROOT/data/.run_session.json"
printf '%s\n' "{\"run_id\":\"$RUN_SESSION_ID\",\"shell_pid\":$$,\"started_at\":$(date +%s)}" >"$RUN_SESSION_FILE"

ARGS=(
  -m iphone_cleanup
  --repo-root "$REPO_ROOT"
  --defaults-config "$REPO_ROOT/config/app.defaults.yaml"
)

if [[ -n "$CONFIG_OVERRIDE" ]]; then
  ARGS+=(--local-config "$CONFIG_OVERRIDE")
elif [[ -f "$REPO_ROOT/config/app.local.yaml" ]]; then
  ARGS+=(--local-config "$REPO_ROOT/config/app.local.yaml")
fi

if [[ "$NO_OPEN_BROWSER" -eq 1 ]]; then
  ARGS+=(--no-open-browser)
fi

# Run the app as a child process so we can intercept signals and let the
# FastAPI lifespan shutdown (unmount ifuse, close handlers) run to completion
# before this script returns. `exec` would replace the shell and skip cleanup.
APP_PID=0
APP_EXIT=0

forward_signal() {
  local sig="$1"
  if [[ "$APP_PID" -ne 0 ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill -"$sig" "$APP_PID" 2>/dev/null || true
  fi
}

on_exit() {
  trap - EXIT INT TERM HUP
  rm -f "$REPO_ROOT/data/.run_session.json" "$REPO_ROOT/data/runtime_session.json" 2>/dev/null || true
  if [[ "$APP_PID" -ne 0 ]] && kill -0 "$APP_PID" 2>/dev/null; then
    # Child still alive at shell exit (e.g. uncaught error in the script);
    # ask it to terminate, then escalate if it does not stop in time.
    kill -TERM "$APP_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$APP_PID" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$APP_PID" 2>/dev/null; then
      kill -KILL "$APP_PID" 2>/dev/null || true
    fi
    wait "$APP_PID" 2>/dev/null || true
  fi
}

trap 'forward_signal INT'  INT
trap 'forward_signal TERM' TERM
trap 'forward_signal HUP'  HUP
trap on_exit EXIT

"$APP_PYTHON" "${ARGS[@]}" &
APP_PID=$!

# Re-wait until the child has actually exited; `wait` returns early when a
# trap fires, so we loop until the PID is gone and capture its real status.
while kill -0 "$APP_PID" 2>/dev/null; do
  set +e
  wait "$APP_PID"
  APP_EXIT=$?
  set -e
done
APP_PID=0
exit "$APP_EXIT"
