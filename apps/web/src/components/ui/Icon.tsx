import type { LucideIcon } from "lucide-react";
import React from "react";

/** The three icon sizes of the design system, in px: inline text, controls, empty states. */
export type IconSize = 14 | 16 | 20;

/** One stroke width for every icon, so the set reads as a family. */
export const ICON_STROKE = 1.75;

export interface IconProps {
  readonly icon: LucideIcon;
  readonly size?: IconSize;
  readonly className?: string;
  /**
   * Accessible name for an icon that stands alone. Leave it out for decorative icons (next to a
   * text label or inside a labelled button): they are then hidden from assistive technology.
   */
  readonly label?: string;
}

/**
 * Shared SVG icon wrapper (lucide-react). Fixed sizes, one stroke width, aria-hidden unless a
 * label is given. Use it instead of rendering lucide icons with ad hoc sizes.
 */
export function Icon({
  icon: Glyph,
  size = 16,
  className,
  label,
}: IconProps): React.JSX.Element {
  return label === undefined ? (
    <Glyph
      aria-hidden="true"
      size={size}
      strokeWidth={ICON_STROKE}
      className={className}
    />
  ) : (
    <Glyph
      role="img"
      aria-label={label}
      size={size}
      strokeWidth={ICON_STROKE}
      className={className}
    />
  );
}
