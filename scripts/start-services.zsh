#!/usr/bin/env zsh
set -euo pipefail

NETWORK_NAME=${NETWORK_NAME:-forgejo-net}
RUNNER_IMAGE=${RUNNER_IMAGE:-local/forgejo-runner-docker:12}
RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
DIND_NAME=${DIND_NAME:-docker-dind}
DIND_VOLUME=${DIND_VOLUME:-forgejo-dind-data}
RUNNER_DATA_DIR=${RUNNER_DATA_DIR:-./runner-data}
DIND_PORT=${DIND_PORT:-2375}

echo "==> Starting docker:dind"
container delete -f "${DIND_NAME}" >/dev/null 2>&1 || true
container run -d \
  --name "${DIND_NAME}" \
  --network "${NETWORK_NAME}" \
  --cap-add ALL \
  -v "${DIND_VOLUME}:/var/lib/docker" \
  -p "127.0.0.1:${DIND_PORT}:2375" \
  docker:dind \
  dockerd -H tcp://0.0.0.0:2375 --tls=false

echo "==> Starting forgejo runner"
container delete -f "${RUNNER_NAME}" >/dev/null 2>&1 || true
container run -d \
  --name "${RUNNER_NAME}" \
  --network "${NETWORK_NAME}" \
  -e DOCKER_HOST="tcp://${DIND_NAME}.test:2375" \
  -v "${PWD}/${RUNNER_DATA_DIR}:/data" \
  "${RUNNER_IMAGE}" \
  forgejo-runner daemon --config /data/runner-config.yml

echo "Done."
