# UI/UX standards: look, feel and motion

- **Status:** Direction C "Console" chosen by the owner on 2026-11-09; it supersedes Direction A (Quiet Instrument, 2026-11-06). Dark first, ultramarine accent, tiled panes, IBM Plex. The tokens, themes, fonts and primitives are in [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md). These standards remain the review gate for all web UI.
- **Applies to:** every screen in `apps/web`, from the first sign-in page to the Phase 14 surfaces.
- **Not in this document:** mock-ups, final colours or a chosen typeface. None exist yet. The first UI task produces a design brief and the owner picks a direction (see "Process").

The aim is a web client that feels modern, calm, precise and clearly designed by people who care, never like a generic generated template ("AI slop"). Every rule below is checkable, and the review gate at the end turns them into pass or fail questions.

## 1. What MAYA should feel like

MAYA is a voice-first digital employee that does real work and asks before it acts. The interface has to earn trust.

- **Calm:** generous space, quiet colour, one clear focus per screen, motion that settles.
- **Precise:** exact alignment, consistent rhythm, real data shown exactly, no vague decoration.
- **Alive:** the system visibly listens, thinks, works and waits. State changes are animated so the user always knows what is happening.

Anti-goals: playful or cartoonish, neon "hacker" styling, bland corporate dashboards, and anything that looks like a template with the logo swapped.

## 2. The "AI slop" ban list

| Looks like slop                                                                            | Do this instead                                                                                                   |
| :----------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------- |
| Untouched default palette, purple-to-blue gradient hero, glowing blobs                     | A small designed palette from section 4; gradients only where they carry meaning (for example the voice orb glow) |
| Glass blur and drop shadows on every card                                                  | Elevation from a short scale; most surfaces are flat with a hairline border                                       |
| Three identical feature cards with an icon, a title and a sentence                         | Layout chosen for the content: a timeline, a split view, a table, a single strong statement                       |
| Everything centred, one column, uniform spacing                                            | A real grid, deliberate asymmetry, spacing rhythm from the scale, clear hierarchy                                 |
| System font stack only, or three font families                                             | One carefully chosen family (two at most), a defined type scale, tabular numerals for data                        |
| Emoji or sparkle icons as decoration, "magic" and "supercharge" copy                       | One consistent icon set (Lucide is already installed), plain and specific copy                                    |
| Fake testimonials, fake metrics, filler copy standing in for content, invented screenshots | Real content, or a visibly labelled "planned" or sample state. Never simulate behavior that does not exist        |
| Animation on everything, bounce on every hover, endless looping                            | Motion only where it explains something (section 6)                                                               |
| Spinners as the only loading state, blank screens, "Oops!" errors                          | Designed skeletons, empty states with a next step, errors that say what happened and what to do                   |
| Components that only look right in the happy state                                         | Every component specified for default, hover, focus, active, disabled, loading, error and empty                   |

## 3. Design system first

Nothing is styled with a raw value inside a component. Tokens come first and live in one place (Tailwind CSS v4 theme variables in `apps/web/src/app/globals.css` or a file it imports).

- **Colour roles, not colours:** background, surface, surface-raised, border, text, text-muted, accent, accent-text, and the status roles in section 4. There are exactly two themes, dark (default) and light, both defined from the start. There is no system mode: the choice is one icon toggle, stored per device, default dark.
- **Type scale:** a fixed set of sizes with matched line heights and weights (display, title, heading, body, small, caption, mono).
- **Spacing:** one scale on a 4 px base. **Radius:** two or three values, used consistently. **Elevation:** three levels at most. **Z-index:** a named list.
- **Motion tokens:** durations and easings from section 6.
- Palette: the Console tokens (near-black dark theme, ultramarine accent) in UI_DESIGN_SYSTEM.md replace the earlier navy and teal starting point.

## 4. Colour and state

