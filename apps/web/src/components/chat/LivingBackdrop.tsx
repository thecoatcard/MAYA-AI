"use client";

import React, { useEffect, useRef } from "react";

import { usePrefersReducedMotion } from "./useReducedMotion";

/** Upper bound on drawn particles. A test holds this so the field can never grow unnoticed. */
export const LIVING_DOT_COUNT = 28;

const CANVAS_W = 1100;
const CANVAS_H = 600;
/** The loop draws at most this often. 30 frames a second is plenty for a slow drift. */
const FRAME_MS = 33;
/** Lane radii (half-width) in canvas px; the lanes are flat ellipses tilted a little. */
const LANES = [170, 270, 380] as const;
const LANE_FLAT = 0.34;
/** Distinct base tilt angles for each of the 3 orbital planes */
const LANE_TILTS = [-0.26, -0.18, -0.32] as const;
/** Slow precession rates in radians per second (gentle orbital rotation) */
const PRECESSION_RATES = [0.007, -0.005, 0.004] as const;
const BREATH_S = 6.4;

interface Dot {
  readonly lane: number;
  readonly phase: number;
  /** Radians per second (one lap takes 70 to 130 seconds). */
  readonly speed: number;
  readonly radius: number;
  readonly alpha: number;
  readonly depth: number;
}

/** Deterministic spread so every render and every test sees the same field. */
export function makeDots(count: number = LIVING_DOT_COUNT): Dot[] {
  let seed = 0x9e3779b9;
  const rand = (): number => {
    seed = (Math.imul(seed ^ (seed >>> 15), 0x2c1b3c6d) + 0x1b873593) >>> 0;
    return seed / 0x100000000;
  };
  const dots: Dot[] = [];
  for (let i = 0; i < Math.min(count, LIVING_DOT_COUNT); i += 1) {
    const lane = i % LANES.length;
    const direction = lane === 1 ? -1 : 1;
    dots.push({
      lane,
      phase: rand() * Math.PI * 2,
      speed: (direction * (Math.PI * 2)) / (70 + rand() * 60),
      radius: 1 + rand() * 1.1,
      alpha: 0.3 + rand() * 0.3,
      depth: 0.4 + rand() * 0.9,
    });
  }
  return dots;
}

interface Palette {
  accent: string;
  accentHover: string;
  muted: string;
}

function readPalette(el: Element): Palette {
  const style = getComputedStyle(el);
  const pick = (name: string, fallback: string): string => {
    const value = style.getPropertyValue(name).trim();
    return value === "" ? fallback : value;
  };
  return {
    accent: pick("--maya-accent", "currentColor"),
    accentHover: pick("--maya-accent-hover", "currentColor"),
    muted: pick("--maya-text-muted", "currentColor"),
  };
}

/**
 * A calm, living MAYA presence: a living orb with layered luminescence, three elegant
 * elliptical orbit rings with slow precession, and a sparse field of 28 tiny dust/shuttle
 * particles that drift along them and lean smoothly toward the pointer. aria-hidden,
 * pointer-transparent, rendered behind the text.
 *
 * Cost control: one small canvas, drawn by one requestAnimationFrame loop at 30 fps that is
 * stopped while the tab is hidden or the canvas is off screen, and never started under
 * prefers-reduced-motion (one static frame is drawn instead). The component is mounted only
 * while the empty state is shown, so leaving it (a message, typing, voice) unmounts the canvas
 * and cancels the loop: zero work after that.
 */
