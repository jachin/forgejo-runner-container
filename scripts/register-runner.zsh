#!/usr/bin/env zsh
set -euo pipefail

RUNNER_DATA_DIR=${RUNNER_DATA_DIR:-./runner-data}
RUNNER_CONFIG=${RUNNER_CONFIG:-${RUNNER_DATA_DIR}/runner-config.yml}
BASE_RUNNER_IMAGE=${BASE_RUNNER_IMAGE:-data.forgejo.org/forgejo/runner:12}
RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
RUNNER_LABELS=${RUNNER_LABELS:-docker,linux,arm64}

mkdir -p "${RUNNER_DATA_DIR}"

if [[ ! -f "${RUNNER_CONFIG}" ]]; then
  echo "Missing config: ${RUNNER_CONFIG}"
  echo "Run ./scripts/generate-config.zsh first"
  exit 1
fi

if [[ -n "${FORGEJO_URL:-}" && -n "${RUNNER_TOKEN:-}" ]]; then
  echo "==> Attempting non-interactive registration"
  set +e
  container run --rm -i \
    -v "${PWD}/${RUNNER_DATA_DIR}:/data" \
    "${BASE_RUNNER_IMAGE}" \
    forgejo-runner register \
    --no-interactive \
    --instance "${FORGEJO_URL}" \
    --token "${RUNNER_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${RUNNER_LABELS}" \
    --config /data/runner-config.yml
  rc=$?
  set -e

  if [[ ${rc} -eq 0 ]]; then
    echo "Registration complete"
    exit 0
  fi

  echo "Non-interactive registration failed; falling back to interactive mode"
fi

container run --rm -it \
  -v "${PWD}/${RUNNER_DATA_DIR}:/data" \
  "${BASE_RUNNER_IMAGE}" \
  forgejo-runner register --config /data/runner-config.yml
