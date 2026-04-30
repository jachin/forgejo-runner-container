#!/usr/bin/env zsh
set -euo pipefail

RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
DIND_NAME=${DIND_NAME:-docker-dind}
NETWORK_NAME=${NETWORK_NAME:-forgejo-net}

echo "==> Containers"
container list --all

echo "\n==> Runner logs (tail 50)"
container logs -n 50 "${RUNNER_NAME}" || true

echo "\n==> DinD logs (tail 50)"
container logs -n 50 "${DIND_NAME}" || true

echo "\n==> Docker daemon reachability test"
container run --rm --network "${NETWORK_NAME}" docker:cli -H "tcp://${DIND_NAME}.test:2375" info >/dev/null

echo "OK"
