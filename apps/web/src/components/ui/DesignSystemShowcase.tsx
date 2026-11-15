"use client";

import { LayoutGrid, List, Plus, Search } from "lucide-react";
import React, { useRef, useState } from "react";

import {
  Badge,
  Button,
  CodeBlockShell,
  EmptyState,
  ErrorState,
  IconButton,
  Kbd,
  Notice,
  Pane,
  Popover,
  Row,
  ScrollArea,
  Skeleton,
  Stack,
  StatusPill,
  TableScroll,
  ThemeToggle,
  ToastRegion,
  Tooltip,
  Truncate,
  WrapText,
  useToasts,
  type TaskStatus,
  Progress,
  SegmentedControl,
  Select,
  Sheet,
  Switch,
  Tabs,
} from "./index";

const SURFACES = ["bg", "pane", "raised", "overlay", "hover", "code"] as const;
const TEXTS = ["text", "secondary", "muted"] as const;
const ACCENTS = [
  "accent",
  "accent-hover",
  "accent-active",
  "accent-soft",
] as const;
const STATUSES = ["success", "warning", "danger", "info"] as const;
const SPACES = [1, 2, 3, 4, 6, 8, 12] as const;
const LAYERS = [
  "base",
  "sticky",
  "nav",
  "panel",
  "dropdown",
  "modal",
  "toast",
  "tooltip",
] as const;
const BARS = [0.5, 0.8, 0.6, 1, 0.7, 0.9, 0.55, 0.75, 0.45] as const;
const ORB_STATES = ["idle", "listening", "thinking", "speaking"] as const;
const TASKS: readonly TaskStatus[] = [
  "pending",
  "running",
  "awaiting_approval",
  "completed",
  "failed",
];

const LONG_URL =
  "https://example.invalid/a/very/long/path/with-no-spaces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?token=redacted";

function Section({
  id,
  title,
  children,
}: {
  readonly id: string;
  readonly title: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section aria-labelledby={id} className="grid min-w-0 gap-3">
      <h2 id={id} className="maya-label m-0 border-b border-maya-border pb-1">
        {title}
      </h2>
      {children}
    </section>
  );
}

/**
 * QA page for the design system: every token and primitive in every state, so the audit harness
 * can screenshot it in both themes. Sample content only; nothing here is product data.
 */
