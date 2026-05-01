FROM docker:dind AS docker-tools
FROM node:20-alpine AS node-tools

FROM data.forgejo.org/forgejo/runner:12

USER root

# Copy Docker CLI and plugins from docker:dind image to avoid package-manager network dependencies.
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

# Copy Node.js toolchain from official Node image (includes npm/npx/corepack).
# Copy whole /usr/local/bin to preserve symlinks for npm/npx/corepack.
COPY --from=node-tools /usr/local/bin /usr/local/bin
COPY --from=node-tools /usr/local/lib/node_modules /usr/local/lib/node_modules

# Copy runtime libs required by Node from Alpine image.
COPY --from=node-tools /usr/lib/libstdc++.so.6 /usr/lib/libstdc++.so.6
COPY --from=node-tools /usr/lib/libgcc_s.so.1 /usr/lib/libgcc_s.so.1

RUN set -eux; \
  docker --version; \
  docker buildx version; \
  docker compose version; \
  node --version; \
  npm --version

# Keep upstream runner entrypoint/cmd.
