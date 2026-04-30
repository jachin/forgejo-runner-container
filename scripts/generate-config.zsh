#!/usr/bin/env zsh
set -euo pipefail

RUNNER_DATA_DIR=${RUNNER_DATA_DIR:-./runner-data}
RUNNER_CONFIG=${RUNNER_CONFIG:-${RUNNER_DATA_DIR}/runner-config.yml}
BASE_RUNNER_IMAGE=${BASE_RUNNER_IMAGE:-data.forgejo.org/forgejo/runner:12}

mkdir -p "${RUNNER_DATA_DIR}"

if [[ -f "${RUNNER_CONFIG}" ]]; then
  echo "Config already exists: ${RUNNER_CONFIG}"
  exit 0
fi

container run --rm "${BASE_RUNNER_IMAGE}" forgejo-runner generate-config > "${RUNNER_CONFIG}"
echo "Generated ${RUNNER_CONFIG}"
