# UI design system: "Console" foundation

- **Status:** Implemented and tested in `apps/web` (work package WP1a, 2026-11-09). Direction C "Console" was chosen by the owner on 2026-11-09 (see [UI_UX_STANDARDS.md](UI_UX_STANDARDS.md)). The shell and page packages in [UI_REDESIGN_WORK_PACKAGES.md](UI_REDESIGN_WORK_PACKAGES.md) are **not** done yet and build on this.
- **Code:** tokens `apps/web/src/app/globals.css`; primitives `apps/web/src/components/ui/`; theme logic `apps/web/src/lib/theme.ts`; fonts `apps/web/public/fonts/ibm-plex/`; QA page `/design-system` (development only).

## 1. Themes

Exactly two themes: **dark (default)** and **light**. There is no "system" mode and no `prefers-color-scheme` logic anywhere.

| Item               | Contract                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| :----------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Attribute          | `<html data-theme="dark">` or `data-theme="light"`. No attribute means dark.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Storage            | `localStorage["maya.theme"]` = `"dark"` or `"light"`, per device, read and written inside try/catch. Anything else (including the old `"system"`) reads as dark. Blocked storage means the choice lasts until the page closes.                                                                                                                                                                                                                                                                                                                                                 |
| Before first paint | An inline script in the root layout (`THEME_BOOTSTRAP_SCRIPT`) sets the attribute. The server renders `data-theme="dark"` and `suppressHydrationWarning` covers that one attribute. The API's CSP already allows inline scripts for the exported pages.                                                                                                                                                                                                                                                                                                                        |
| Toggle             | One icon button, `components/ui/ThemeToggle.tsx`: sun while dark, moon while light. `aria-label` and `title` state the action ("Switch to light theme"). It is not also `aria-pressed` (a changing label plus a pressed state would read twice). Both icons stay mounted in a fixed 32 px box (44 px on coarse pointers), so nothing shifts; the cross-fade and quarter turn take 120 ms and are instant under reduced motion. It is mounted in the title bar (`AppShell`) and the sign-in headers. The Settings page has an Appearance section that only says the toggle is in the title bar, so the toggle is not duplicated there. |
| Components         | Never read the theme. They use token classes (`bg-maya-pane`, `text-maya-text`, ...) and the values follow the attribute.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Hook               | `useTheme()` returns `{ theme, setTheme, toggleTheme }`; all toggles on a page stay in sync (custom event, plus the `storage` event across tabs).                                                                                                                                                                                                                                                                                                                                                                                                                              |

## 2. Tokens

All tokens are CSS variables `--maya-*` in `globals.css`, mapped into Tailwind 4 through `@theme inline` (`bg-maya-*`, `text-maya-*`, `border-maya-*`, `rounded-maya-*`, `shadow-maya-*`, `text-maya-small`, `z-maya-modal`, ...).

| Group                | Tokens (dark values; light values are in the file)                                                                                                                                                             |
| :------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Surfaces             | `bg` #0b0d11, `pane` #12151b, `raised` #1b1f27, `overlay` #232833, `hover` #262b36, `code-bg`, `disabled-bg`, `scrim`                                                                                          |
| Text                 | `text` #e8eaee, `text-secondary` #c2c7d1, `text-muted` #98a0ae, `accent-text` (on accent), `disabled-text`                                                                                                     |
| Seams                | `border` #272c36 (hairline, decorative), `border-strong` #6a7384 (control edge, 3:1), `focus`                                                                                                                  |
| Accent (ultramarine) | `accent` #8da0ff, `accent-hover`, `accent-active`, `accent-soft` (tint), `link`                                                                                                                                |
| Semantic             | `success`, `warning`, `danger`, `info`, each with a `-soft` tint                                                                                                                                               |
| Other                | `selection`, `selection-text`, `scroll-thumb`, `scroll-thumb-hover`, syntax `tok-*`                                                                                                                            |
| Type (rem)           | `text-label` 0.75, `text-caption` 0.8125, `text-small` 0.875, `text-body` 1, `text-heading` 1.0625, `text-title` 1.5, `text-display` 2.125, `text-mono` 0.8125, with matching `leading-*` and `tracking-label` |
| Spacing              | `space-0,1,2,3,4,5,6,8,10,12,14,16` (4 px base)                                                                                                                                                                |
| Radius               | `radius-sm` 2 px, `md` 4 px, `lg` 6 px (near-square)                                                                                                                                                           |
| Elevation            | `shadow-1` (1 px ring), `shadow-2`, `shadow-3` (neutral shadow plus ring, never a glow)                                                                                                                        |
| Layers               | `z-base` 0, `sticky` 10, `nav` 20, `panel` 30, `dropdown` 40, `modal` 50, `toast` 60, `tooltip` 70 (`z-maya-*` utilities)                                                                                      |
| Motion               | `dur-instant` 60 ms, `dur-fast` 120, `dur-base` 200, `dur-slow` 320, all multiplied by `--maya-motion-scale` (1, and 0 under reduced motion); `ease-out`, `ease-in-out`, `ease-in`, `ease-standard`            |
| Layout               | `nav-width` 3.5rem, `sidebar-width` 18.75rem, `panel-width` 21.25rem, `header-height` 3rem, `column` 44rem, `column-wide` 64rem, `measure` 70ch, `control` 2rem, `touch` 2.75rem, `safe-bottom`, `safe-right`  |

