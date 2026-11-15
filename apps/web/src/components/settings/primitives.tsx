"use client";

import React from "react";

import { Badge, cx, type Tone } from "../ui";
import { SettingSwitch } from "./SettingSwitch";

/**
 * Shared building blocks of every Settings section, so spacing, grid and wording stay identical:
 * a section header (title plus one line), a card that groups related settings, a row with the
 * label and helper text on the left and the control on the right, and a facts list.
 *
 * Saving rule used by the whole page: toggles and choices save at once and show "Saving..." then
 * "Saved" next to the control; anything that holds a credential has an explicit Save button.
 */

export function SectionHeader({
  id,
  title,
  description,
  headingRef,
}: {
  readonly id: string;
  readonly title: string;
  readonly description: string;
  readonly headingRef?: React.Ref<HTMLHeadingElement>;
}): React.JSX.Element {
  return (
    <header className="grid min-w-0 gap-1">
      <h2
        id={id}
        ref={headingRef}
        tabIndex={-1}
        className="m-0 text-maya-title font-semibold text-maya-text"
      >
        {title}
      </h2>
      <p className="m-0 text-maya-small text-maya-secondary">{description}</p>
    </header>
  );
}

export function SettingsCard({
  title,
  description,
  children,
  tone = "default",
  as: Tag = "div",
  ...rest
}: {
  readonly title?: string;
  readonly description?: string;
  readonly children: React.ReactNode;
  readonly tone?: "default" | "danger";
  readonly as?: "div" | "section";
} & React.HTMLAttributes<HTMLElement>): React.JSX.Element {
  return (
    <Tag
      {...rest}
      className={cx(
        "min-w-0 rounded-maya-lg border bg-maya-pane",
        tone === "danger" ? "border-maya-danger" : "border-maya-border",
      )}
    >
      {title !== undefined && (
        <div className="min-w-0 border-b border-maya-border px-4 py-3">
          <h3 className="m-0 text-maya-heading font-semibold text-maya-text">
            {title}
          </h3>
          {description !== undefined && (
            <p className="m-0 mt-1 text-maya-small text-maya-secondary">
              {description}
            </p>
          )}
        </div>
      )}
      <div className="min-w-0 divide-y divide-maya-border">{children}</div>
    </Tag>
  );
}

/** One setting: label and helper on the left, the control aligned on the right. */
export function SettingRow({
  id,
  title,
  help,
  note,
  children,
}: {
  readonly id: string;
  readonly title: string;
  readonly help: string;
  readonly note?: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="grid min-w-0 gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-6">
      <div className="min-w-0">
        <p id={`${id}-label`} className="m-0 text-maya-body font-medium">
          {title}
        </p>
        <p
          id={`${id}-help`}
          className="m-0 mt-1 text-maya-small text-maya-secondary"
        >
          {help}
        </p>
        {note !== undefined && (
          <p className="m-0 mt-1 text-maya-small text-maya-muted">{note}</p>
        )}
      </div>
      <div className="flex min-w-0 flex-col items-start gap-0.5 sm:items-end">
        {children}
      </div>
    </div>
  );
}

export interface SwitchRowProps {
  readonly id: string;
  readonly title: string;
  readonly help: string;
  readonly checked: boolean;
  readonly disabled: boolean;
  readonly stateText: string;
  readonly note?: string;
  readonly onToggle: () => void;
}

/** A switch row that saves at once; `stateText` reads On, Off, Saving... or Saved. */
export function SwitchRow({
  id,
  title,
  help,
  checked,
  disabled,
  stateText,
  note,
  onToggle,
}: SwitchRowProps): React.JSX.Element {
  return (
    <SettingRow id={id} title={title} help={help} note={note}>
      <SettingSwitch
        checked={checked}
        disabled={disabled}
        labelledBy={`${id}-label`}
        describedBy={`${id}-help`}
        onToggle={onToggle}
      />
      <span aria-live="polite" className="text-maya-caption text-maya-muted">
        {stateText}
      </span>
    </SettingRow>
  );
}

export function Fact({
  label,
  children,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="grid min-w-0 gap-1 px-4 py-3 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-maya-caption text-maya-muted">{label}</dt>
      <dd className="m-0 min-w-0 text-maya-small text-maya-text">{children}</dd>
    </div>
  );
}

export function StatusBadge({
  tone,
  children,
}: {
  readonly tone: Tone;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return <Badge tone={tone}>{children}</Badge>;
}
