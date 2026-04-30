FROM data.forgejo.org/forgejo/runner:12

USER root

# Install Docker CLI + Buildx + Compose plugin inside the runner image.
# Supports both Alpine-based and Debian/Ubuntu-based base images.
RUN set -eux; \
  if command -v apk >/dev/null 2>&1; then \
    apk add --no-cache docker-cli docker-cli-buildx docker-cli-compose ca-certificates; \
  elif command -v apt-get >/dev/null 2>&1; then \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo ${VERSION_CODENAME}) stable" > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin; \
    rm -rf /var/lib/apt/lists/*; \
  else \
    echo "Unsupported base image: no apk or apt-get available"; \
    exit 1; \
  fi; \
  docker --version; \
  docker buildx version; \
  docker compose version

# Keep the same runtime user model as the upstream runner image.
# (The base image command/entrypoint is inherited.)
