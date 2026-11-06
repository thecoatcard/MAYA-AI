# UI/UX standards: look, feel and motion

- **Status:** Direction A — Quiet Instrument chosen by the owner on 2026-11-06. These standards remain the review gate for all web UI.
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

| Looks like slop | Do this instead |
| :--- | :--- |
| Untouched default palette, purple-to-blue gradient hero, glowing blobs | A small designed palette from section 4; gradients only where they carry meaning (for example the voice orb glow) |
| Glass blur and drop shadows on every card | Elevation from a short scale; most surfaces are flat with a hairline border |
| Three identical feature cards with an icon, a title and a sentence | Layout chosen for the content: a timeline, a split view, a table, a single strong statement |
| Everything centred, one column, uniform spacing | A real grid, deliberate asymmetry, spacing rhythm from the scale, clear hierarchy |
| System font stack only, or three font families | One carefully chosen family (two at most), a defined type scale, tabular numerals for data |
| Emoji or sparkle icons as decoration, "magic" and "supercharge" copy | One consistent icon set (Lucide is already installed), plain and specific copy |
| Fake testimonials, fake metrics, filler copy standing in for content, invented screenshots | Real content, or a visibly labelled "planned" or sample state. Never simulate behavior that does not exist |
| Animation on everything, bounce on every hover, endless looping | Motion only where it explains something (section 6) |
| Spinners as the only loading state, blank screens, "Oops!" errors | Designed skeletons, empty states with a next step, errors that say what happened and what to do |
| Components that only look right in the happy state | Every component specified for default, hover, focus, active, disabled, loading, error and empty |

## 3. Design system first

Nothing is styled with a raw value inside a component. Tokens come first and live in one place (Tailwind CSS v4 theme variables in `apps/web/src/app/globals.css` or a file it imports).

- **Colour roles, not colours:** background, surface, surface-raised, border, text, text-muted, accent, accent-text, and the status roles in section 4. Light and dark themes are both defined from the start, and the theme follows the system with a manual switch.
- **Type scale:** a fixed set of sizes with matched line heights and weights (display, title, heading, body, small, caption, mono).
- **Spacing:** one scale on a 4 px base. **Radius:** two or three values, used consistently. **Elevation:** three levels at most. **Z-index:** a named list.
- **Motion tokens:** durations and easings from section 6.
- Starting point only, not a mandate: the foundation page uses a deep navy background and a teal accent with a visible focus ring. The design brief may keep, refine or replace them.

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

| Token | Value | Use |
| :--- | :--- | :--- |
| `instant` | 90 ms | press and hover feedback |
| `fast` | 150 ms | small state changes, tooltips, toggles |
| `base` | 220 ms | panels, popovers, list changes |
| `slow` | 360 ms | drawers, view changes |
| `deliberate` | 560 ms | first-load sequence and large layout shifts only |
| `ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | things entering |
| `ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | things moving or resizing |
| `ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | things leaving (shorter than entering) |

**Rules**

1. Animate `transform` and `opacity`. Do not animate layout properties (width, height, top, left, margins) on hot paths; use transforms or the browser's view transitions.
2. Motion never blocks input. A control is clickable immediately, and an interrupted animation settles cleanly.
3. `prefers-reduced-motion` is honoured everywhere. Each animated state has a static equivalent that carries the same information (label, icon, shape), so nothing depends on movement.
4. Stagger lists by a few tens of milliseconds and cap the total sequence under about 500 ms. No looping animation near body text. Ambient loops (the orb) are subtle and pause when the tab is hidden.
5. Use CSS (transitions, keyframes, view transitions) first. A script-driven animation library, canvas or WebGL is added only for what CSS cannot do (the orb, the DAG, shared-element transitions), after approval (section 12), loaded lazily so it never delays first paint.
6. Budget: interactions stay at 60 FPS on a mid-range laptop, with no long main-thread tasks caused by animation. Measure it and record the result honestly (section 11).

**The voice orb** (proposal; the Phase 14 states are the contract):

| State | Behaviour |
| :--- | :--- |
| idle | slow breathing (a few seconds per cycle), very small amplitude |
| listening | reacts to the microphone level with smoothing (fast attack, slower release) |
| thinking | steady rotating glow or noise, independent of volume, clearly different from listening |
| speaking | follows the output audio envelope |
| interrupted | contracts within about 120 ms, then settles into listening |

Each state also shows a text label for assistive technology and for reduced motion. The orb is a visual of the real audio state and never fakes activity when nothing is happening.

**Where motion is restricted:** the approval centre. The exact payload (email, diff, property change) renders at once, complete and static. Nothing moves after render, so a button cannot shift under the pointer. Decorative animation never sits on or near Approve, Reject or step-up controls.

## 7. Typography

- One family chosen for the brand (a second for code only), self-hosted. No external font service or CDN: the web app deliberately contacts nothing external today. The owner chose `system-ui` on 2026-11-06 as an explicit exception to the system-stack anti-goal.
- Body at 16 px or larger with 1.5 line height; measure of 60 to 75 characters for reading text.
- Numbers in tables, costs, timings and diffs use tabular figures. Code and payloads use a monospace face with clear distinction between similar characters.

## 8. Trust surfaces

- Show the exact payload that will run, never a model's summary in its place. Truncation always has an explicit "show all".
- Irreversible or high-impact actions need deliberate friction (clear wording, a distinct confirmation, the step-up prompt for RESTRICTED actions). Approve is never styled to invite accidental clicks and is not the default focus for RESTRICTED actions.
- Voice cannot approve anything. The interface says so where voice is active.
- Errors are specific, calm and actionable. They never expose stack traces, secrets or another user's data.

## 9. Accessibility floor

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
