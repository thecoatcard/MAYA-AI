import type { NextConfig } from "next";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  resolveApiProxyTarget,
  resolveProxyTimeoutMs,
} from "./src/lib/api-proxy-target";

// The desktop app serves the exported web UI from the API itself (same origin), so there is no
// Next server and no proxy: rewrites are unsupported with `output: "export"` and are not needed.
const desktopExport = process.env.MAYA_DESKTOP_EXPORT === "1";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  outputFileTracingRoot: dirname(fileURLToPath(import.meta.url)),
  ...(desktopExport
    ? {
        output: "export" as const,
        trailingSlash: true,
        distDir: ".next-desktop",
      }
    : {
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
      }),
};

export default nextConfig;
