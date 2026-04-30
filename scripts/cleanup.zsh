#!/usr/bin/env zsh
set -euo pipefail

NETWORK_NAME=${NETWORK_NAME:-forgejo-net}
RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
DIND_NAME=${DIND_NAME:-docker-dind}
DIND_VOLUME=${DIND_VOLUME:-forgejo-dind-data}

container delete -f "${RUNNER_NAME}" >/dev/null 2>&1 || true
container delete -f "${DIND_NAME}" >/dev/null 2>&1 || true
container network delete "${NETWORK_NAME}" >/dev/null 2>&1 || true

if [[ "${DELETE_DIND_VOLUME:-0}" == "1" ]]; then
  container volume delete "${DIND_VOLUME}" >/dev/null 2>&1 || true
fi

echo "Cleanup done."
