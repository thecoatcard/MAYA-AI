// Build the static web export the desktop app serves: apps/web/.next-desktop (see next.config.ts).
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "web");
const next = createRequire(path.join(webDir, "package.json")).resolve("next/dist/bin/next");
const result = spawnSync(process.execPath, [next, "build"], {
  cwd: webDir,
  stdio: "inherit",
  env: { ...process.env, MAYA_DESKTOP_EXPORT: "1" },
});
process.exit(result.status ?? 1);