export function DesignSystemShowcase(): React.JSX.Element {
  const anchor = useRef<HTMLButtonElement>(null);
  const [popover, setPopover] = useState(false);
  const [switchOn, setSwitchOn] = useState(true);
  const [view, setView] = useState<"list" | "grid">("list");
  const [tab, setTab] = useState<"one" | "two">("one");
  const [sheet, setSheet] = useState(false);
  const { toasts, push, dismiss } = useToasts();

  return (
    <main className="mx-auto grid min-w-0 max-w-5xl gap-8 bg-maya-bg p-4 text-maya-text">
      <header className="flex min-w-0 items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="m-0 text-maya-title font-semibold">
            MAYA design system
          </h1>
          <p className="m-0 text-maya-small text-maya-secondary">
            QA showcase. Sample content only. Not linked from the product.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <Section id="ds-colour" title="Colour tokens">
        <div
          className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6"
          data-testid="swatches"
        >
          {[...SURFACES, ...ACCENTS, ...STATUSES.map((s) => `${s}-soft`)].map(
            (name) => (
              <div
                key={name}
                className="min-w-0 border border-maya-border-strong p-2"
              >
                <div
                  className="h-8 border border-maya-border"
                  style={{
                    background: `var(--maya-${name === "code" ? "code-bg" : name})`,
                  }}
                />
                <code className="block text-maya-label [overflow-wrap:anywhere]">
                  --maya-{name}
                </code>
              </div>
            ),
          )}
        </div>
        <Row gap={4} wrap>
          {TEXTS.map((t) => (
            <span
              key={t}
              className={
                t === "text"
                  ? "text-maya-text"
                  : t === "secondary"
                    ? "text-maya-secondary"
                    : "text-maya-muted"
              }
            >
              text-{t}
            </span>
          ))}
          {STATUSES.map((s) => (
            <span key={s} style={{ color: `var(--maya-${s})` }}>
              {s}
            </span>
          ))}
          <span className="bg-maya-selection px-1">selection</span>
        </Row>
      </Section>

      <Section id="ds-type" title="Type scale, spacing, radii, layers">
        <Stack gap={1}>
          <p className="m-0 text-maya-display font-semibold">Display 34</p>
          <p className="m-0 text-maya-title font-semibold">Title 24</p>
          <p className="m-0 text-maya-heading font-semibold">Heading 17</p>
          <p className="m-0 text-maya-body">
            Body 16. Hindi sample: नमस्ते, यह एक परीक्षण वाक्य है।
          </p>
          <p className="m-0 text-maya-small">Small 14</p>
          <p className="m-0 text-maya-caption">
            Caption 13 (smallest chrome text)
          </p>
          <p className="maya-label m-0">Mono label 12 uppercase tracked</p>
          <p className="maya-tabular m-0 font-mono text-maya-mono">
            Mono 13 tabular 0123456789
          </p>
        </Stack>
        <Row gap={3} wrap>
          {SPACES.map((n) => (
            <div key={n} className="text-center text-maya-label">
              <div
                className="bg-maya-accent"
                style={{ width: `var(--maya-space-${n})`, height: "1rem" }}
              />
              {n}
            </div>
          ))}
          {(["sm", "md", "lg"] as const).map((r) => (
            <div
              key={r}
              className="size-10 border border-maya-border-strong bg-maya-raised"
              style={{ borderRadius: `var(--maya-radius-${r})` }}
            />
          ))}
          {([1, 2, 3] as const).map((n) => (
            <div
              key={n}
              className="size-10 bg-maya-pane"
              style={{ boxShadow: `var(--maya-shadow-${n})` }}
            />
          ))}
        </Row>
        <Row gap={2} wrap>
          {LAYERS.map((l) => (
            <Badge key={l} tone="neutral">
              z-{l}
            </Badge>
          ))}
        </Row>
      </Section>

      <Section id="ds-buttons" title="Buttons">
        <Row gap={2} wrap>
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="primary" disabled>
            Disabled
          </Button>
          <Button variant="primary" loading>
            Loading
          </Button>
          <Button size="sm" leftIcon={<Plus size={14} />}>
            Small
          </Button>
          <Button size="lg">Large</Button>
          <IconButton label="Search" icon={<Search size={16} />} />
          <Tooltip content="Switches the theme">
            <IconButton label="Tooltip anchor" icon={<Plus size={16} />} />
          </Tooltip>
          <Button ref={anchor} onClick={() => setPopover((o) => !o)}>
            Popover
          </Button>
          <Popover
            open={popover}
            onOpenChange={setPopover}
            anchorRef={anchor}
            label="Sample popover"
          >
            Flips and shifts to stay inside the window.
          </Popover>
          <Button
            onClick={() =>
              push({ message: "Saved a sample toast.", tone: "success" })
            }
          >
            Toast
          </Button>
          <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd>
        </Row>
      </Section>

      <Section id="ds-badges" title="Badges and status pills (icon plus text)">
        <Row gap={2} wrap>
          {(
            [
              "neutral",
              "accent",
              "success",
              "warning",
              "danger",
              "info",
            ] as const
          ).map((t) => (
            <Badge key={t} tone={t}>
              {t}
            </Badge>
          ))}
          {TASKS.map((s) => (
            <StatusPill key={s} status={s} />
          ))}
        </Row>
      </Section>

      <Section id="ds-states" title="Loading, empty, error, notices">
        <div className="grid gap-3 md:grid-cols-2">
          <div
            aria-busy="true"
            className="grid content-start gap-2 border border-maya-border p-3"
          >
            <p className="m-0 text-maya-caption text-maya-muted">Loading</p>
            <Skeleton lines={3} />
          </div>
          <EmptyState
            title="No tasks yet"
            description="Ask MAYA to start one."
            action={<Button size="sm">New task</Button>}
          />
          <ErrorState
            title="Could not load tasks"
            message="The backend did not answer. Check it is running, then retry."
            onRetry={() => undefined}
          />
          <Stack>
            <Notice tone="info" title="Info">
              Plain information.
            </Notice>
            <Notice tone="warning" title="Warning">
              Needs attention.
            </Notice>
            <Notice tone="danger" title="Error">
              Something failed.
            </Notice>
            <Notice tone="success" title="Done">
              It worked.
            </Notice>
          </Stack>
        </div>
      </Section>

      <Section
        id="ds-overflow"
        title="Long content (nothing may widen the page)"
      >
        <div className="grid gap-3 md:grid-cols-2">
          <div className="min-w-0 border border-maya-border p-3">
            <Truncate>
              {"A single line title that is far too long to fit in its box and must end with an ellipsis " +
                LONG_URL}
            </Truncate>
            <Truncate lines={2} className="mt-2">
              {"Two line clamp. " +
                "Words that keep going and going. ".repeat(12)}
            </Truncate>
            <WrapText as="p" className="mt-2 mb-0">
              {LONG_URL}
            </WrapText>
          </div>
          <CodeBlockShell label="Sample payload">
            {JSON.stringify(
              {
                url: LONG_URL,
                note: "scrolls inside this box, never widens the page",
              },
              null,
              2,
            )}
          </CodeBlockShell>
          <TableScroll label="Sample table">
            <table className="w-max min-w-full text-left text-maya-small">
              <thead>
                <tr>
                  <th className="p-2">Name</th>
                  <th className="p-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-maya-border">
                  <td className="p-2">row</td>
                  <td className="maya-tabular p-2 whitespace-nowrap">
                    {LONG_URL}
                  </td>
                </tr>
              </tbody>
            </table>
          </TableScroll>
          <div className="h-40">
            <Pane
              header={<Truncate>Pane header</Truncate>}
              footer="Pane footer (never scrolls)"
              bodyLabel="Pane body"
            >
              <Stack className="p-3">
                {Array.from({ length: 20 }, (_, i) => (
                  <p key={i} className="m-0">
                    Line {i + 1} of the only scrolling region.
                  </p>
                ))}
              </Stack>
            </Pane>
          </div>
          <ScrollArea
            label="Scroll area"
            className="h-24 border border-maya-border p-2"
          >
            {Array.from({ length: 12 }, (_, i) => (
              <p key={i} className="m-0">
                Scroll row {i + 1}
              </p>
            ))}
          </ScrollArea>
        </div>
      </Section>

      <Section
        id="ds-surfaces"
        title="Switch, segmented control, sheet, progress"
      >
        <Row gap={6} wrap align="center">
          <Switch
            checked={switchOn}
            onCheckedChange={setSwitchOn}
            label="Sample switch"
            showState
          />
          <Switch
            checked={false}
            onCheckedChange={() => undefined}
            label="Off switch"
          />
          <Switch
            checked
            onCheckedChange={() => undefined}
            label="Disabled on"
            disabled
          />
          <SegmentedControl
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: "list", label: "List", icon: List },
              { value: "grid", label: "Grid", icon: LayoutGrid },
            ]}
          />
          <Tabs
            label="Sample tabs"
            idPrefix="ds-tabs"
            value={tab}
            onChange={setTab}
            options={[
              { value: "one", label: "Overview" },
              { value: "two", label: "Details" },
            ]}
          />
          <Select label="Sample select" defaultValue="a">
            <option value="a">Option A</option>
            <option value="b">Option B</option>
          </Select>
        </Row>
        <div
          role="tabpanel"
          id={`ds-tabs-panel-${tab}`}
          aria-labelledby={`ds-tabs-tab-${tab}`}
          className="text-maya-small text-maya-secondary"
        >
          Panel for {tab}.
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Progress label="Upload" value={60} showLabel />
          <Progress label="Working, amount unknown" showLabel />
        </div>
        <div className="grid gap-2">
          <div
            className="maya-row maya-reveal flex items-center justify-between px-3 py-2 text-maya-small"
            data-selected="true"
          >
            <span>Selected row (hover shows actions)</span>
            <span data-reveal>
              <Kbd>Del</Kbd>
            </span>
          </div>
          <div className="maya-row maya-reveal flex items-center justify-between px-3 py-2 text-maya-small">
            <span>Plain row</span>
            <span data-reveal>
              <Kbd>Del</Kbd>
            </span>
          </div>
        </div>
        <Row gap={3} wrap>
          <Button onClick={() => setSheet(true)}>Open sheet</Button>
          <span className="maya-vib-pop maya-popover relative px-3 py-2 text-maya-small">
            Vibrancy popover surface
          </span>
        </Row>
        <Sheet
          open={sheet}
          onClose={() => setSheet(false)}
          title="Sample sheet"
          description="Scales and fades in from 0.97 over a blurred scrim."
          footer={
            <Button variant="primary" onClick={() => setSheet(false)}>
              Done
            </Button>
          }
        >
          <p className="m-0 text-maya-small">Sheet body.</p>
        </Sheet>
      </Section>

      <Section id="ds-motion" title="Motion kit (static under reduced motion)">
        <Row gap={6} wrap align="start">
          {ORB_STATES.map((state) => (
            <div key={state} className="grid justify-items-center gap-2">
              <div
                className="motion-orb flex h-10 items-end gap-0.5"
                data-state={state}
                role="img"
                aria-label={`Voice ${state}`}
              >
                {BARS.map((h, n) => (
                  <i
                    key={n}
                    className="block h-full w-1 origin-bottom bg-maya-accent"
                    style={{ "--h": h, "--n": n } as React.CSSProperties}
                  />
                ))}
              </div>
              <span className="maya-label">{state}</span>
            </div>
          ))}
        </Row>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="motion-enter border border-maya-border p-3 text-maya-small">
            motion-enter
          </div>
          <div className="motion-message border border-maya-border p-3 text-maya-small">
            motion-message
          </div>
          <div className="grid gap-1">
            <div className="h-1.5 overflow-hidden bg-maya-raised">
              <div
                className="motion-progress h-full bg-maya-accent"
                style={{ "--maya-progress": 0.6 } as React.CSSProperties}
              />
            </div>
            <span className="text-maya-caption text-maya-secondary">
              Step 3 of 5 (60%)
            </span>
            <div className="h-1.5 overflow-hidden bg-maya-raised">
              <div className="motion-progress-indeterminate h-full bg-maya-accent" />
            </div>
            <span className="text-maya-caption text-maya-secondary">
              Working, amount unknown
            </span>
          </div>
        </div>
        <div className="motion-none border border-maya-warning bg-maya-warning-soft p-3 text-maya-small">
          motion-none guard: approval controls live in a box like this and never
          move.
        </div>
      </Section>

      <ToastRegion toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