Focus ring: 2 px solid `--maya-focus`, 2 px offset, on `:focus-visible` everywhere. Forced colours (`forced-colors: active`): focus and seams switch to `Highlight` and `CanvasText`.

### Legacy aliases

The old copy of the tokens in `components/chat/chat.css` is deleted (WP2): `globals.css` is the only place tokens are declared, on `:root` and `:root[data-theme="light"]`. `chat.css` now declares only a few non-theme values it needs itself (`--maya-composer-max`, `--maya-dot`, ripple and loop timings; asserted by `chat.css.test.tsx`) and uses the real tokens, including the legacy alias names that `globals.css` still defines (`--maya-surface`, `--maya-control-border`, `--maya-glass-*`, `--maya-state-layer`, `--maya-tracking-*`). Its class names must not reuse the primitives' (`maya-skeleton`, `maya-empty`, `maya-notice`, `maya-code`): an unlayered rule would out-rank the primitive's utilities (asserted by the same test).

## 3. Fonts (self-hosted, SIL OFL 1.1)

Fetched once from the npm registry (`npm pack`, outside the repository; not a dependency and not in `package.json`). `font-display: swap`, `unicode-range` per subset, a system-font fallback stack, and nothing external is contacted at runtime (verified in Edge: no external request).

| Family                   | Package and version                          | Files in `public/fonts/ibm-plex/`                              |                  Size |
| :----------------------- | :------------------------------------------- | :------------------------------------------------------------- | --------------------: |
| IBM Plex Sans            | `@fontsource/ibm-plex-sans` 5.2.8            | `ibm-plex-sans-latin-400/500/600-normal.woff2`                 | 22.1 / 23.6 / 23.7 KB |
| IBM Plex Mono            | `@fontsource/ibm-plex-mono` 5.2.7            | `ibm-plex-mono-latin-400/500-normal.woff2`                     |        14.4 / 14.5 KB |
| IBM Plex Sans Devanagari | `@fontsource/ibm-plex-sans-devanagari` 5.2.8 | `ibm-plex-sans-devanagari-devanagari-400/500/600-normal.woff2` | 75.6 / 77.4 / 77.3 KB |

Licence texts (same folder): `LICENSE-ibm-plex-sans.txt`, `LICENSE-ibm-plex-mono.txt`, `LICENSE-ibm-plex-sans-devanagari.txt`. Not shipped: latin-ext, cyrillic, greek and vietnamese subsets, italics, weights 300 and 700 (the packages have them; add on request). `--maya-font-sans` lists Plex Sans, Plex Sans Devanagari, then the system faces; `--maya-font-mono` lists Plex Mono then system monospace.

## 4. Type rules

Prose and form text 16 px. Chrome text (labels, nav, meta, timestamps) is never below 13 px (`text-maya-caption`); mono labels are never below 12 px and are uppercase only with tracking (`.maya-label`). Sizes are rem and `html` stays at 100 percent, so browser zoom and OS text scaling apply. Use `.maya-tabular` for numbers in tables and costs.

