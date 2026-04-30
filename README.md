# Forgejo Runner on macOS `container` with Docker tools inside runner

This project sets up a Forgejo Actions runner on macOS using Apple's lightweight `container` runtime.

The runner image includes Docker CLI tools (`docker`, `buildx`, `compose`) while Docker Engine runs in a sidecar (`docker:dind`).

## Architecture

- `forgejo-runner` container:
  - Runs `forgejo-runner daemon`
  - Has Docker CLI tools installed
  - Talks to Docker daemon via `DOCKER_HOST=tcp://docker-dind.test:2375`
- `docker-dind` container:
  - Runs `dockerd`
  - Provides build/push capabilities for workflows that use Docker

## Prerequisites

- macOS with Apple `container` installed and working
- Forgejo instance with Actions enabled
- A runner registration token
- Registry credentials as needed for image push

## Files in this repo

- `Containerfile` — custom runner image with Docker CLI tools
- `scripts/setup.zsh` — one-time bootstrap (`container system start`, network/volumes)
- `scripts/build-runner-image.zsh` — builds `local/forgejo-runner-docker:12`
- `scripts/generate-config.zsh` — generates default `runner-data/runner-config.yml`
- `scripts/register-runner.zsh` — registers runner with Forgejo
- `scripts/start-services.zsh` — starts `docker-dind` and `forgejo-runner`
- `scripts/status.zsh` — basic health/status checks
- `scripts/stop-services.zsh` — stop services
- `scripts/cleanup.zsh` — optional cleanup of containers/network/volumes

## Quick start

1. Bootstrap runtime:

```/dev/null/shell.zsh#L1-1
./scripts/setup.zsh
```

2. Build custom runner image:

```/dev/null/shell.zsh#L1-1
./scripts/build-runner-image.zsh
```

3. Generate runner config:

```/dev/null/shell.zsh#L1-1
./scripts/generate-config.zsh
```

4. Register runner:

```/dev/null/shell.zsh#L1-1
FORGEJO_URL="https://forgejo.example.com" RUNNER_TOKEN="..." ./scripts/register-runner.zsh
```

5. Start services:

```/dev/null/shell.zsh#L1-1
./scripts/start-services.zsh
```

6. Validate:

```/dev/null/shell.zsh#L1-1
./scripts/status.zsh
```

## Script contents

Create the following files exactly as shown.

### `scripts/setup.zsh`
```/dev/null/scripts/setup.zsh#L1-28
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
```

### `scripts/build-runner-image.zsh`
```/dev/null/scripts/build-runner-image.zsh#L1-9
#!/usr/bin/env zsh
set -euo pipefail

RUNNER_IMAGE=${RUNNER_IMAGE:-local/forgejo-runner-docker:12}

echo "==> Building ${RUNNER_IMAGE}"
container build -t "${RUNNER_IMAGE}" .
echo "Done."
```

### `scripts/generate-config.zsh`
```/dev/null/scripts/generate-config.zsh#L1-17
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
```

### `scripts/register-runner.zsh`
```/dev/null/scripts/register-runner.zsh#L1-44
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
```

### `scripts/start-services.zsh`
```/dev/null/scripts/start-services.zsh#L1-44
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
```

### `scripts/status.zsh`
```/dev/null/scripts/status.zsh#L1-20
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
```

### `scripts/stop-services.zsh`
```/dev/null/scripts/stop-services.zsh#L1-12
#!/usr/bin/env zsh
set -euo pipefail

RUNNER_NAME=${RUNNER_NAME:-forgejo-runner}
DIND_NAME=${DIND_NAME:-docker-dind}

container stop "${RUNNER_NAME}" || true
container stop "${DIND_NAME}" || true

echo "Stopped."
```

### `scripts/cleanup.zsh`
```/dev/null/scripts/cleanup.zsh#L1-29
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
```

### Make scripts executable

```/dev/null/scripts/chmod.zsh#L1-1
chmod +x scripts/*.zsh
```

## Environment variables

Most scripts support overrides:

- `NETWORK_NAME` (default: `forgejo-net`)
- `RUNNER_IMAGE` (default: `local/forgejo-runner-docker:12`)
- `RUNNER_NAME` (default: `forgejo-runner`)
- `DIND_NAME` (default: `docker-dind`)
- `RUNNER_DATA_DIR` (default: `./runner-data`)
- `DIND_PORT` (default: `2375`)
- `FORGEJO_URL` (required for registration)
- `RUNNER_TOKEN` (required for registration)
- `RUNNER_LABELS` (optional, comma-separated)

## Notes

- `docker:dind` is configured without TLS for local network simplicity.
- Keep this host trusted. For stronger hardening, isolate network and enable TLS.
- If your workflows use `docker/build-push-action`, ensure credentials are provided in workflow secrets.

## Troubleshooting

- Check runner logs:

```/dev/null/shell.zsh#L1-1
container logs forgejo-runner
```

- Check dind logs:

```/dev/null/shell.zsh#L1-1
container logs docker-dind
```

- Verify Docker daemon reachability from runner network:

```/dev/null/shell.zsh#L1-1
container run --rm --network forgejo-net docker:cli -H tcp://docker-dind.test:2375 info
```
