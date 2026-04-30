#!/usr/bin/env zsh
set -euo pipefail

RUNNER_IMAGE=${RUNNER_IMAGE:-local/forgejo-runner-docker:12}

echo "==> Building ${RUNNER_IMAGE}"
container build -t "${RUNNER_IMAGE}" .
echo "Done."