## 5. Primitives (`components/ui`, no new dependency, lucide icons only)

Import from `components/ui`. The older `Glass*` components remain for the views that use them; `GlassInput` and `GlassTextarea` were moved onto these tokens (13 px chrome text, 16 px field text, 32 px clear button). Pages must not add their own buttons, badges or panes.

| Primitive                                     | Overflow and state rules                                                                                                                                                                                                                           |
| :-------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Pane`                                        | Seam border, `header` / body / `footer` slots. The body is the **only** scroll region (`min-h-0 min-w-0 overflow-auto`); `bodyLabel` makes it a focusable named region.                                                                            |
| `Stack`, `Row`                                | Flex with a token `gap` and `min-w-0` by default; wraps only when asked.                                                                                                                                                                           |
| `Truncate`, `WrapText`                        | `Truncate`: one-line ellipsis or `lines={n}` clamp, full text in `title`. `WrapText` breaks URLs, hashes and paths anywhere.                                                                                                                       |
| `ScrollArea`, `CodeBlockShell`, `TableScroll` | Scroll inside their own box, focusable and named (`label` required); never widen the parent.                                                                                                                                                       |
| `Button`, `IconButton`                        | primary / secondary / ghost / danger, sm / md / lg; 32 px minimum hit height, 44 px on coarse pointers; `loading` keeps the label's space (no layout shift) and blocks clicks; native `disabled`. `IconButton` requires `label`.                   |
| `Badge`, `StatusPill`                         | Icon plus text, never colour alone; `StatusPill` maps the five task states.                                                                                                                                                                        |
| `Skeleton`                                    | Shimmer (static under reduced motion), `aria-hidden`; put `aria-busy` on the loading region.                                                                                                                                                       |
| `EmptyState`, `ErrorState`                    | Next-step slot; `ErrorState` is `role="alert"` with a retry slot. A failure is never shown as empty.                                                                                                                                               |
| `Notice`, `ToastRegion`, `useToasts`          | Toasts sit on `z-maya-toast`, top centre below the header, inside the safe area, so they never cover the composer (bottom) or the work panel controls. `--maya-toast-top` lets a shell move them. Non-error toasts dismiss after 6 s; errors stay. |
| `Popover`, `Tooltip`                          | Portal on the dropdown or tooltip layer; flips to the other side and shifts to stay inside the viewport (`computePlacement`, unit tested); width capped to the window; Escape and outside press close.                                             |
| `Kbd`, `ThemeToggle`                          | As named.                                                                                                                                                                                                                                          |

## 6. Motion kit (CSS only, transform and opacity)

| Class                                                           | Job                                                                        | Reduced-motion equivalent                          |
| :-------------------------------------------------------------- | :------------------------------------------------------------------------- | :------------------------------------------------- |
| `motion-enter`                                                  | A panel or page appears from the nav side (320 ms)                         | Appears instantly                                  |
| `motion-message`                                                | A new message arrives (200 ms rise)                                        | Appears instantly                                  |
| `motion-toast-in`, `motion-toast-out`                           | Toast arrives and leaves from its edge                                     | Instant                                            |
| `motion-progress`                                               | Real progress: a full-width fill scaled by `--maya-progress` (never width) | Jumps to the value; the percentage text carries it |
| `motion-progress-indeterminate`                                 | "Working, amount unknown", always with a label                             | Static bar plus the label                          |
| `motion-shimmer`                                                | Loading placeholder                                                        | Flat block                                         |
| `motion-orb` (`data-state` idle, listening, thinking, speaking) | Nine-bar level meter; the state is also written as text                    | Bars hold their resting heights                    |
| `motion-interactive`, `motion-press`, `motion-swap`             | Hover, press and theme-icon feedback (colour, opacity, 1 px press)         | Instant                                            |
| `motion-none`                                                   | Guard: no animation or transition inside. Wrap approval controls in it     | n/a                                                |

Loops pause while `<html data-paused>` is set (`WorkspaceProvider` sets it on `visibilitychange`). Under reduced motion every duration token is 0 and a global rule collapses any remaining animation.

## 7. Contrast proof

Computed by `apps/web/scripts/contrast-report.mjs` (WCAG relative luminance, from the token values in `globals.css`) and enforced by `src/app/design-tokens.test.tsx`. Text pairs need 4.5:1; UI boundaries, focus ring, accent fill and scrollbar thumbs need 3:1; disabled text is exempt in WCAG but is held to 3:1. Re-run: `node apps/web/scripts/contrast-report.mjs`. All 134 checks (67 per theme) pass.

## 8. Guard: no raw colours

`src/test/token-scan.test.tsx` scans every shipped source file for raw `bg-white`, `text-black`, palette utilities (`zinc`, `slate`, `gray`, `red`, `blue` and the rest), arbitrary colour values, `#hex`, `rgb()` literals, `backdrop-blur` or `backdrop-filter`, gradient utilities and glow shadows. Files not yet migrated are listed with their current count in `LEGACY` in that test: a file outside the list must have zero; a listed file cannot get worse; when a file reaches zero its entry must be deleted (a stale or loose entry fails). Each page package removes its files from the list. Never add a file or raise a number.

