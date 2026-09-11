FROM python:3.14-slim

# System deps for host introspection
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps smartmontools iproute2 curl \
    && ARCH=$(uname -m) \
    && case "$ARCH" in \
         x86_64)  DOCKER_ARCH="x86_64" ;; \
         aarch64) DOCKER_ARCH="aarch64" ;; \
         *)       echo "Unsupported arch: $ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-27.5.1.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source + package metadata together; [project].version is canonical.
COPY pyproject.toml README.md ./
COPY src/ ./src/
# static/ must be present before `pip install` so hatchling's force-include
# (which maps static/ → buoy/static in the wheel) can find it at build time.
COPY static/ ./static/

# Install the package (deps + buoy itself). zigbee2mqtt is an optional
# extra (paho-mqtt is a small, pure-Python-friendly dep) installed by
# default so the shipped image can actually run that plugin without a
# custom build — confirmed 2026-08-23 that a plain `pip install .` here
# left it unimportable (ImportError, isolated per-plugin by the loader,
# but silently absent from the dashboard with no obvious signal why).
RUN pip install --no-cache-dir ".[zigbee2mqtt]"
COPY buoy.yaml.example ./buoy.yaml.example

# Create plugin + data directories. Owned by uid/gid 1000 (not just root) so
# a hardened deployment can run as a non-root user via compose's `user:`
# (see docker-compose.minimal.yml) without a separate chown step — this is
# a no-op for the default root-user run, since root can write here either
# way. The image's default user stays root: buoy's host-introspection
# features (nsenter into the host mount/PID namespace, docker.sock) need
# it. See SEC-10 hardening docs (README) for what each privilege tier
# actually unlocks, verified against a real container rather than assumed.
RUN mkdir -p /plugins /data && chown -R 1000:1000 /plugins /data

EXPOSE 8090

VOLUME ["/plugins", "/data", "/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8090/api/health || exit 1

ENTRYPOINT ["python", "-m", "buoy"]
CMD ["--config", "/config/buoy.yaml"]
