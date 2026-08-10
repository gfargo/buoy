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

# Copy source + metadata together (hatchling reads src/buoy/__init__.py for version)
COPY pyproject.toml README.md ./
COPY src/ ./src/
# static/ must be present before `pip install` so hatchling's force-include
# (which maps static/ → buoy/static in the wheel) can find it at build time.
COPY static/ ./static/

# Install the package (deps + buoy itself)
RUN pip install --no-cache-dir .
COPY buoy.yaml.example ./buoy.yaml.example

# Create plugin + data directories
RUN mkdir -p /plugins /data

EXPOSE 8090

VOLUME ["/plugins", "/data", "/config"]

ENTRYPOINT ["python", "-m", "buoy"]
CMD ["--config", "/config/buoy.yaml"]