## 9. How page packages use this

1. Replace raw colour classes with token classes and delete the file from `LEGACY`.
2. Build layouts from `Pane`, `Stack` and `Row`; give every grid and flex child `min-w-0`; only a `Pane` body, `ScrollArea`, `CodeBlockShell` or `TableScroll` scrolls.
3. Wrap long strings in `Truncate` (with `title`) or `WrapText`.
4. Use the shared `Button`, `IconButton`, `Badge`, `StatusPill`, `EmptyState`, `ErrorState`, `Skeleton`, `Notice`, toasts, `Popover` and `Tooltip`; do not write new ones.
5. Use the motion classes only for their stated jobs; put approval controls in `motion-none`.
6. Check the page in both themes at 360, 768, 1280 and 1920 px and at 200 percent zoom (the `/design-system` page is the reference).

## 10. QA showcase

`/design-system` (`app/(dev)/design-system/page.tsx`) renders every token and primitive in loading, empty, error and long-content states, with the theme toggle. It is not linked from the product. It renders in `next dev` only; every production build, including the desktop export, serves a 404 for that path (verified: the exported `design-system/` page contains no showcase content). The showcase is also rendered and asserted in `DesignSystemShowcase.test.tsx`.

## 11. Verification status

| Check                                                                                                                              | Result                                                                                                                               |
| :--------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| Unit and component tests, contrast, font files, token scan                                                                         | Tested (vitest)                                                                                                                      |
| Edge (Playwright) on `/design-system` at 360, 768, 1280, 1920 px in both themes                                                    | Tested: no horizontal page overflow, fonts loaded from the local server, no external request, theme attribute and background correct |
| Reduced motion (emulated)                                                                                                          | Tested: the orb animation is `none`                                                                                                  |
| Real Electron window, OS text scaling, forced colours in a real browser, 200 percent zoom, frame rate and layout-shift measurement | Not run                                                                                                                              |

## Appendix: contrast table (generated)

### Dark theme

