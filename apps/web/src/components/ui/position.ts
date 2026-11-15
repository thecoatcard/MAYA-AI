/** Placement maths for Popover and Tooltip. Pure, so it is unit-tested without a browser. */

export type Side = "top" | "bottom" | "left" | "right";

export interface Rect {
  readonly top: number;
  readonly left: number;
  readonly width: number;
  readonly height: number;
}

export interface Size {
  readonly width: number;
  readonly height: number;
}

export interface PlacementInput {
  readonly anchor: Rect;
  readonly floating: Size;
  readonly viewport: Size;
  /** Preferred side. It flips to the opposite side when there is not enough room. */
  readonly side?: Side;
  /** Distance between anchor and floating element. */
  readonly gap?: number;
  /** Minimum distance to every viewport edge. */
  readonly margin?: number;
}

export interface Placement {
  readonly top: number;
  readonly left: number;
  readonly side: Side;
}

const OPPOSITE: Record<Side, Side> = {
  top: "bottom",
  bottom: "top",
  left: "right",
  right: "left",
};

function clamp(value: number, min: number, max: number): number {
  // If the floating element is larger than the room, pin it to the start edge.
  return Math.max(min, Math.min(value, Math.max(min, max)));
}

/**
 * Flip to the opposite side when the preferred one does not fit and the other has more room, then
 * centre on the anchor along the other axis and shift so the element stays inside the viewport.
 */
export function computePlacement({
  anchor,
  floating,
  viewport,
  side = "bottom",
  gap = 8,
  margin = 8,
}: PlacementInput): Placement {
  const room: Record<Side, number> = {
    top: anchor.top - margin,
    bottom: viewport.height - (anchor.top + anchor.height) - margin,
    left: anchor.left - margin,
    right: viewport.width - (anchor.left + anchor.width) - margin,
  };
  const need =
    side === "top" || side === "bottom" ? floating.height : floating.width;
  const chosen =
    room[side] >= need + gap || room[OPPOSITE[side]] <= room[side]
      ? side
      : OPPOSITE[side];

  let top: number;
  let left: number;
  if (chosen === "top" || chosen === "bottom") {
    top =
      chosen === "bottom"
        ? anchor.top + anchor.height + gap
        : anchor.top - floating.height - gap;
    left = anchor.left + anchor.width / 2 - floating.width / 2;
  } else {
    left =
      chosen === "right"
        ? anchor.left + anchor.width + gap
        : anchor.left - floating.width - gap;
    top = anchor.top + anchor.height / 2 - floating.height / 2;
  }

  return {
    side: chosen,
    top: clamp(top, margin, viewport.height - floating.height - margin),
    left: clamp(left, margin, viewport.width - floating.width - margin),
  };
}
