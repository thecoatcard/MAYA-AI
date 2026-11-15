import path from "node:path";

/**
 * Locate the repository root by walking up until docker-compose.yml and backend/alembic.ini sit
 * side by side. Only existence is checked.
 * @param {string} start
 * @param {(p: string) => boolean} exists
 * @returns {string | null}
 */
export function findRepoRoot(start, exists) {
  let dir = path.resolve(start);
  for (;;) {
    if (
      exists(path.join(dir, "docker-compose.yml")) &&
      exists(path.join(dir, "backend", "alembic.ini"))
    ) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/**
 * Pick the env file handed to Docker Compose, Alembic and the API: MAYA_ENV_FILE, else the
 * repo-root `.env` if it exists, else `.env.example`. Only the PATH is returned; the contents are
 * never read here or anywhere in the shell.
 * @param {{ env: Record<string, string | undefined>, repoRoot: string, exists: (p: string) => boolean }} ctx
 * @returns {{ file: string, source: 'MAYA_ENV_FILE' | '.env' | '.env.example' }}
 */
export function resolveEnvFile({ env, repoRoot, exists }) {
  const chosen = (env.MAYA_ENV_FILE ?? "").trim();
  if (chosen) {
    const file = path.resolve(chosen);
    if (!exists(file)) throw new Error("MAYA_ENV_FILE points to a file that does not exist.");
    return { file, source: "MAYA_ENV_FILE" };
  }
  const dotenv = path.join(repoRoot, ".env");
  if (exists(dotenv)) return { file: dotenv, source: ".env" };
  return { file: path.join(repoRoot, ".env.example"), source: ".env.example" };
}

/**
 * Resolve every location the supervisor needs.
 * Dev: everything is found from the repo checkout. Packaged: the web export is a bundled resource;
 * the backend must be a checkout (MAYA_REPO_ROOT) or a configured python (MAYA_PYTHON), because the
 * Python runtime is not bundled yet (known gap, ADR-0013).
 * @param {{ isPackaged: boolean, appDir: string, resourcesPath?: string,
 *   env: Record<string, string | undefined>, exists: (p: string) => boolean }} ctx
 */
export function resolveLocations({ isPackaged, appDir, resourcesPath, env, exists }) {
  const repoRoot = isPackaged ? env.MAYA_REPO_ROOT?.trim() || null : findRepoRoot(appDir, exists);
  if (!repoRoot) {
    throw new Error(
      "Could not find the MAYA repository. Set MAYA_REPO_ROOT to a checkout that has backend/.venv.",
    );
  }
  const backendDir = path.join(repoRoot, "backend");
  const python = env.MAYA_PYTHON?.trim() || path.join(backendDir, ".venv", "Scripts", "python.exe");
  const webDir =
    isPackaged && resourcesPath
      ? path.join(resourcesPath, "web")
      : path.join(repoRoot, "apps", "web", ".next-desktop");
  return {
    repoRoot,
    backendDir,
    python,
    webDir,
    composeFile: path.join(repoRoot, "docker-compose.yml"),
  };
}
