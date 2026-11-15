"use client";

/**
 * Compatibility re-export. The theme logic lives in lib/theme.ts and the single-icon toggle in
 * components/ui/ThemeToggle.tsx (UI design system foundation). This file stays so existing
 * imports keep working until the page packages migrate them; new code imports from those two.
 */
export { ThemeToggle } from "../ui/ThemeToggle";
export {
  THEME_STORAGE_KEY,
  readStoredTheme,
  storeTheme,
  type Theme,
} from "../../lib/theme";
