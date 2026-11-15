# AGENTS.md: web client (`apps/web`)

Local rules for everything under `apps/web`. They add to the repository-wide [AGENTS.md](../../AGENTS.md) and never weaken it.

## Before creating or restyling any UI

1. Read [docs/architecture/UI_UX_STANDARDS.md](../../docs/architecture/UI_UX_STANDARDS.md) and follow it.
2. For design, motion and look-and-feel work, act as the UI/UX Designer role (`governance/roles/ui-ux-designer.md`). For implementation, act as the Frontend Engineer role (`governance/roles/frontend-engineer.md`). A role grants no extra permission.

## Non-negotiables

- **The result must look designed, not generated.** No default or template styling, untouched default palettes, generic gradient heroes, identical card grids, emoji as icons, or hype copy. The ban list in the standards document is a hard requirement.
- **Tokens, not raw values.** Colours, sizes, spacing, radii, shadows and durations come from the design tokens, in both light and dark themes.
- **Motion has a job** (explain cause, state or place), uses transform and opacity, never blocks input, and always has a static equivalent under `prefers-reduced-motion`.
- **Approval screens are exact and still:** the real payload, complete and static, with no animation near the decision controls.
- **Accessibility floor:** WCAG 2.2 AA, full keyboard use, visible focus, labelled states, no information by colour or motion alone.
- **Do not simulate.** No fake data, chat, progress or metrics. Anything not built is labelled planned.
- **Approval first (Level 3):** new packages (animation, components, fonts), third-party assets, external services, CDNs and analytics. The app contacts nothing external today.

## Verify and report

```powershell
npm --prefix apps\web run lint
npm --prefix apps\web run typecheck
npm --prefix apps\web test
npm --prefix apps\web run build
```

Report visual checks honestly: screenshots at four widths in both themes, a keyboard walkthrough, contrast and reduced-motion checks, and frame-rate and layout-shift measurements. List anything you did not run as not run.