| Group | Pair                                          | Foreground | Background |   Ratio | Needs | Result |
| :---- | :-------------------------------------------- | :--------- | :--------- | ------: | ----: | :----- |
| Text  | text on bg                                    | #e8eaee    | #0b0d11    | 16.15:1 | 4.5:1 | Pass   |
| Text  | text-secondary on bg                          | #c2c7d1    | #0b0d11    | 11.47:1 | 4.5:1 | Pass   |
| Text  | text-muted on bg                              | #98a0ae    | #0b0d11    |  7.38:1 | 4.5:1 | Pass   |
| Text  | link on bg                                    | #9dafff    | #0b0d11    |  9.27:1 | 4.5:1 | Pass   |
| UI    | border-strong on bg (control edge)            | #6a7384    | #0b0d11    |  4.07:1 |   3:1 | Pass   |
| UI    | focus ring on bg                              | #9dafff    | #0b0d11    |  9.27:1 |   3:1 | Pass   |
| UI    | accent fill on bg                             | #8da0ff    | #0b0d11    |  7.97:1 |   3:1 | Pass   |
| Text  | text on pane                                  | #e8eaee    | #12151b    | 15.18:1 | 4.5:1 | Pass   |
| Text  | text-secondary on pane                        | #c2c7d1    | #12151b    | 10.78:1 | 4.5:1 | Pass   |
| Text  | text-muted on pane                            | #98a0ae    | #12151b    |  6.94:1 | 4.5:1 | Pass   |
| Text  | link on pane                                  | #9dafff    | #12151b    |  8.71:1 | 4.5:1 | Pass   |
| UI    | border-strong on pane (control edge)          | #6a7384    | #12151b    |  3.83:1 |   3:1 | Pass   |
| UI    | focus ring on pane                            | #9dafff    | #12151b    |  8.71:1 |   3:1 | Pass   |
| UI    | accent fill on pane                           | #8da0ff    | #12151b    |  7.49:1 |   3:1 | Pass   |
| Text  | text on raised                                | #e8eaee    | #1b1f27    | 13.71:1 | 4.5:1 | Pass   |
| Text  | text-secondary on raised                      | #c2c7d1    | #1b1f27    |  9.74:1 | 4.5:1 | Pass   |
| Text  | text-muted on raised                          | #98a0ae    | #1b1f27    |  6.27:1 | 4.5:1 | Pass   |
| Text  | link on raised                                | #9dafff    | #1b1f27    |  7.87:1 | 4.5:1 | Pass   |
| UI    | border-strong on raised (control edge)        | #6a7384    | #1b1f27    |  3.46:1 |   3:1 | Pass   |
| UI    | focus ring on raised                          | #9dafff    | #1b1f27    |  7.87:1 |   3:1 | Pass   |
| UI    | accent fill on raised                         | #8da0ff    | #1b1f27    |  6.77:1 |   3:1 | Pass   |
| Text  | text on overlay                               | #e8eaee    | #232833    | 12.25:1 | 4.5:1 | Pass   |
| Text  | text-secondary on overlay                     | #c2c7d1    | #232833    |  8.70:1 | 4.5:1 | Pass   |
| Text  | text-muted on overlay                         | #98a0ae    | #232833    |  5.60:1 | 4.5:1 | Pass   |
| Text  | link on overlay                               | #9dafff    | #232833    |  7.04:1 | 4.5:1 | Pass   |
| UI    | border-strong on overlay (control edge)       | #6a7384    | #232833    |  3.09:1 |   3:1 | Pass   |
| UI    | focus ring on overlay                         | #9dafff    | #232833    |  7.04:1 |   3:1 | Pass   |
| UI    | accent fill on overlay                        | #8da0ff    | #232833    |  6.05:1 |   3:1 | Pass   |
| Text  | text-muted on hover                           | #98a0ae    | #262b36    |  5.38:1 | 4.5:1 | Pass   |
| Text  | text on hover                                 | #e8eaee    | #262b36    | 11.77:1 | 4.5:1 | Pass   |
| Text  | text-secondary on hover                       | #c2c7d1    | #262b36    |  8.36:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent (primary button)        | #0a1030    | #8da0ff    |  7.62:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent-hover (primary button)  | #0a1030    | #9dafff    |  8.86:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent-active (primary button) | #0a1030    | #b1beff    | 10.34:1 | 4.5:1 | Pass   |
| Text  | link on accent-soft (accent badge)            | #9dafff    | #1b2250    |  7.19:1 | 4.5:1 | Pass   |
| UI    | accent border on accent-soft                  | #8da0ff    | #1b2250    |  6.18:1 |   3:1 | Pass   |
| Text  | success on pane                               | #6fd29a    | #12151b    |  9.89:1 | 4.5:1 | Pass   |
| Text  | success on success-soft (badge)               | #6fd29a    | #13301f    |  7.73:1 | 4.5:1 | Pass   |
| Text  | text on success-soft (notice body)            | #e8eaee    | #13301f    | 11.86:1 | 4.5:1 | Pass   |
| Text  | text-secondary on success-soft                | #c2c7d1    | #13301f    |  8.42:1 | 4.5:1 | Pass   |
| UI    | success border on pane                        | #6fd29a    | #12151b    |  9.89:1 |   3:1 | Pass   |
| Text  | warning on pane                               | #e6b450    | #12151b    |  9.58:1 | 4.5:1 | Pass   |
| Text  | warning on warning-soft (badge)               | #e6b450    | #35290e    |  7.46:1 | 4.5:1 | Pass   |
| Text  | text on warning-soft (notice body)            | #e8eaee    | #35290e    | 11.82:1 | 4.5:1 | Pass   |
| Text  | text-secondary on warning-soft                | #c2c7d1    | #35290e    |  8.40:1 | 4.5:1 | Pass   |
| UI    | warning border on pane                        | #e6b450    | #12151b    |  9.58:1 |   3:1 | Pass   |
| Text  | danger on pane                                | #ff8a80    | #12151b    |  8.01:1 | 4.5:1 | Pass   |
| Text  | danger on danger-soft (badge)                 | #ff8a80    | #3d1c19    |  6.67:1 | 4.5:1 | Pass   |
| Text  | text on danger-soft (notice body)             | #e8eaee    | #3d1c19    | 12.65:1 | 4.5:1 | Pass   |
| Text  | text-secondary on danger-soft                 | #c2c7d1    | #3d1c19    |  8.98:1 | 4.5:1 | Pass   |
| UI    | danger border on pane                         | #ff8a80    | #12151b    |  8.01:1 |   3:1 | Pass   |
| Text  | info on pane                                  | #86b6f2    | #12151b    |  8.69:1 | 4.5:1 | Pass   |
| Text  | info on info-soft (badge)                     | #86b6f2    | #16294a    |  6.89:1 | 4.5:1 | Pass   |
| Text  | text on info-soft (notice body)               | #e8eaee    | #16294a    | 12.02:1 | 4.5:1 | Pass   |
| Text  | text-secondary on info-soft                   | #c2c7d1    | #16294a    |  8.54:1 | 4.5:1 | Pass   |
| UI    | info border on pane                           | #86b6f2    | #12151b    |  8.69:1 |   3:1 | Pass   |
| Text  | selection-text on selection                   | #ffffff    | #2b3680    | 10.81:1 | 4.5:1 | Pass   |
| Text  | disabled-text on disabled-bg                  | #8a93a3    | #1b1f27    |  5.33:1 |   3:1 | Pass   |
| Text  | text on code-bg                               | #e8eaee    | #0e1015    | 15.80:1 | 4.5:1 | Pass   |
| Text  | syntax keyword on code-bg                     | #9dafff    | #0e1015    |  9.07:1 | 4.5:1 | Pass   |
| Text  | syntax string on code-bg                      | #f0c27a    | #0e1015    | 11.51:1 | 4.5:1 | Pass   |
| Text  | syntax number on code-bg                      | #86b6f2    | #0e1015    |  9.05:1 | 4.5:1 | Pass   |
| Text  | syntax comment on code-bg                     | #98a0ae    | #0e1015    |  7.23:1 | 4.5:1 | Pass   |
| Text  | syntax name on code-bg                        | #d3b4ff    | #0e1015    | 10.64:1 | 4.5:1 | Pass   |
| Text  | syntax type on code-bg                        | #6fd29a    | #0e1015    | 10.29:1 | 4.5:1 | Pass   |
| UI    | scrollbar thumb on pane                       | #6a7384    | #12151b    |  3.83:1 |   3:1 | Pass   |
| UI    | scrollbar thumb hover on pane                 | #8a93a3    | #12151b    |  5.90:1 |   3:1 | Pass   |

