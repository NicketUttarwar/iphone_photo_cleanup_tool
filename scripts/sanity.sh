#!/usr/bin/env bash
# Full-stack sanity: venv + pytest + short live HTTP smoke (isolated port/paths).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_LIVE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-live) SKIP_LIVE=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--skip-live]" >&2
      echo "  Runs pytest (dev deps) and optional live HTTP checks on an ephemeral port." >&2
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

VENV_DIR="$REPO_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"
"$PIP" install -q --upgrade pip wheel
"$PIP" install -q -r "$REPO_ROOT/requirements.txt"
"$PIP" install -q -e "$REPO_ROOT[dev]"

echo "[sanity] host prerequisites"
bash "$REPO_ROOT/scripts/check_host_prerequisites.sh" --repo-root "$REPO_ROOT" || true

echo "[sanity] pytest"
"$PY" -m pytest tests/ -q --tb=line

if [[ "$SKIP_LIVE" -eq 1 ]]; then
  echo "[sanity] live smoke skipped (--skip-live)"
  exit 0
fi

SMOKE_PORT=18766
SMOKE_CFG="$(mktemp "${TMPDIR:-/tmp}/iphone-cleanup-sanity.XXXXXX.yaml")"
trap 'rm -f "$SMOKE_CFG"; lsof -tiTCP:'"$SMOKE_PORT"' -sTCP:LISTEN 2>/dev/null | xargs kill -TERM 2>/dev/null || true' EXIT

cat > "$SMOKE_CFG" <<YAML
server:
  host: "127.0.0.1"
  port: $SMOKE_PORT
paths:
  data_dir: "data/_sanity"
  logs_dir: "data/_sanity/logs"
  thumbnail_cache_dir: "data/_sanity/thumbs"
  scan_artifacts_dir: "data/_sanity/scans"
  user_scans_dir: "data/_sanity/user_scans"
  mount_point: "data/_sanity/mount"
ui:
  open_browser: false
  sse_poll_interval_ms: 200
logging:
  level: "INFO"
YAML

CURL="$(command -v curl || true)"
if [[ -z "$CURL" && -x /usr/bin/curl ]]; then CURL=/usr/bin/curl; fi
if [[ -z "$CURL" ]]; then
  echo "[sanity] curl not found; skipping live smoke" >&2
  exit 0
fi

lsof -tiTCP:"$SMOKE_PORT" -sTCP:LISTEN 2>/dev/null | xargs kill -TERM 2>/dev/null || true
sleep 0.3

echo "[sanity] live HTTP smoke on port $SMOKE_PORT"
"$REPO_ROOT/scripts/run.sh" --skip-host-check --no-open-browser --config "$SMOKE_CFG" &
RPID=$!
BASE="http://127.0.0.1:$SMOKE_PORT"
for _ in $(seq 1 40); do
  if "$CURL" -sf "$BASE/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

check() {
  local name="$1" url="$2" expect="${3:-200}"
  local code
  code="$("$CURL" -s -o /dev/null -w "%{http_code}" "$url")"
  if [[ "$code" != "$expect" ]]; then
    echo "[sanity] FAIL $name: $url -> $code (expected $expect)" >&2
    exit 1
  fi
  echo "[sanity] OK   $name ($code)"
}

check health "$BASE/health"
check index "$BASE/"
check status "$BASE/api/status"
check device "$BASE/api/device"
check sessions "$BASE/api/scan/sessions"
check groups "$BASE/api/scan/groups"
check delete_preview_no_mount "$BASE/api/delete/preview" 400
check docs_preview_no_mount "$BASE/api/documents/preview" 400
code="$("$CURL" -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/scan/start?kind=exact")"
[[ "$code" == "400" ]] || { echo "[sanity] FAIL scan/start -> $code (expected 400)" >&2; exit 1; }
echo "[sanity] OK   scan/start without mount (400)"
code="$("$CURL" -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/activity-log/clear")"
[[ "$code" == "200" ]] || { echo "[sanity] FAIL activity-log/clear -> $code" >&2; exit 1; }
echo "[sanity] OK   activity-log/clear (200)"

if ! "$CURL" -sN --max-time 3 "$BASE/api/events" | head -n 3 | grep -q '^data:'; then
  echo "[sanity] WARN SSE /api/events (no data: line in 3s)" >&2
fi

LOG_FILE="$REPO_ROOT/data/_sanity/logs/app.log"
grep -q server_start "$LOG_FILE" || { echo "[sanity] FAIL missing server_start in $LOG_FILE" >&2; exit 1; }
echo "[sanity] OK   file log server_start"

kill -TERM "$RPID" 2>/dev/null || true
for _ in $(seq 1 20); do kill -0 "$RPID" 2>/dev/null || break; sleep 0.5; done
wait "$RPID" 2>/dev/null || true
RPID=0

grep -q server_stop "$LOG_FILE" || { echo "[sanity] FAIL missing server_stop in $LOG_FILE" >&2; exit 1; }
echo "[sanity] OK   file log server_stop"
echo "[sanity] all checks passed"
