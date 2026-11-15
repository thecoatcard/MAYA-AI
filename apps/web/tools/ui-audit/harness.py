"""MAYA web UI audit harness (Playwright + mocked /api/v1/*).

Usage (from anywhere; Python must have playwright, e.g. backend/.venv):
  python harness.py --list
  python harness.py --plan standard --workers 4
  python harness.py --pages tasks,memory --widths 320,760,1440 --states normal,long,error500 --theme both
  python harness.py --pages all --states normal --widths 1440 --theme light --keyboard --shots all
  python harness.py --pages all --plan standard --reduced-motion       # emulate prefers-reduced-motion
  python harness.py --pages all --states normal --widths 1440 --forced-colors
  python harness.py --pages all --states normal --widths 1440 --zoom 2  # 200% zoom: viewport = width/2 at DPR 2

Serves apps/web/.next-desktop (build it first: npm --prefix apps/desktop run build:web) from a
same-origin Python http.server and intercepts every /api/v1/* request with fixtures.Scenario.
Writes <out>/results.json, <out>/summary.md and screenshots under <out>/shots/.

Detectors run in the page (see DETECT_JS): horizontal page overflow, children wider than their
parent, clipped text (overflow hidden, no ellipsis), elements outside the viewport, overlapping
siblings, low text contrast, small hit targets, and (with --keyboard) a Tab traversal checking
focus visibility and obscured focus. Exit code is 0 unless the run itself failed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402

REPO = Path(__file__).resolve().parents[4]  # apps/web/tools/ui-audit -> repo root
DEFAULT_EXPORT = REPO / "apps" / "web" / ".next-desktop"

ALL_WIDTHS = [320, 480, 760, 1024, 1440, 2560]
HEIGHTS = {320: 640, 480: 800, 760: 900, 1024: 768, 1440: 900, 2560: 1300}
KEY_STATES = {"normal", "empty", "long", "error500", "signedout", "unreachable", "many", "expired401"}


# --------------------------------------------------------------------------- static server
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def send_head(self):
        path = self.translate_path(self.path.split("?", 1)[0])
        p = Path(path)
        if not p.exists() or (p.is_dir() and not (p / "index.html").exists()):
            body = (Path(self.directory) / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            import io
            return io.BytesIO(body)
        return super().send_head()


def start_server(directory: Path) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# --------------------------------------------------------------------------- page-side detectors
DETECT_JS = r"""
() => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const out = {viewport: [vw, vh], pageOverflowX: false, pageOverflowY: null, wider: [], clipped: [], clippedY: [],
               outside: [], overlap: [], contrast: [], smallTargets: [], counts: {}, nestedScroll: [], fixedWide: []};
  const de = document.documentElement;
  out.pageOverflowX = de.scrollWidth > de.clientWidth + 1 || document.body.scrollWidth > de.clientWidth + 1;
  out.scrollWidth = Math.max(de.scrollWidth, document.body.scrollWidth);
  out.pageOverflowY = de.scrollHeight > de.clientHeight + 1;
  const path = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 4; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      const tid = e.getAttribute('data-testid'); if (tid) s += `[data-testid=${tid}]`;
      else if (e.id) s += '#' + e.id;
      else if (e.className && typeof e.className === 'string') s += '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.');
      parts.unshift(s);
    }
    return parts.join(' > ').slice(0, 160);
  };
  const vis = (el, cs, r) => cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && cs.opacity !== '0';
  const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  const scrollerX = (el) => { for (let e = el.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return e; } return null; };
  const all = Array.from(document.body.querySelectorAll('*')).filter((e) => !['SCRIPT','STYLE','NEXT-ROUTE-ANNOUNCER','PATH','CIRCLE','LINE','RECT','G','POLYLINE','POLYGON'].includes(e.tagName.toUpperCase()) && !e.closest('svg') );
  out.counts.elements = all.length;
  const lists = {wider: 14, clipped: 14, clippedY: 10, outside: 14, overlap: 12, contrast: 14, smallTargets: 10, nestedScroll: 8, fixedWide: 6};
  const total = {}; for (const k in lists) total[k] = 0;
  const push = (k, v) => { total[k]++; if (out[k].length < lists[k]) out[k].push(v); };

  // colour parsing through a canvas so oklab()/color-mix() work
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', {willReadFrequently: true});
  const cache = new Map();
  const rgba = (c) => { if (cache.has(c)) return cache.get(c); cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0,0,1,1); const d = cx.getImageData(0,0,1,1).data; let v;
    // canvas premultiplies; recover alpha by drawing on transparent: alpha is d[3]
    v = [d[0], d[1], d[2], d[3] / 255]; cache.set(c, v); return v; };
  const over = (top, bot) => { const a = top[3] + bot[3] * (1 - top[3]); if (a === 0) return [0,0,0,0]; return [0,1,2].map((i) => (top[i]*top[3] + bot[i]*bot[3]*(1-top[3]))/a).concat([a]); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126*f(c[0]) + 0.7152*f(c[1]) + 0.0722*f(c[2]); };
  const bgOf = (el) => { const layers = []; let hasImg = false; for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e); if (cs.backgroundImage && cs.backgroundImage !== 'none') hasImg = true; const b = rgba(cs.backgroundColor); if (b[3] > 0) layers.push(b); if (b[3] >= 0.99) break; }
    let acc = [255,255,255,1]; if (!layers.length || layers[layers.length-1][3] < 0.99) { const rb = rgba(getComputedStyle(document.documentElement).backgroundColor); acc = rb[3] > 0 ? rb : rgba(getComputedStyle(document.body).backgroundColor); if (acc[3] < 0.99) acc = [255,255,255,1]; }
    for (let i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc); return {c: acc, hasImg}; };

  const rects = new Map();
  for (const el of all) { const cs = getComputedStyle(el); const r = el.getBoundingClientRect(); rects.set(el, [cs, r]); }

  for (const el of all) {
    const [cs, r] = rects.get(el); if (!vis(el, cs, r)) continue;
    const parent = el.parentElement; const pr = parent && rects.get(parent) ? rects.get(parent)[1] : null;
    const sx = scrollerX(el);
    // children wider than parent (not clipped by scroller, not positioned out of flow)
    if (pr && cs.position !== 'absolute' && cs.position !== 'fixed' && cs.position !== 'sticky') {
      const ps = rects.get(parent)[0];
      const pScrolls = ps.overflowX === 'auto' || ps.overflowX === 'scroll';
      if (!pScrolls && (r.right > pr.right + 1.5 || r.left < pr.left - 1.5) && pr.width > 0) {
        push('wider', {el: path(el), text: txt(el), by: Math.round(Math.max(r.right - pr.right, pr.left - r.left)), w: Math.round(r.width), parentW: Math.round(pr.width)});
      }
    }
    // outside viewport (horizontal), unless inside a horizontal scroller that clips it
    if (cs.position !== 'fixed' && (r.right > vw + 1.5 || r.left < -1.5) && !(sx && sx !== document.documentElement && sx !== document.body && getComputedStyle(sx).overflowX !== 'visible')) {
      push('outside', {el: path(el), text: txt(el), left: Math.round(r.left), right: Math.round(r.right), vw});
    }
    if (cs.position === 'fixed' && (r.right > vw + 1.5 || r.left < -1.5 || r.bottom > vh + 1.5)) push('fixedWide', {el: path(el), left: Math.round(r.left), right: Math.round(r.right), bottom: Math.round(r.bottom), vw, vh});
    // clipped text
    const ox = cs.overflowX, oy = cs.overflowY;
    const srOnly = el.clientWidth <= 1 && el.clientHeight <= 1;
    if (!srOnly && (ox === 'hidden' || ox === 'clip') && el.scrollWidth > el.clientWidth + 1 && cs.textOverflow !== 'ellipsis' && !el.closest('svg')) {
      push('clipped', {el: path(el), text: txt(el), scrollW: el.scrollWidth, clientW: el.clientWidth});
    } else if (!srOnly && cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1 && !el.getAttribute('title') && !el.getAttribute('aria-label')) {
      // truncated with ellipsis but no accessible full text
      push('clipped', {el: path(el), text: txt(el), scrollW: el.scrollWidth, clientW: el.clientWidth, note: 'ellipsis-no-title'});
    }
    if (!srOnly && (oy === 'hidden' || oy === 'clip') && el.scrollHeight > el.clientHeight + 2 && el.clientHeight > 0 && cs.display !== 'inline') {
      if (!(cs.webkitLineClamp && cs.webkitLineClamp !== 'none')) push('clippedY', {el: path(el), text: txt(el), scrollH: el.scrollHeight, clientH: el.clientHeight});
    }
    // nested scroll containers
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 2) {
      let anc = null; for (let e = el.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e).overflowY; if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 2) { anc = e; break; } }
      if (anc) push('nestedScroll', {el: path(el), inside: path(anc)});
    }
    // small targets
    if (el.matches('a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=tab],[role=switch]') && (r.width < 24 || r.height < 24) && !el.disabled) {
      push('smallTargets', {el: path(el), text: txt(el) || el.getAttribute('aria-label'), w: Math.round(r.width), h: Math.round(r.height)});
    }
    // contrast (own text nodes only)
    const ownText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (ownText) {
      const fg0 = rgba(cs.color); const bg = bgOf(el); let fg = fg0; if (fg0[3] < 1) fg = over(fg0, bg.c);
      const L1 = lum(fg), L2 = lum(bg.c); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700; const large = size >= 24 || (size >= 18.66 && bold);
      if (!bg.hasImg && ratio < (large ? 3 : 4.5) && !(el.closest('[disabled],[aria-disabled=true]'))) push('contrast', {el: path(el), text: txt(el), ratio: +ratio.toFixed(2), fg: cs.color, size});
    }
  }
  // overlapping siblings (in-flow, visible)
  const parents = new Set(all.map((e) => e.parentElement).filter(Boolean));
  for (const p of parents) {
    const kids = Array.from(p.children).filter((k) => rects.has(k)).filter((k) => { const [cs, r] = rects.get(k); return vis(k, cs, r) && cs.position !== 'absolute' && cs.position !== 'fixed' && cs.position !== 'sticky'; });
    if (kids.length < 2 || kids.length > 60) continue;
    for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
      const a = rects.get(kids[i])[1], b = rects.get(kids[j])[1];
      const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left), iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (ix > 3 && iy > 3) push('overlap', {a: path(kids[i]), b: path(kids[j]), ix: Math.round(ix), iy: Math.round(iy)});
    }
  }
  out.totals = total;
  out.counts.interactive = all.filter((e) => e.matches('a[href],button,input,select,textarea,[tabindex]')).length;
  out.counts.landmarks = {main: document.querySelectorAll('main').length, h1: document.querySelectorAll('h1').length};
  out.animations = document.getAnimations().filter((a) => a.playState === 'running').map((a) => (a.animationName || a.transitionProperty || 'anim')).slice(0, 10);
  out.animationCount = document.getAnimations().filter((a) => a.playState === 'running').length;
  out.title = document.title; out.url = location.pathname + location.search;
  const mm = document.querySelector('#maya-main'); out.mainText = mm ? (mm.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 700) : '';
  out.bodyText = (document.body.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 200);
  return out;
}
"""

FOCUS_JS = r"""
() => {
  const e = document.activeElement; if (!e || e === document.body) return null;
  const cs = getComputedStyle(e), r = e.getBoundingClientRect();
  let outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
  let shadow = cs.boxShadow && cs.boxShadow !== 'none';
  // the indicator may sit on a focus-within ancestor (composer box)
  for (let a = e.parentElement, n = 0; a && !outline && !shadow && n < 4; a = a.parentElement, n++) { const ac = getComputedStyle(a); if (a.matches(':focus-within') && ((ac.outlineStyle !== 'none' && parseFloat(ac.outlineWidth) > 0) || (ac.boxShadow && ac.boxShadow !== 'none'))) { outline = true; } }
  let obscured = false;
  if (r.width > 0 && r.height > 0) { const x = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1), y = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
    const t = document.elementFromPoint(x, y); obscured = !!t && t !== e && !e.contains(t) && !t.contains(e); }
  const offscreen = r.right < 0 || r.left > innerWidth || r.bottom < 0 || r.top > innerHeight;
  const label = (e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || e.tagName).trim().replace(/\s+/g, ' ').slice(0, 40);
  const sel = e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.getAttribute('data-testid') ? `[data-testid=${e.getAttribute('data-testid')}]` : '');
  return {sel, label, visibleIndicator: outline || !!shadow, outline: outline ? cs.outline : null, obscured, offscreen, w: Math.round(r.width), h: Math.round(r.height)};
}
"""


def keyboard_walk(page, max_tabs: int = 45) -> dict:
    steps, problems, seen = [], [], set()
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0,0); }")
    for i in range(max_tabs):
        page.keyboard.press("Tab")
        page.wait_for_timeout(220)  # let focus transitions (skip link) finish
        info = page.evaluate(FOCUS_JS)
        if info is None:
            if i > 0:
                break  # focus left the document (wrapped to browser chrome)
            continue
        key = info["sel"] + "|" + info["label"]
        if key in seen:
            break
        seen.add(key)
        steps.append(info)
        if not info["visibleIndicator"]:
            problems.append({"type": "no-visible-focus", **info})
        if info["obscured"]:
            problems.append({"type": "focus-obscured", **info})
        if info["offscreen"]:
            problems.append({"type": "focus-offscreen", **info})
    return {"stops": len(steps), "problems": problems[:12], "problemCount": len(problems), "order": [s["label"] for s in steps][:45]}


# --------------------------------------------------------------------------- per-page preparation
def prepare(page, pname: str, state: str) -> None:
    """Best-effort interactions that reveal worst-case content (never raise)."""
    try:
        if pname == "conversation" and state not in ("error500", "expired401", "signedout", "unreachable", "slow", "empty"):
            btn = page.locator("[data-testid=conversation-slot] button").first
            if btn.count():
                btn.click(timeout=2500)
                page.wait_for_timeout(900)
        elif pname == "x-tasks-newtask":
            page.locator("main").get_by_role("button", name=re.compile("New Task", re.I)).first.click(timeout=3000)
            page.wait_for_timeout(600)
        elif pname == "x-approvals-detail" and state not in ("error500", "expired401", "empty", "slow"):
            page.locator("main button").first.click(timeout=3000)
            page.wait_for_timeout(700)
        elif pname == "x-memory-add":
            page.locator("main").get_by_role("button", name=re.compile("add|new|create", re.I)).first.click(timeout=3000)
            page.wait_for_timeout(600)
        elif pname == "x-connections-register":
            page.locator("main").get_by_role("button", name=re.compile("register|add|new", re.I)).first.click(timeout=3000)
            page.wait_for_timeout(600)
        elif pname == "x-conversation-approval":
            page.locator("[data-testid=conversation-slot] button").first.click(timeout=3000)
            page.wait_for_timeout(2500)
            page.get_by_role("button", name=re.compile("review", re.I)).first.click(timeout=3000)
            page.wait_for_timeout(700)
        elif pname == "tasks" and state not in ("error500", "expired401", "signedout", "unreachable", "slow", "empty"):
            first = page.locator("main button, main [role=button], main article").first
            if first.count():
                first.click(timeout=2500)
                page.wait_for_timeout(700)
    except Exception:
        pass


def capture(page, scen: fixtures.Scenario, pname: str, state: str, args, width: int = 0) -> dict:
    path, kind = fixtures.PAGES[pname]
    res: dict = {}
    settle = 900 if state != "slow" else 1200
    page.wait_for_timeout(settle)
    if kind == "redirect":
        page.wait_for_timeout(1000)
    if args.keyboard and kind != "redirect" and (args.plan != "standard" or (state == "normal" and width in (760, 1440))):
        res["keyboard"] = keyboard_walk(page)  # before prepare(): clicks would move the focus start point
        page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0,0); }")
    prepare(page, pname, state)
    res["detect"] = page.evaluate(DETECT_JS)
    res["finalUrl"] = page.url.split("//", 1)[-1].split("/", 1)[-1]
    return res


# --------------------------------------------------------------------------- run
def build_plan(args) -> list[tuple[str, str, int, str]]:
    pages = list(fixtures.PAGES) if args.pages == "all" else args.pages.split(",")
    themes = ["light", "dark"] if args.theme == "both" else [args.theme]
    plan = []
    if args.plan == "standard":
        for p in pages:
            kind = fixtures.PAGES[p][1]
            for t in themes:
                for w in ALL_WIDTHS:
                    plan.append((p, "normal", w, t))
                if kind in ("redirect", "notfound"):
                    continue
                for s in fixtures.STATES:
                    if s == "normal":
                        continue
                    if kind == "auth" and s not in ("empty", "long", "rtl", "unreachable", "signedout"):
                        continue
                    for w in (320, 760, 1440):
                        plan.append((p, s, w, t))
    else:
        widths = [int(w) for w in args.widths.split(",")]
        states = list(fixtures.STATES) if args.states == "all" else args.states.split(",")
        for p in pages:
            for s in states:
                for w in widths:
                    for t in themes:
                        plan.append((p, s, w, t))
    return plan


def want_shot(args, state: str, width: int, theme: str) -> bool:
    if args.shots == "none":
        return False
    if args.shots == "all":
        return True
    if state == "normal":
        return width in (320, 760, 1440) and True
    return state in KEY_STATES and width in (320, 1440) and theme == "light"


def run(args) -> dict:
    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    (out / "shots").mkdir(parents=True, exist_ok=True)
    plan = build_plan(args)
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        plan = [c for k, c in enumerate(plan) if k % n == i]
    srv, port = start_server(Path(args.export))
    base = f"http://127.0.0.1:{port}"
    results = []
    t0 = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx_cache: dict = {}
        for idx, (pname, state, width, theme) in enumerate(plan, 1):
            path, kind = fixtures.PAGES[pname]
            scen = fixtures.make_state(state)
            vw = int(width / args.zoom)
            vh = int(HEIGHTS.get(width, 900) / args.zoom)
            ctx = browser.new_context(
                viewport={"width": vw, "height": vh},
                device_scale_factor=args.zoom if args.zoom != 1 else 1,
                color_scheme=theme,
                reduced_motion="reduce" if args.reduced_motion else "no-preference",
                forced_colors="active" if args.forced_colors else "none",
                locale="en-US",
            )
            ctx.add_init_script(f"try{{localStorage.setItem('maya.theme','{theme}')}}catch(e){{}}")
            api_log: list[str] = []

            def on_route(route, _request=None, scen=scen, api_log=api_log):
                req = route.request
                try:
                    body = req.post_data_json if req.post_data else None
                except Exception:
                    body = None
                status, payload, delay, ctype = scen.handle(req.method, req.url, body)
                api_log.append(f"{req.method} {req.url.split('/api/v1')[-1].split('?')[0]} -> {status}")
                if status == -1:
                    route.abort("connectionrefused")
                    return
                if delay:
                    return  # 'slow' state: leave the request pending forever so the loading UI is captured
                if status == 204:
                    route.fulfill(status=204, body="")
                    return
                if ctype:
                    route.fulfill(status=status, content_type=ctype, body=payload if isinstance(payload, str) else json.dumps(payload))
                else:
                    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

            ctx.route("**/api/v1/**", on_route)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)[:200]))
            page.on("console", lambda m, errors=errors: errors.append("console.error: " + m.text[:200]) if m.type == "error" and "Failed to load resource" not in m.text else None)
            rec: dict = {"page": pname, "state": state, "width": width, "theme": theme, "zoom": args.zoom,
                         "reducedMotion": args.reduced_motion, "forcedColors": args.forced_colors}
            try:
                page.goto(base + path, wait_until="load", timeout=20000)
                rec.update(capture(page, scen, pname, state, args, width))
                if want_shot(args, state, width, theme):
                    name = f"{pname}__{state}__{width}__{theme}.jpg"
                    page.screenshot(path=str(out / "shots" / name), type="jpeg", quality=62)
                    rec["screenshot"] = name
            except Exception as exc:  # keep going
                rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            rec["jsErrors"] = errors[:5]
            rec["apiCalls"] = len(api_log)
            rec["unmocked"] = sorted(set(scen.unmocked))
            ctx.close()
            results.append(rec)
            if idx % 20 == 0:
                print(f"[{args.shard or '0/1'}] {idx}/{len(plan)} {time.time() - t0:.0f}s", flush=True)
        browser.close()
    srv.shutdown()
    return {"meta": {"base": "static export", "cases": len(results), "seconds": round(time.time() - t0)}, "results": results}


ISSUE_KEYS = ["wider", "clipped", "clippedY", "outside", "overlap", "contrast", "smallTargets", "fixedWide", "nestedScroll"]


def summarise(data: dict) -> str:
    rows = data["results"]
    lines = ["# UI audit summary", "", f"Cases: {len(rows)}", ""]
    ov = [r for r in rows if r.get("detect", {}).get("pageOverflowX")]
    lines += [f"## Horizontal page overflow: {len(ov)} cases", ""]
    seen = {}
    for r in ov:
        seen.setdefault((r["page"], r["state"]), []).append(f'{r["width"]}/{r["theme"]}')
    for (p, s), v in sorted(seen.items()):
        lines.append(f"- {p} / {s}: {', '.join(sorted(set(v)))}")
    lines += ["", "## Issue totals per page (all cases)", "", "| page | cases | " + " | ".join(ISSUE_KEYS) + " | errors |", "|---|---|" + "---|" * (len(ISSUE_KEYS) + 1)]
    pages = sorted({r["page"] for r in rows})
    for p in pages:
        pr = [r for r in rows if r["page"] == p]
        sums = [sum((r.get("detect", {}).get("totals") or {}).get(k, 0) for r in pr) for k in ISSUE_KEYS]
        errs = sum(1 for r in pr if r.get("error"))
        lines.append(f"| {p} | {len(pr)} | " + " | ".join(map(str, sums)) + f" | {errs} |")
    kb = [r for r in rows if r.get("keyboard") and r["keyboard"]["problemCount"]]
    lines += ["", f"## Keyboard problems: {len(kb)} cases", ""]
    for r in kb[:30]:
        lines.append(f'- {r["page"]}/{r["state"]}/{r["width"]}/{r["theme"]}: ' + "; ".join(f'{x["type"]} {x["sel"]} "{x["label"]}"' for x in r["keyboard"]["problems"][:3]))
    errs = [r for r in rows if r.get("error")]
    lines += ["", f"## Run errors: {len(errs)}", ""] + [f'- {r["page"]}/{r["state"]}/{r["width"]}/{r["theme"]}: {r["error"]}' for r in errs[:20]]
    um = sorted({u for r in rows for u in r.get("unmocked", [])})
    lines += ["", "## Unmocked endpoints requested", ""] + [f"- {u}" for u in um] if um else ["", "## Unmocked endpoints requested", "", "none"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", default="all", help="comma list or 'all'")
    ap.add_argument("--widths", default="320,760,1440")
    ap.add_argument("--states", default="normal", help="comma list or 'all'")
    ap.add_argument("--theme", default="both", choices=["light", "dark", "both"])
    ap.add_argument("--plan", default="", choices=["", "standard"], help="standard: normal at 6 widths + every stress state at 320/760/1440")
    ap.add_argument("--shots", default="key", choices=["none", "key", "all"])
    ap.add_argument("--keyboard", action="store_true", help="Tab traversal focus checks")
    ap.add_argument("--reduced-motion", action="store_true")
    ap.add_argument("--forced-colors", action="store_true")
    ap.add_argument("--zoom", type=float, default=1.0, help="2 = 200%% browser zoom (viewport = width/2, DPR 2)")
    ap.add_argument("--export", default=str(DEFAULT_EXPORT))
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--shard", default="", help="i/n (internal)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("pages :", ", ".join(fixtures.PAGES))
        print("states:", ", ".join(fixtures.STATES))
        print("widths:", ", ".join(map(str, ALL_WIDTHS)))
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.workers > 1 and not args.shard:
        procs = []
        passthrough, skip = [], 0
        argv = sys.argv[1:]
        for k, a in enumerate(argv):
            if skip:
                skip -= 1
                continue
            if a == "--workers":
                skip = 1
                continue
            if a.startswith("--workers="):
                continue
            if a == "--out":
                skip = 1
                continue
            passthrough.append(a)
        for i in range(args.workers):
            sub_out = out / f"_shard{i}"
            cmd = [sys.executable, str(Path(__file__).resolve())] + passthrough + ["--shard", f"{i}/{args.workers}", "--out", str(sub_out)]
            procs.append((i, subprocess.Popen(cmd)))
        for _, p in procs:
            p.wait()
        merged: list = []
        for i in range(args.workers):
            f = out / f"_shard{i}" / "results.json"
            if f.exists():
                merged += json.loads(f.read_text(encoding="utf-8"))["results"]
            shots = out / f"_shard{i}" / "shots"
            if shots.exists():
                (out / "shots").mkdir(exist_ok=True)
                for s in shots.iterdir():
                    s.replace(out / "shots" / s.name)
            shutil.rmtree(out / f"_shard{i}", ignore_errors=True)
        data = {"meta": {"cases": len(merged), "workers": args.workers}, "results": merged}
    else:
        data = run(args)

    (out / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.shard:
        (out / "summary.md").write_text(summarise(data), encoding="utf-8")
        print(f"wrote {out / 'results.json'} ({len(data['results'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