### Light theme

| Group | Pair                                          | Foreground | Background |   Ratio | Needs | Result |
| :---- | :-------------------------------------------- | :--------- | :--------- | ------: | ----: | :----- |
| Text  | text on bg                                    | #101216    | #f1f2f5    | 16.75:1 | 4.5:1 | Pass   |
| Text  | text-secondary on bg                          | #343944    | #f1f2f5    | 10.34:1 | 4.5:1 | Pass   |
| Text  | text-muted on bg                              | #525967    | #f1f2f5    |  6.29:1 | 4.5:1 | Pass   |
| Text  | link on bg                                    | #2a3fc4    | #f1f2f5    |  7.20:1 | 4.5:1 | Pass   |
| UI    | border-strong on bg (control edge)            | #7c8392    | #f1f2f5    |  3.40:1 |   3:1 | Pass   |
| UI    | focus ring on bg                              | #3046d4    | #f1f2f5    |  6.35:1 |   3:1 | Pass   |
| UI    | accent fill on bg                             | #3046d4    | #f1f2f5    |  6.35:1 |   3:1 | Pass   |
| Text  | text on pane                                  | #101216    | #ffffff    | 18.75:1 | 4.5:1 | Pass   |
| Text  | text-secondary on pane                        | #343944    | #ffffff    | 11.57:1 | 4.5:1 | Pass   |
| Text  | text-muted on pane                            | #525967    | #ffffff    |  7.04:1 | 4.5:1 | Pass   |
| Text  | link on pane                                  | #2a3fc4    | #ffffff    |  8.06:1 | 4.5:1 | Pass   |
| UI    | border-strong on pane (control edge)          | #7c8392    | #ffffff    |  3.81:1 |   3:1 | Pass   |
| UI    | focus ring on pane                            | #3046d4    | #ffffff    |  7.11:1 |   3:1 | Pass   |
| UI    | accent fill on pane                           | #3046d4    | #ffffff    |  7.11:1 |   3:1 | Pass   |
| Text  | text on raised                                | #101216    | #e6e8ed    | 15.29:1 | 4.5:1 | Pass   |
| Text  | text-secondary on raised                      | #343944    | #e6e8ed    |  9.44:1 | 4.5:1 | Pass   |
| Text  | text-muted on raised                          | #525967    | #e6e8ed    |  5.74:1 | 4.5:1 | Pass   |
| Text  | link on raised                                | #2a3fc4    | #e6e8ed    |  6.57:1 | 4.5:1 | Pass   |
| UI    | border-strong on raised (control edge)        | #7c8392    | #e6e8ed    |  3.10:1 |   3:1 | Pass   |
| UI    | focus ring on raised                          | #3046d4    | #e6e8ed    |  5.80:1 |   3:1 | Pass   |
| UI    | accent fill on raised                         | #3046d4    | #e6e8ed    |  5.80:1 |   3:1 | Pass   |
| Text  | text on overlay                               | #101216    | #ffffff    | 18.75:1 | 4.5:1 | Pass   |
| Text  | text-secondary on overlay                     | #343944    | #ffffff    | 11.57:1 | 4.5:1 | Pass   |
| Text  | text-muted on overlay                         | #525967    | #ffffff    |  7.04:1 | 4.5:1 | Pass   |
| Text  | link on overlay                               | #2a3fc4    | #ffffff    |  8.06:1 | 4.5:1 | Pass   |
| UI    | border-strong on overlay (control edge)       | #7c8392    | #ffffff    |  3.81:1 |   3:1 | Pass   |
| UI    | focus ring on overlay                         | #3046d4    | #ffffff    |  7.11:1 |   3:1 | Pass   |
| UI    | accent fill on overlay                        | #3046d4    | #ffffff    |  7.11:1 |   3:1 | Pass   |
| Text  | text-muted on hover                           | #525967    | #dcdfe6    |  5.28:1 | 4.5:1 | Pass   |
| Text  | text on hover                                 | #101216    | #dcdfe6    | 14.05:1 | 4.5:1 | Pass   |
| Text  | text-secondary on hover                       | #343944    | #dcdfe6    |  8.67:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent (primary button)        | #ffffff    | #3046d4    |  7.11:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent-hover (primary button)  | #ffffff    | #2a3fc4    |  8.06:1 | 4.5:1 | Pass   |
| Text  | accent-text on accent-active (primary button) | #ffffff    | #2335b0    |  9.51:1 | 4.5:1 | Pass   |
| Text  | link on accent-soft (accent badge)            | #2a3fc4    | #e2e6fb    |  6.50:1 | 4.5:1 | Pass   |
| UI    | accent border on accent-soft                  | #3046d4    | #e2e6fb    |  5.74:1 |   3:1 | Pass   |
| Text  | success on pane                               | #17683a    | #ffffff    |  6.82:1 | 4.5:1 | Pass   |
| Text  | success on success-soft (badge)               | #17683a    | #d8ede0    |  5.56:1 | 4.5:1 | Pass   |
| Text  | text on success-soft (notice body)            | #101216    | #d8ede0    | 15.27:1 | 4.5:1 | Pass   |
| Text  | text-secondary on success-soft                | #343944    | #d8ede0    |  9.43:1 | 4.5:1 | Pass   |
| UI    | success border on pane                        | #17683a    | #ffffff    |  6.82:1 |   3:1 | Pass   |
| Text  | warning on pane                               | #7c4f00    | #ffffff    |  7.06:1 | 4.5:1 | Pass   |
| Text  | warning on warning-soft (badge)               | #7c4f00    | #f4e5c4    |  5.66:1 | 4.5:1 | Pass   |
| Text  | text on warning-soft (notice body)            | #101216    | #f4e5c4    | 15.04:1 | 4.5:1 | Pass   |
| Text  | text-secondary on warning-soft                | #343944    | #f4e5c4    |  9.29:1 | 4.5:1 | Pass   |
| UI    | warning border on pane                        | #7c4f00    | #ffffff    |  7.06:1 |   3:1 | Pass   |
| Text  | danger on pane                                | #b0241c    | #ffffff    |  6.74:1 | 4.5:1 | Pass   |
| Text  | danger on danger-soft (badge)                 | #b0241c    | #f8dad7    |  5.14:1 | 4.5:1 | Pass   |
| Text  | text on danger-soft (notice body)             | #101216    | #f8dad7    | 14.28:1 | 4.5:1 | Pass   |
| Text  | text-secondary on danger-soft                 | #343944    | #f8dad7    |  8.82:1 | 4.5:1 | Pass   |
| UI    | danger border on pane                         | #b0241c    | #ffffff    |  6.74:1 |   3:1 | Pass   |
| Text  | info on pane                                  | #1d5aa6    | #ffffff    |  6.85:1 | 4.5:1 | Pass   |
| Text  | info on info-soft (badge)                     | #1d5aa6    | #d9e6f6    |  5.42:1 | 4.5:1 | Pass   |
| Text  | text on info-soft (notice body)               | #101216    | #d9e6f6    | 14.82:1 | 4.5:1 | Pass   |
| Text  | text-secondary on info-soft                   | #343944    | #d9e6f6    |  9.15:1 | 4.5:1 | Pass   |
| UI    | info border on pane                           | #1d5aa6    | #ffffff    |  6.85:1 |   3:1 | Pass   |
| Text  | selection-text on selection                   | #101216    | #c9d1fa    | 12.48:1 | 4.5:1 | Pass   |
| Text  | disabled-text on disabled-bg                  | #626977    | #e6e8ed    |  4.50:1 |   3:1 | Pass   |
| Text  | text on code-bg                               | #101216    | #eceef3    | 16.15:1 | 4.5:1 | Pass   |
| Text  | syntax keyword on code-bg                     | #2a3fc4    | #eceef3    |  6.94:1 | 4.5:1 | Pass   |
| Text  | syntax string on code-bg                      | #8a4b00    | #eceef3    |  5.86:1 | 4.5:1 | Pass   |
| Text  | syntax number on code-bg                      | #1d5aa6    | #eceef3    |  5.90:1 | 4.5:1 | Pass   |
| Text  | syntax comment on code-bg                     | #525967    | #eceef3    |  6.06:1 | 4.5:1 | Pass   |
| Text  | syntax name on code-bg                        | #7a3fa8    | #eceef3    |  5.83:1 | 4.5:1 | Pass   |
| Text  | syntax type on code-bg                        | #17683a    | #eceef3    |  5.88:1 | 4.5:1 | Pass   |
| UI    | scrollbar thumb on pane                       | #7c8392    | #ffffff    |  3.81:1 |   3:1 | Pass   |
| UI    | scrollbar thumb hover on pane                 | #5d6575    | #ffffff    |  5.86:1 |   3:1 | Pass   |
