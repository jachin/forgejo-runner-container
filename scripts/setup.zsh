#!/usr/bin/env zsh
set -euo pipefail

NETWORK_NAME=${NETWORK_NAME:-forgejo-net}
DIND_VOLUME=${DIND_VOLUME:-forgejo-dind-data}
RUNNER_DATA_DIR=${RUNNER_DATA_DIR:-./runner-data}

echo "==> Starting container system"
container system start

echo "==> Ensuring network: ${NETWORK_NAME}"
if ! container network list --format json | grep -q "\"${NETWORK_NAME}\""; then
  container network create "${NETWORK_NAME}"
fi

echo "==> Ensuring volume: ${DIND_VOLUME}"
if ! container volume list --format json | grep -q "\"${DIND_VOLUME}\""; then
  container volume create "${DIND_VOLUME}"
fi

echo "==> Ensuring runner data dir: ${RUNNER_DATA_DIR}"
mkdir -p "${RUNNER_DATA_DIR}"

echo "Done."
