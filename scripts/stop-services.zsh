#!/usr/bin/env zsh
set -euo pipefail

RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
DIND_NAME=${DIND_NAME:-docker-dind}

container stop "${RUNNER_NAME}" || true
container stop "${DIND_NAME}" || true

echo "Stopped."
