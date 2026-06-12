#!/usr/bin/env bash
# Run the Yokogawa LAN service on the host connected to the GS211 USB devices.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/yokogawa_service.sh [options]

Runs the Yokogawa LAN service in the foreground. The service must run on the
host that sees the Yokogawa GS211s through NI-VISA/PyVISA.

Defaults:
  config: site/yokogawa.yaml
  bind:   0.0.0.0:8020

Environment overrides:
  YOKOGAWA_SERVICE_CONFIG  Config YAML path (default: site/yokogawa.yaml)
  YOKOGAWA_SERVICE_HOST    Bind host (default: 0.0.0.0)
  YOKOGAWA_SERVICE_PORT    Bind port (default: 8020)
  YOKOGAWA_SERVICE_RELOAD  Set to 1/true/yes/on to enable uvicorn reload
  YOKOGAWA_SERVICE_ACCESS_LOG
                           Set to 0/false/no/off to disable access logs

Options:
  --config PATH       Config YAML path
  --host HOST         Bind host
  --port PORT         Bind port
  --reload            Enable uvicorn reload
  --no-access-log     Disable uvicorn access logs
  -h, --help          Show this help

Examples:
  # Run on the lab host:
  ./scripts/yokogawa_service.sh

  # Then from another terminal or machine:
  curl http://<lab-host>:8020/health
USAGE
}

bool_enabled() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

bool_disabled() {
  case "${1:-}" in
    0|false|FALSE|no|NO|off|OFF)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONFIG_PATH="${YOKOGAWA_SERVICE_CONFIG:-site/yokogawa.yaml}"
HOST="${YOKOGAWA_SERVICE_HOST:-0.0.0.0}"
PORT="${YOKOGAWA_SERVICE_PORT:-8020}"
RELOAD="${YOKOGAWA_SERVICE_RELOAD:-0}"
ACCESS_LOG="${YOKOGAWA_SERVICE_ACCESS_LOG:-1}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift
      ;;
    --host)
      HOST="$2"
      shift
      ;;
    --port)
      PORT="$2"
      shift
      ;;
    --reload)
      RELOAD=1
      ;;
    --no-access-log)
      ACCESS_LOG=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

cd "${REPO_ROOT}"

if [ ! -f "${CONFIG_PATH}" ]; then
  echo "Yokogawa config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

args=(
  yoko-lan serve
  --config "${CONFIG_PATH}"
  --host "${HOST}"
  --port "${PORT}"
)

if bool_enabled "${RELOAD}"; then
  args+=(--reload)
fi

if bool_disabled "${ACCESS_LOG}"; then
  args+=(--no-access-log)
fi

echo "Starting Yokogawa LAN service"
echo "  Config: ${CONFIG_PATH}"
echo "  Bind: ${HOST}:${PORT}"
echo "Press Ctrl-C to stop."
echo

exec uv run "${args[@]}"
