# Maya AI Sandbox Runner Container (Decision D14)
# Enforces non-root execution (UID 10001), no host Docker socket mount, read-only root compatibility.

FROM python:3.12-slim

# Install minimal build tools and git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group with fixed UID/GID 10001 (Decision D14)
RUN groupadd -g 10001 sandbox && \
    useradd -u 10001 -g 10001 -m -s /bin/bash sandbox

# Set up ephemeral workspace directory
RUN mkdir -p /workspace /tmp/sandbox && \
    chown -R sandbox:sandbox /workspace /tmp/sandbox

WORKDIR /workspace
USER sandbox:sandbox

# Environment variables
ENV HOME=/home/sandbox \
    TMPDIR=/tmp/sandbox \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Default entrypoint
CMD ["python3", "--version"]
