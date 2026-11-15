/**
 * @typedef {{ x: number, y: number, width: number, height: number }} Rectangle
 * @typedef {{ bounds?: Rectangle } | Rectangle} DisplayLike
 */

/**
 * Returns true if the bounds rectangle intersects at least one display.
 * @param {{ x?: number, y?: number, width?: number, height?: number } | null | undefined} bounds
 * @param {readonly DisplayLike[] | null | undefined} displays
 * @returns {boolean}
 */
export function isWithinAnyDisplay(bounds, displays) {
  if (!bounds || typeof bounds.x !== "number" || typeof bounds.y !== "number") return false;
  const width = bounds.width ?? 0;
  const height = bounds.height ?? 0;
  if (width <= 0 || height <= 0) return false;
  if (!Array.isArray(displays) || displays.length === 0) return false;

  const bx1 = bounds.x;
  const by1 = bounds.y;
  const bx2 = bx1 + width;
  const by2 = by1 + height;

  return displays.some((d) => {
    const raw = d && typeof d === "object" && "bounds" in d && d.bounds ? d.bounds : d;
    if (
      !raw ||
      typeof raw.x !== "number" ||
      typeof raw.y !== "number" ||
      typeof raw.width !== "number" ||
      typeof raw.height !== "number" ||
      raw.width <= 0 ||
      raw.height <= 0
    ) {
      return false;
    }
    const dx1 = raw.x;
    const dy1 = raw.y;
    const dx2 = dx1 + raw.width;
    const dy2 = dy1 + raw.height;
    return bx1 < dx2 && bx2 > dx1 && by1 < dy2 && by2 > dy1;
  });
}

/**
 * Validate remembered window bounds read from disk. Anything malformed falls back to defaults.
 * If displays are provided and the candidate position does not intersect any display, x and y are dropped.
 * @param {unknown} raw
 * @param {{ width: number, height: number, minWidth: number, minHeight: number }} d
 * @param {readonly DisplayLike[]} [displays]
 * @returns {{ width: number, height: number, x?: number, y?: number, maximized: boolean }}
 */
export function sanitizeBounds(raw, d, displays) {
  const r = raw && typeof raw === "object" ? /** @type {Record<string, unknown>} */ (raw) : {};
  const num = (/** @type {unknown} */ v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const width = num(r.width);
  const height = num(r.height);
  const x = num(r.x);
  const y = num(r.y);
  const sane = width !== null && height !== null && width <= 10_000 && height <= 10_000;
  const finalWidth = sane ? Math.max(width, d.minWidth) : d.width;
  const finalHeight = sane ? Math.max(height, d.minHeight) : d.height;
  const hasPos = sane && x !== null && y !== null && Math.abs(x) < 20_000 && Math.abs(y) < 20_000;
  const posAllowed = hasPos && (!displays || isWithinAnyDisplay({ x, y, width: finalWidth, height: finalHeight }, displays));
  return {
    width: finalWidth,
    height: finalHeight,
    ...(posAllowed ? { x, y } : {}),
    maximized: r.maximized === true,
  };
}
