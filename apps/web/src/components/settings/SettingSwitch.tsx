"use client";

import React from "react";

import { Switch } from "../ui/Switch";

export interface SettingSwitchProps {
  readonly checked: boolean;
  readonly disabled?: boolean;
  /** Id of the element that names the switch. */
  readonly labelledBy: string;
  /** Id of the element that describes it. */
  readonly describedBy?: string;
  readonly onToggle: () => void;
}

/**
 * An on/off switch (role="switch") for settings rows: the shared iOS-style Switch. The state is
 * never colour alone: the thumb moves and the caller shows the words On or Off next to it. Hit
 * area is 44 px high on coarse pointers.
 */
export function SettingSwitch({
  checked,
  disabled = false,
  labelledBy,
  describedBy,
  onToggle,
}: SettingSwitchProps): React.JSX.Element {
  return (
    <Switch
      checked={checked}
      disabled={disabled}
      labelledBy={labelledBy}
      describedBy={describedBy}
      onCheckedChange={onToggle}
    />
  );
}
