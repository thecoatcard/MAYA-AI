import type { NextConfig } from "next";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  resolveApiProxyTarget,
  resolveProxyTimeoutMs,
} from "./src/lib/api-proxy-target";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  outputFileTracingRoot: dirname(fileURLToPath(import.meta.url)),
  experimental: {
    // The default (30 s) would end a silent chat stream before the API's deadline.
    proxyTimeout: resolveProxyTimeoutMs(
      process.env.MAYA_CHAT_REPLY_DEADLINE_SECONDS,
    ),
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${resolveApiProxyTarget(process.env.MAYA_API_PROXY_TARGET)}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