- One brand accent used sparingly (as a guide, under about a tenth of any screen) plus neutrals with a slight tint. Pure black or pure white backgrounds are avoided.
- Task state colours follow Phase 14: pending grey, in progress blue, awaiting approval amber, completed green, failed red. **Colour is never the only signal**: every state also has an icon or label and a distinct shape or text.
- Contrast targets WCAG 2.2 AA in both themes: 4.5:1 for body text, 3:1 for large text and interface boundaries. Check the real token pairs, not screenshots.

## 5. Layout and responsiveness

- Design for the Phase 14 surfaces first: the ambient workspace (orb, conversation and deliverable canvas side by side), the task centre drawer and the approval centre.
- Desktop shows the split view; tablet and mobile collapse it into stacked or tabbed panes without losing any function. Test at least 360, 768, 1280 and 1920 px widths.
- Every data view designs its empty, loading (skeleton), partial, error, offline and permission-denied states. Layout never jumps when content arrives (target a cumulative layout shift of 0 on the main views).
- Density is a choice per surface: the conversation is airy, the task and approval views are denser and exact.

## 6. Motion

Motion is a language, not a layer of sparkle. It must do at least one of these jobs: show cause and effect, show a state change, keep the user oriented across a change of layout, or confirm an action. If it does none, remove it.

**Proposed motion tokens** (the design brief may tune them):

