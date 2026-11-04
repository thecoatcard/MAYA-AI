// Loads the machine-readable governance policy (governance/governance.config.json).
import { readJson, ROOT } from './fsutil.mjs';

export const CONFIG_PATH = 'governance/governance.config.json';

export function loadConfig(root = ROOT) {
  return readJson(root, CONFIG_PATH);
}