export function LivingBackdrop(): React.JSX.Element {
  const ref = useRef<HTMLCanvasElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const canvas = ref.current;
    if (canvas === null) return undefined;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      ctx = null;
    }
    if (ctx === null) return undefined;
    const g = ctx;

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(CANVAS_W * dpr);
    canvas.height = Math.round(CANVAS_H * dpr);
    // Narrow screens: shrink the lanes so the field stays on screen.
    const fit = Math.min(1, Math.max(0.38, window.innerWidth / 900));
    const dots = makeDots();
    let palette = readPalette(canvas);
    const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
    let raf = 0;
    let last = 0;
    let started = 0;
    let visible = !document.hidden;
    let onScreen = true;

    const draw = (seconds: number, ramp: number): void => {
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, CANVAS_W, CANVAS_H);
      const cx = CANVAS_W / 2;
      const cy = CANVAS_H / 2;
      const breath = reduced ? 0 : Math.sin((seconds / BREATH_S) * Math.PI * 2);
      const pulse = reduced
        ? 0
        : Math.sin((seconds / (BREATH_S * 0.5)) * Math.PI * 2);

      // 1. Three elegant elliptical orbit rings with slow precession and responsive parallax
      for (let i = 0; i < LANES.length; i += 1) {
        const rx = LANES[i] * fit;
        const tilt =
          LANE_TILTS[i] + (reduced ? 0 : PRECESSION_RATES[i] * seconds);
        g.save();
        g.translate(
          cx + pointer.x * (4 + i * 2),
          cy + pointer.y * (3 + i * 1.5),
        );
        g.rotate(tilt);
        g.globalAlpha = (0.13 + i * 0.02) * ramp;
        g.strokeStyle = palette.muted;
        g.lineWidth = 1;
        g.beginPath();
        g.ellipse(0, 0, rx, rx * LANE_FLAT, 0, 0, Math.PI * 2);
        g.stroke();
        g.restore();
      }

      // 2. Soft dust/shuttle particles drifting smoothly along their precessing lanes
      g.fillStyle = palette.accent;
      for (const dot of dots) {
        const rx = LANES[dot.lane] * fit;
        const angle = dot.phase + (reduced ? 0 : dot.speed * seconds);
        const tilt =
          LANE_TILTS[dot.lane] +
          (reduced ? 0 : PRECESSION_RATES[dot.lane] * seconds);
        const lx = Math.cos(angle) * rx;
        const ly = Math.sin(angle) * rx * LANE_FLAT;
        const x =
          cx +
          lx * Math.cos(tilt) -
          ly * Math.sin(tilt) +
          pointer.x * (10 + dot.depth * 6);
        const y =
          cy +
          lx * Math.sin(tilt) +
          ly * Math.cos(tilt) +
          pointer.y * (7 + dot.depth * 4);
        // Keep dots faint where the heading and hint sit so typography stays crisp
        const overText = Math.abs(x - cx) < 220 && y - cy > 45 && y - cy < 250;
        g.globalAlpha = dot.alpha * ramp * (overText ? 0.25 : 1);
        g.beginPath();
        g.arc(x, y, dot.radius, 0, Math.PI * 2);
        g.fill();

        // Subtle stardust glow around brighter shuttle particles
        if (dot.radius > 1.5) {
          g.globalAlpha = dot.alpha * ramp * 0.25 * (overText ? 0.2 : 1);
          g.beginPath();
          g.arc(x, y, dot.radius * 2.2, 0, Math.PI * 2);
          g.fill();
        }
      }

      // 3. Living orb mark: ambient aura, inner luminous bloom, iridescent ring, inner resonance, and pulsing core
      const orbX = cx + pointer.x * 6;
      const orbY = cy + pointer.y * 4;

      // Ambient outer halo
      const auraR = 100 * (1 + breath * 0.04);
      const halo = g.createRadialGradient(orbX, orbY, 6, orbX, orbY, auraR);
      halo.addColorStop(0, palette.accent);
      halo.addColorStop(1, "transparent");
      g.globalAlpha = 0.16 * ramp;
      g.fillStyle = halo;
      g.beginPath();
      g.arc(orbX, orbY, auraR, 0, Math.PI * 2);
      g.fill();

      // Inner luminous bloom
      const bloomR = 48 * (1 - breath * 0.03);
      const bloom = g.createRadialGradient(orbX, orbY, 4, orbX, orbY, bloomR);
      bloom.addColorStop(0, palette.accentHover);
      bloom.addColorStop(0.7, palette.accent);
      bloom.addColorStop(1, "transparent");
      g.globalAlpha = 0.24 * ramp;
      g.fillStyle = bloom;
      g.beginPath();
      g.arc(orbX, orbY, bloomR, 0, Math.PI * 2);
      g.fill();

      // Outer containment ring with dual-stop gradient
      const ringR = 30 * (1 + breath * 0.035);
      const ring = g.createLinearGradient(
        orbX - ringR,
        orbY - ringR,
        orbX + ringR,
        orbY + ringR,
      );
      ring.addColorStop(0, palette.accent);
      ring.addColorStop(1, palette.accentHover);
      g.globalAlpha = 0.9 * ramp;
      g.strokeStyle = ring;
      g.lineWidth = 2.2;
      g.beginPath();
      g.arc(orbX, orbY, ringR, 0, Math.PI * 2);
      g.stroke();

      // Inner resonance ring (gentle out-of-phase pulse)
      const innerRingR = 20 * (1 + pulse * 0.04);
      const innerRing = g.createLinearGradient(
        orbX + innerRingR,
        orbY - innerRingR,
        orbX - innerRingR,
        orbY + innerRingR,
      );
      innerRing.addColorStop(0, palette.accentHover);
      innerRing.addColorStop(1, palette.accent);
      g.globalAlpha = (0.35 + breath * 0.1) * ramp;
      g.strokeStyle = innerRing;
      g.lineWidth = 1;
      g.beginPath();
      g.arc(orbX, orbY, innerRingR, 0, Math.PI * 2);
      g.stroke();

      // Pulsing intelligent core with depth
      const coreR = 9.5 * (1 - breath * 0.05);
      const coreGrad = g.createRadialGradient(
        orbX - coreR * 0.25,
        orbY - coreR * 0.25,
        coreR * 0.1,
        orbX,
        orbY,
        coreR,
      );
      coreGrad.addColorStop(0, palette.accentHover);
      coreGrad.addColorStop(1, palette.accent);
      g.globalAlpha = 0.95 * ramp;
      g.fillStyle = coreGrad;
      g.beginPath();
      g.arc(orbX, orbY, coreR, 0, Math.PI * 2);
      g.fill();
      g.globalAlpha = 1;
    };

    if (reduced) {
      // Static composition: one frame, no loop, no pointer listener.
      draw(0, 1);
      const themeWatch = new MutationObserver(() => {
        palette = readPalette(canvas);
        draw(0, 1);
      });
      themeWatch.observe(document.documentElement, { attributes: true });
      return () => themeWatch.disconnect();
    }

    const frame = (now: number): void => {
      raf = 0;
      if (!visible || !onScreen) return;
      raf = window.requestAnimationFrame(frame);
      if (now - last < FRAME_MS) return;
      last = now;
      if (started === 0) started = now;
      pointer.x += (pointer.tx - pointer.x) * 0.05;
      pointer.y += (pointer.ty - pointer.y) * 0.05;
      const ramp = Math.min(1, (now - started) / 1400);
      draw(now / 1000, ramp);
    };
    const sync = (): void => {
      const shouldRun = visible && onScreen;
      if (shouldRun && raf === 0) raf = window.requestAnimationFrame(frame);
      if (!shouldRun && raf !== 0) {
        window.cancelAnimationFrame(raf);
        raf = 0;
      }
    };
    const onVisibility = (): void => {
      visible = !document.hidden;
      sync();
    };
    const onPointer = (event: PointerEvent): void => {
      pointer.tx = (event.clientX / window.innerWidth) * 2 - 1;
      pointer.ty = (event.clientY / window.innerHeight) * 2 - 1;
    };
    const onPointerLeave = (): void => {
      pointer.tx = 0;
      pointer.ty = 0;
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pointermove", onPointer, { passive: true });
    window.addEventListener("pointerleave", onPointerLeave);
    const io =
      typeof IntersectionObserver === "function"
        ? new IntersectionObserver((entries) => {
            onScreen = entries.some((e) => e.isIntersecting);
            sync();
          })
        : null;
    io?.observe(canvas);
    const themeWatch = new MutationObserver(() => {
      palette = readPalette(canvas);
    });
    themeWatch.observe(document.documentElement, { attributes: true });
    sync();

    return () => {
      if (raf !== 0) window.cancelAnimationFrame(raf);
      raf = 0;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pointerleave", onPointerLeave);
      io?.disconnect();
      themeWatch.disconnect();
    };
  }, [reduced]);

  return (
    <canvas
      ref={ref}
      className="maya-living"
      width={CANVAS_W}
      height={CANVAS_H}
      aria-hidden="true"
      data-testid="living-backdrop"
    />
  );
}
