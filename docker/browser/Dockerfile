# Maya AI Browser Automation Sandbox Container (Phase 13, Decisions D4, D14)
# Enforces non-root execution (UID 10001), no host Docker socket mount, read-only root compatibility.

FROM node:22-slim

# Install system dependencies for headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group with fixed UID/GID 10001 (Decision D14)
RUN groupadd -g 10001 browser_runner && \
    useradd -u 10001 -g 10001 -m -s /bin/bash browser_runner

# Ephemeral workspace and sandbox scratch dirs
RUN mkdir -p /workspace /tmp/browser && \
    chown -R browser_runner:browser_runner /workspace /tmp/browser

WORKDIR /workspace

# Install Playwright / MCP dependencies
RUN npm install -g @playwright/mcp@latest playwright@latest

USER browser_runner:browser_runner

ENV HOME=/home/browser_runner \
    TMPDIR=/tmp/browser \
    NODE_ENV=production \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    CHROME_PATH=/usr/bin/chromium

# Default entrypoint runs the MCP browser server
CMD ["npx", "@playwright/mcp"]