| Token         | Value                            | Use                                              |
| :------------ | :------------------------------- | :----------------------------------------------- |
| `instant`     | 90 ms                            | press and hover feedback                         |
| `fast`        | 150 ms                           | small state changes, tooltips, toggles           |
| `base`        | 220 ms                           | panels, popovers, list changes                   |
| `slow`        | 360 ms                           | drawers, view changes                            |
| `deliberate`  | 560 ms                           | first-load sequence and large layout shifts only |
| `ease-out`    | `cubic-bezier(0.16, 1, 0.3, 1)`  | things entering                                  |
| `ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | things moving or resizing                        |
| `ease-in`     | `cubic-bezier(0.4, 0, 1, 1)`     | things leaving (shorter than entering)           |

**Rules**

1. Animate `transform` and `opacity`. Do not animate layout properties (width, height, top, left, margins) on hot paths; use transforms or the browser's view transitions.
2. Motion never blocks input. A control is clickable immediately, and an interrupted animation settles cleanly.
3. `prefers-reduced-motion` is honoured everywhere. Each animated state has a static equivalent that carries the same information (label, icon, shape), so nothing depends on movement.
4. Stagger lists by a few tens of milliseconds and cap the total sequence under about 500 ms. No looping animation near body text. Ambient loops (the orb) are subtle and pause when the tab is hidden.
5. Use CSS (transitions, keyframes, view transitions) first. A script-driven animation library, canvas or WebGL is added only for what CSS cannot do (the orb, the DAG, shared-element transitions), after approval (section 12), loaded lazily so it never delays first paint.
6. Budget: interactions stay at 60 FPS on a mid-range laptop, with no long main-thread tasks caused by animation. Measure it and record the result honestly (section 11).

**The voice orb** (proposal; the Phase 14 states are the contract):

| State       | Behaviour                                                                              |
| :---------- | :------------------------------------------------------------------------------------- |
| idle        | slow breathing (a few seconds per cycle), very small amplitude                         |
| listening   | reacts to the microphone level with smoothing (fast attack, slower release)            |
| thinking    | steady rotating glow or noise, independent of volume, clearly different from listening |
| speaking    | follows the output audio envelope                                                      |
| interrupted | contracts within about 120 ms, then settles into listening                             |

Each state also shows a text label for assistive technology and for reduced motion. The orb is a visual of the real audio state and never fakes activity when nothing is happening.

**Voice scopes and the mini mic switch (owner request 2026-11-13).** The title-bar voice chip is the **master** voice: one session owned by `ConversationRuntime` (mounted above the routes), so it survives conversation switches, Tasks/Artifacts/Settings and sidebar changes, and it tracks all tasks. The composer mic is the **conversation-bound** voice: it survives leaving to Tasks and returning to the same conversation, and ends with a short notice when a different conversation (or a new one) is opened. Master wins: while it is on, the composer mic reads "Voice is on (all conversations)" and starts nothing. Next to the chip a compact icon-only switch ("Mute microphone" / "Unmute microphone", `aria-pressed`, tooltip with state, MicOff icon plus the text "Muted" on the chip) turns the user's own microphone off without ending the session or the spoken updates; the choice is remembered per device (`localStorage`, try/catch).

**Soft-native feel (owner request 2026-11-12).** The tokens live in `globals.css`; the recipes in `src/app/ui-surfaces.css`.

- **Radius:** `--maya-radius-sm/md/lg/xl/full` = 8 / 12 / 18 / 24 px / pill (chips, controls, cards, sheets). Squircle corners via `corner-shape` where supported.
- **Elevation:** `--maya-shadow-1/2/3` and `--maya-shadow-lift` are layered (hairline ring, ambient, key light); dark mode adds a top highlight.
- **Vibrancy:** `--maya-vib-*` translucent colours plus blur and saturate, used only by `.maya-vib-bar/side/pop/sheet/scrim` (title bar, status bar, sidebar, popovers, sheets). Blur sits in `ui-surfaces.css` behind `@supports`; the opaque surface is the fallback and what `prefers-reduced-transparency` and forced colours get. Contrast is checked on the translucent colour blended over the surfaces behind it. This is a deliberate, owner-approved exception to the "blur on every card" ban: cards stay flat.
- **Motion tokens (current values):** instant 90, fast 160, base 240, slow 360 ms; `--maya-ease-spring`, `--maya-ease-out-soft`, `--maya-ease-press`, `--maya-ease-overshoot` (switch thumb only); `--maya-stagger` 32 ms.
- **State recipe:** hover tints (`--maya-hover`) and lifts 1 px (`.motion-lift`), pressed tints (`--maya-pressed`) and scales to 0.98 (`.motion-press`), selected uses `--maya-accent-soft` plus an accent bar (`.maya-row`), icon nudge `.maya-nudge-icon`, row actions on hover or focus (`.maya-reveal` with `data-reveal`). Movement is removed under reduced motion; colour changes stay.
- **Entrances:** `.motion-page` (route change, fade plus 4 px) and `.motion-stagger` (first 8 children, 32 ms apart). Sheets scale from 0.97 (`.maya-sheet`), toasts slide in (`.maya-toast`).
- **Sidebar fold:** the sidebar slides out with transform and opacity while the content gutter slides by the sidebar width (`data-fold`); width and margins are never animated.
- **Icons:** `Icon` wrapper, sizes 14 / 16 / 20, stroke width from `--maya-icon-stroke` (1.75), decorative icons are `aria-hidden`.
- **New components:** `Switch`, `SegmentedControl`, `Tabs`, `Select`, `Sheet`, `Progress` (see the design-system page).

**Living empty state (conversation, owner request 2026-11-13).** The empty thread shows the startup-screen ring mark, three faint shuttle lanes with 28 tiny dots drifting along them (laps of 70 to 130 s, a few pixels of pointer parallax) and a static accent wash behind the heading. The continuous motion is one small canvas (`LivingBackdrop`), not CSS, so it needs no infinite keyframes: it draws at 30 fps, stops while the tab is hidden or the canvas is off screen, draws one static frame under `prefers-reduced-motion`, and is unmounted (loop cancelled) when a message arrives, the user types, or voice goes live. It is decoration: `aria-hidden`, pointer-transparent, behind the text, colours read from tokens (`--maya-accent`, `--maya-text-muted`). Heading, hint and chips enter with the shared `.motion-stagger`. Suggestion chips are real buttons that only fill the draft and focus the composer; they never send. Tests: `EmptyState.test.tsx`.

**Where motion is restricted:** the approval centre. The exact payload (email, diff, property change) renders at once, complete and static. Nothing moves after render, so a button cannot shift under the pointer. Decorative animation never sits on or near Approve, Reject or step-up controls.

## 7. Typography

- One family chosen for the brand (a second for code only), self-hosted. No external font service or CDN: the web app deliberately contacts nothing external today. The `system-ui` exception of 2026-11-06 is replaced by the IBM Plex decision of 2026-11-09 (below).
- Body and form text at 16 px or larger with 1.5 line height; measure of 60 to 75 characters for reading text. **Chrome text (labels, nav, meta, timestamps) is never below 13 px; mono labels are never below 12 px and are uppercase only with letter-spacing.** Sizes are rem tokens so 200 percent zoom and OS text scaling work.
- Owner-approved faces (2026-11-09): IBM Plex Sans, IBM Plex Mono and IBM Plex Sans Devanagari, self-hosted (SIL OFL 1.1, licences kept with the files), with a system fallback stack.
- Numbers in tables, costs, timings and diffs use tabular figures. Code and payloads use a monospace face with clear distinction between similar characters.

## 8. Trust surfaces

- Show the exact payload that will run, never a model's summary in its place. Truncation always has an explicit "show all".
- Irreversible or high-impact actions need deliberate friction (clear wording, a distinct confirmation, the step-up prompt for RESTRICTED actions). Approve is never styled to invite accidental clicks and is not the default focus for RESTRICTED actions.
- Voice cannot approve anything. The interface says so where it matters: on the inline approval card while voice is on, and once as a notice when voice connects. There is no permanent voice panel; the composer waveform button is the single voice toggle (start/stop, states as icon + text + aria, never colour or motion alone) with a mic mute switch beside it only while voice is on. Per-message composer modes (Deep research, brain icon) are icon buttons with `aria-pressed`, a visible on-state and a dismissible chip, and reset after send.
- The question card (`AskUserCard`, MAYA asking a choice or a missing detail) is never an approval and never looks like one: accent-soft tint and a question icon (approvals are warning-toned), the words "Not an approval", plain-text options (model text is never Markdown or a link), numbered options with keys 1 to 9, Up/Down, Enter to submit and Esc to skip, visible Submit and Skip buttons, a polite live announcement, and no focus taken from a composer that has text. Its answer is only a chat message; it can approve nothing.
- The schedule draft card (`ScheduleDraftCard`) and the "Scheduled updates" notice follow the same rule as the question card: info-toned (calendar icon), never warning-toned like approvals, no animation at all. Creating a schedule is click-only: the card says "Not created until you click", has no key shortcut and no voice path, and announces its result in a polite live region. Reading an update aloud starts the voice listen-only: the microphone is never requested, the notice says "Your mic is muted" with an Unmute button, and nothing starts by itself when the browser blocks audio or microphone permission is denied. The wake-PC run mode is disabled with a plain reason outside the desktop app and asks first.
- Errors are specific, calm and actionable. They never expose stack traces, secrets or another user's data.

## 9. Accessibility floor

### Work log under a reply

The step list (`ProgressSteps`, shared with the Task card) has a header that names the current phase in plain words (mapped in `lib/work-log.ts`, fallback "Working on it"), rows of one 14 px outline icon, a label and a muted "· detail", and while live one accent status line below a thin divider ("Searching the web for 2s …"; static text, only the phase is a polite live region, never the seconds). Finished, it folds to "Worked for X · N steps". Housekeeping steps and raw internal ids are hidden unless they failed; times under one second are never shown; figures that are not real are left out. Row motion is one short opacity and transform settle with a static equivalent under reduced motion; nothing animates near approval or question cards.

WCAG 2.2 AA. Full keyboard operation with a visible focus indicator on every interactive element; logical focus order and focus management for drawers and modals; targets at least 44 px on touch layouts; semantic landmarks and headings; live regions for task progress and the orb state; text alternatives for non-text content; no information by colour or motion alone; zoom to 200 percent without loss.

## 10. Content and microcopy

Plain, specific and short. Say what happened and what the user can do. No hype words, no exclamation marks in system messages, no jokes in error states. Sentence case for interface text. Anything not built yet is labelled "planned", as the foundation page does.

## 11. Process and evidence

The UI/UX role (`governance/roles/ui-ux-designer.md`) leads steps 1 to 4 and reviews 5 to 6; the frontend role implements.

1. **Brief:** goals, users, surfaces, states, constraints, and the accepted mood words.
2. **Directions:** two distinct static directions (tokens plus one key screen each). The owner chooses one. No full build starts before this.
3. **Tokens and system:** colour, type, spacing, radius, elevation and motion tokens in both themes; a component inventory with the state matrix from section 2.
4. **Motion spec:** which transitions exist, why each exists, and its reduced-motion equivalent.
5. **Build** with tests (component tests now; browser end-to-end tests belong to Phase 15).
6. **Verify and report:** screenshots at the four widths in both themes, a keyboard-only walkthrough, a contrast check of the real tokens, a reduced-motion pass, and a frame-rate and layout-shift measurement for animated views. **Anything not run is reported as not run.** A described screenshot is not a browser check.

## 12. Dependencies, assets and approvals

Governance still applies. Needing approval (Level 3): any new package (motion library, component primitives, table, PDF or graph viewers, font packages, accessibility test tooling), third-party icons, images or fonts (licence recorded), any external service, analytics or CDN. Phase 14 already names React Flow or SVG for the graph, TanStack Table and `pdfjs-dist`; those still need the dependency workflow. Ship nothing that sends data off the machine, and keep a content security policy compatible with the result.

Performance budgets are **proposals**, to be confirmed by the owner with the brief: a fast first load on a typical laptop connection, animation libraries code-split off the critical path, and a stated JavaScript budget per route recorded in the frontend architecture document.

## Context menus

One component, `components/ui/ContextMenu.tsx` (`useContextMenu`, `MenuSurface`, `MenuButton`). Every right-click menu in the app is built on it; do not hand-roll another.

- **Ways in (all open the same menu):** right-click, the Menu key or Shift+F10 on the focused element, a 450 ms long press on touch, and a visible "..." button (`aria-haspopup="menu"`, shown on hover and focus, always shown on touch). Right-click is never the only way.
- **Native menu:** suppressed only on surfaces that have a menu, and only when the menu has entries. Text inputs and textareas keep the browser menu (Cut, Copy, Paste); the composer opts in (`allowEditable`) because it adds Attach and Clear draft. Selected text in a reply gets the selection menu.
- **Roles and keys:** `role="menu"`, `menuitem`, `menuitemcheckbox`, `separator`, labelled `group`. Focus moves to the first item; arrows, Home, End and typeahead move; Enter or Space acts; ArrowRight opens and ArrowLeft closes the one allowed submenu level; Esc, Tab, a press outside, scroll, resize, window blur and a route change close it; focus returns to the trigger.
- **Placement and look:** portal to `document.body`, flips and clamps to the viewport, one open at a time, vibrancy surface (`maya-popover maya-vib-pop`), tokens only, lucide icons at 14 px, scale and fade within `--maya-dur-instant`, no animation under reduced motion.
- **Content rules:** items that do not apply are disabled with a plain reason, or hidden when the server does not offer them (404 or 501). Destructive items use the danger style and open `ConfirmDeleteDialog`; the menu never deletes by itself. Menus run no network call until an item is chosen, and are built from the data of the surface that opened them.
- **Never in a menu:** Approve. Approval cards offer Copy exact payload and Reject only; approving stays on the button.
- **Actions that send chat:** a menu item sends a short natural-language chat message (with `source_message_id` when it acts on a reply). It never runs a tool itself.

## 13. Review gate (pass or fail)

A UI change is not done until a reviewer can answer yes to all of these:

1. Does every colour, size, space, radius, shadow and duration come from a token?
2. Are both themes complete and contrast-checked?
3. Does each component have all states designed and visible?
4. Does every animation do a job from section 6, and does it have a static reduced-motion equivalent?
5. Is anything animated on layout properties on a hot path?
6. Is the approval payload complete, static and exact?
7. Is it keyboard-operable with visible focus, and correct for assistive technology?
8. Is there any fake data, placeholder text or simulated behavior not labelled "planned"?
9. Does anything contact an external service, font host or CDN?
10. Do the screens avoid every pattern in the ban list?
11. Are the verification steps in section 11 reported, with anything not run listed as not run?
12. Were new dependencies and assets approved and recorded?
