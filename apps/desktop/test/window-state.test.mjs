import assert from "node:assert/strict";
import test from "node:test";

import { isWithinAnyDisplay, sanitizeBounds } from "../src/window-state.mjs";

test("isWithinAnyDisplay: detects intersection with a single display", () => {
  const displays = [{ bounds: { x: 0, y: 0, width: 1920, height: 1080 } }];

  // Fully inside
  assert.equal(isWithinAnyDisplay({ x: 100, y: 100, width: 1280, height: 820 }, displays), true);

  // Partially overlapping left
  assert.equal(isWithinAnyDisplay({ x: -200, y: 100, width: 1280, height: 820 }, displays), true);

  // Partially overlapping right
  assert.equal(isWithinAnyDisplay({ x: 1800, y: 100, width: 1280, height: 820 }, displays), true);

  // Completely off to the right (e.g. disconnected monitor)
  assert.equal(isWithinAnyDisplay({ x: 2000, y: 100, width: 1280, height: 820 }, displays), false);

  // Completely off above
  assert.equal(isWithinAnyDisplay({ x: 100, y: -900, width: 1280, height: 820 }, displays), false);

  // Completely off below
  assert.equal(isWithinAnyDisplay({ x: 100, y: 1100, width: 1280, height: 820 }, displays), false);
});

test("isWithinAnyDisplay: handles multiple displays and plain rectangle objects", () => {
  const displays = [
    { x: 0, y: 0, width: 1920, height: 1080 },
    { x: 1920, y: 0, width: 2560, height: 1440 },
  ];

  // On second display
  assert.equal(isWithinAnyDisplay({ x: 2000, y: 100, width: 1280, height: 820 }, displays), true);

  // Straddling both displays
  assert.equal(isWithinAnyDisplay({ x: 1500, y: 100, width: 1280, height: 820 }, displays), true);

  // Off beyond second display
  assert.equal(isWithinAnyDisplay({ x: 5000, y: 100, width: 1280, height: 820 }, displays), false);
});

test("isWithinAnyDisplay: invalid inputs return false", () => {
  const displays = [{ bounds: { x: 0, y: 0, width: 1920, height: 1080 } }];

  assert.equal(isWithinAnyDisplay(null, displays), false);
  assert.equal(isWithinAnyDisplay(undefined, displays), false);
  assert.equal(isWithinAnyDisplay({ width: 800, height: 600 }, displays), false); // missing x, y
  assert.equal(isWithinAnyDisplay({ x: 10, y: 10, width: 0, height: 100 }, displays), false);
  assert.equal(isWithinAnyDisplay({ x: 10, y: 10, width: 800, height: 600 }, []), false);
  assert.equal(isWithinAnyDisplay({ x: 10, y: 10, width: 800, height: 600 }, null), false);
});

test("sanitizeBounds: drops x/y when not intersecting any real display", () => {
  const defaults = { width: 1280, height: 820, minWidth: 960, minHeight: 640 };
  const displays = [{ bounds: { x: 0, y: 0, width: 1920, height: 1080 } }];

  // Off-screen window from disconnected display: drops x and y
  const offScreen = { width: 1280, height: 820, x: 2500, y: 200, maximized: false };
  const sanitizedOff = sanitizeBounds(offScreen, defaults, displays);
  assert.deepEqual(sanitizedOff, { width: 1280, height: 820, maximized: false });
  assert.equal(sanitizedOff.x, undefined);
  assert.equal(sanitizedOff.y, undefined);

  // On-screen window preserves x and y
  const onScreen = { width: 1280, height: 820, x: 100, y: 100, maximized: false };
  assert.deepEqual(sanitizeBounds(onScreen, defaults, displays), {
    width: 1280,
    height: 820,
    x: 100,
    y: 100,
    maximized: false,
  });
});
