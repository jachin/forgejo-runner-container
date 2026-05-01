FROM docker:dind AS docker-tools

FROM data.forgejo.org/forgejo/runner:12

USER root

# Copy Docker CLI and plugins from docker:dind image to avoid package-manager network dependencies.
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

RUN set -eux; \
  docker --version; \
  docker buildx version; \
  docker compose version

# Keep upstream runner entrypoint/cmd.
