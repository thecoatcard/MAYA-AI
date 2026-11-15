"""Scene builders for the library layouts and the library look of the classic ones.

Each builder takes a :class:`~app.automation.deck_graphics.Ctx` and a validated
:class:`~app.automation.model.Slide` and returns the slide body as scene primitives (no header,
background or footer: the renderers add those). Text is sized here with the same conservative
estimate the PPTX engine and verifier use, so a slide that fits here passes the overflow check; text
that cannot fit at the floor size raises :class:`SceneFitError` and the renderer splits the slide.

New layouts: ``hero_cover``, ``section_break``, ``stat_grid``, ``icon_cards``, ``process``,
``image_text``, ``chart``. The classic ``timeline``, ``comparison``, ``quote``, ``cards`` and
``stat_callout`` use the same builders when the deck uses a library theme.
"""

from __future__ import annotations

from collections.abc import Callable

from app.automation import deck_icons
from app.automation.deck_graphics import (
    BODY_BOTTOM,
    BODY_H,
    BODY_TOP,
    BOX_INSET,
    CONTENT_W,
    MARGIN_X,
    SLIDE_H,
    SLIDE_W,
    Ctx,
    background_art,
    card,
    fit,
    icon_badge,
    motif,
    number_badge,
    para,
    pill,
    progress_bar,
)
from app.automation.deck_scene import (
    Box,
    ChartShape,
    Gradient,
    IconShape,
    ImageShape,
    LineShape,
    Para,
    Prim,
    SceneFitError,
    Series,
    TextBox,
)
from app.automation.design import best_on, mix
from app.automation.inline import plain_text
from app.automation.model import Slide
from app.automation.textfit import estimate_height_in

PAD = 0.3
GAP = 0.35

Builder = Callable[[Ctx, Slide], list[Prim]]


def _row_geometry(count: int, gap: float = GAP) -> tuple[float, list[float]]:
    width = (CONTENT_W - gap * (count - 1)) / count
    return width, [MARGIN_X + i * (width + gap) for i in range(count)]


def _fit_items(
    items: list[tuple[str, str]],
    width: float,
    height: float,
    *,
    title_start: float,
    title_min: float,
    body_gap: float = 6.0,
    what: str,
) -> tuple[float, float]:
    """``(title_pt, body_pt)`` at which every ``(title, body)`` fits ``width`` x ``height`` (the
    text box's own size). The body is two points smaller than the title, never below 12."""
    inner_w = max(0.1, width - 2 * BOX_INSET)
    inner_h = max(0.1, height - 2 * BOX_INSET)
    size = float(title_start)
    while size >= title_min:
        body = max(12.0, size - 2)
        worst = 0.0
        for title, text in items:
            parts = [(plain_text(title), size)]
            if text:
                parts.append((plain_text(text), body))
            worst = max(worst, estimate_height_in(parts, inner_w, body_gap))
        if worst <= inner_h:
            return size, body
        size -= 1.0
    raise SceneFitError(f"{what} does not fit even at {title_min:g}pt; shorten it")


def _needed_height(
    items: list[tuple[str, str]], width: float, title_pt: float, body_pt: float
) -> float:
    """The text box height (insets included) that holds the tallest ``(title, body)`` item."""
    inner_w = max(0.1, width - 2 * BOX_INSET)
    worst = 0.0
    for title, text in items:
        parts = [(plain_text(title), title_pt)]
        if text:
            parts.append((plain_text(text), body_pt))
        worst = max(worst, estimate_height_in(parts, inner_w, 6.0))
    return worst + 2 * BOX_INSET + 0.08


def _item_paras(
    ctx: Ctx, title: str, body: str, title_pt: float, body_pt: float, *, align: str = "left"
) -> tuple[Para, ...]:
    out = [
        para(
            ctx, title, title_pt, ctx.c.text, bold=True, font="heading", align=align, space_after=6
        )
    ]
    if body:
        out.append(para(ctx, body, body_pt, ctx.c.muted, align=align))
    return tuple(out)


# ------------------------------------------------------------------------------ hero slides
def _title_block(
    ctx: Ctx,
    title: str,
    subtitle: str,
    width: float,
    height: float,
    *,
    start: float,
    minimum: float = 26,
) -> tuple[float, float]:
    sub_scale = 0.5

    def sizes(size: float) -> list[tuple[str, float]]:
        out = [(title, 1.0)]
        if subtitle:
            out.append((subtitle, sub_scale))
        return out

    title_pt = fit(sizes(start), width, height, start, minimum, space_after=10, what="title")
    sub_pt = min(24.0, max(14.0, title_pt * sub_scale + 2)) if subtitle else 0.0
    # The subtitle is measured at its own size below; shrink the title until both fit together.
    while subtitle and title_pt > minimum:
        parts = [(plain_text(title), title_pt), (plain_text(subtitle), sub_pt)]
        if estimate_height_in(parts, width - 2 * BOX_INSET, 10) <= height - 2 * BOX_INSET:
            break
        title_pt -= 1.0
        sub_pt = min(24.0, max(14.0, title_pt * sub_scale + 2))
    return title_pt, sub_pt


def hero(ctx: Ctx, slide: Slide, kind: str) -> list[Prim]:
    """Cover, section break or closing slide in the deck's title composition."""
    layout = ctx.style.title_layout if kind != "section" else "left"
    out: list[Prim] = []
    art = background_art(ctx, ctx.style.title_layout)
    if art is not None:
        out.append(art)
    title, subtitle = slide.title, slide.subtitle
    centered = layout == "center" or kind == "closing"
    if kind == "closing" and layout in ("split", "bottom"):
        centered = True
    top_text = 2.55
    if kind == "section":
        return _section(ctx, slide, out)
    if centered:
        width, height = 10.3, 3.0 if not slide.bullets else 2.4
        x = (SLIDE_W - width) / 2
        title_pt, sub_pt = _title_block(ctx, title, subtitle, width, height, start=ctx.big_pt)
        out += motif(ctx, 0.7, 0.55, 3.2, 2.2, kind=ctx.style.motif, strength=0.8)
        out += motif(
            ctx, SLIDE_W - 3.9, SLIDE_H - 2.9, 3.2, 2.2, kind=ctx.style.motif, strength=0.8
        )
        out.append(pill(ctx, SLIDE_W / 2 - 0.4, 2.35 if not slide.bullets else 1.4, 0.8, 0.08))
        top = 2.55 if not slide.bullets else 1.6
        out.append(
            TextBox(
                x,
                top,
                width,
                height,
                _hero_paras(ctx, title, subtitle, title_pt, sub_pt, "center"),
                name="title",
            )
        )
        if kind == "closing" and slide.bullets:
            out += _closing_lines(ctx, slide, x, top + height + 0.1, width)
        return out
    if layout == "bottom":
        out += motif(ctx, 0.7, 0.6, CONTENT_W, 2.8, kind=ctx.style.motif)
        width, height = 9.4, 3.1
        title_pt, sub_pt = _title_block(ctx, title, subtitle, width, height, start=ctx.big_pt)
        out.append(pill(ctx, MARGIN_X + BOX_INSET, 3.78, 0.8, 0.08))
        out.append(
            TextBox(
                MARGIN_X,
                3.95,
                width,
                height,
                _hero_paras(ctx, title, subtitle, title_pt, sub_pt, "left"),
                name="title",
            )
        )
        return out
    # left and split: text on the left, art on the right
    width, height = (7.2 if layout == "left" else 6.9), 3.2
    title_pt, sub_pt = _title_block(ctx, title, subtitle, width, height, start=ctx.big_pt)
    if layout == "split":
        panel_x, panel_w = SLIDE_W - MARGIN_X - 4.1, 4.1
        radius = ctx.radius(panel_w, 6.1, ctx.style.corner_radius * 1.4)
        out.append(
            Box(
                panel_x,
                0.7,
                panel_w,
                6.1,
                kind="round" if radius else "rect",
                gradient=Gradient(ctx.accent, ctx.accent2, 160),
                fill=ctx.accent,
                alpha=0.92 if ctx.family == "glass" else 1.0,
                radius=radius,
                shadow="soft" if ctx.shadow != "none" else "none",
                name="visual:hero-panel",
            )
        )
        ring = mix(best_on(ctx.accent), ctx.accent, 0.2)
        for index, size in enumerate((3.2, 2.2, 1.2)):
            out.append(
                Box(
                    panel_x + (panel_w - size) / 2,
                    0.7 + (6.1 - size) / 2,
                    size,
                    size,
                    kind="oval",
                    line=ring,
                    line_w=2.0 - index * 0.4,
                    line_alpha=0.65,
                    name="deco:ring",
                )
            )
    else:
        out += motif(ctx, 8.1, 0.9, 4.5, 5.7, kind=ctx.style.motif)
    out.append(pill(ctx, MARGIN_X + BOX_INSET, top_text - 0.28, 0.8, 0.08))
    out.append(
        TextBox(
            MARGIN_X,
            top_text,
            width,
            height,
            _hero_paras(ctx, title, subtitle, title_pt, sub_pt, "left"),
            name="title",
        )
    )
    return out


def _hero_paras(
    ctx: Ctx, title: str, subtitle: str, title_pt: float, sub_pt: float, align: str
) -> tuple[Para, ...]:
    out = [
        para(
            ctx,
            title,
            title_pt,
            ctx.title_color,
            bold=True,
            font="heading",
            align=align,
            space_after=10,
        )
    ]
    if subtitle:
        out.append(para(ctx, subtitle, sub_pt, ctx.c.muted, align=align))
    return tuple(out)


def _closing_lines(ctx: Ctx, slide: Slide, x: float, y: float, width: float) -> list[Prim]:
    height = max(0.9, SLIDE_H - 0.8 - y)
    size = fit(
        [(b, 1.0) for b in slide.bullets],
        width,
        height,
        18,
        12,
        space_after=4,
        what="closing lines",
    )
    paras = tuple(
        para(ctx, b, size, ctx.c.muted, align="center", space_after=4) for b in slide.bullets
    )
    return [TextBox(x, y, width, height, paras, name="closing-lines")]


def _section(ctx: Ctx, slide: Slide, out: list[Prim]) -> list[Prim]:
    width, height = 8.6, 2.7
    title_pt, sub_pt = _title_block(
        ctx, slide.title, slide.subtitle, width, height, start=ctx.big_pt - 6
    )
    text_x = MARGIN_X + (2.3 if slide.number else 0.0)
    if slide.number:
        out += number_badge(ctx, MARGIN_X + 0.1, 2.75, 1.75, slide.number)
    else:
        out.append(pill(ctx, MARGIN_X + BOX_INSET, 2.55, 0.8, 0.08))
    out += motif(ctx, 8.3, 3.7, 4.3, 3.1, kind=ctx.style.motif, strength=0.9)
    out.append(
        TextBox(
            text_x,
            2.6,
            width,
            height,
            _hero_paras(ctx, slide.title, slide.subtitle, title_pt, sub_pt, "left"),
            anchor="top",
            name="title",
        )
    )
    return out


# ----------------------------------------------------------------------------- stat tiles
def stat_grid(ctx: Ctx, slide: Slide) -> list[Prim]:
    metrics = list(slide.metrics)
    count = len(metrics)
    out: list[Prim] = []
    if count == 4:
        width = (CONTENT_W - GAP) / 2
        height = (BODY_H - 0.2 - GAP) / 2
        spots = [
            (MARGIN_X + col * (width + GAP), BODY_TOP + 0.1 + row * (height + GAP))
            for row in range(2)
            for col in range(2)
        ]
    else:
        width, xs = _row_geometry(count)
        height = 3.7
        spots = [(x, BODY_TOP + 0.45) for x in xs]
    value_h = min(1.35, height * 0.42)
    has_bar = any(m.progress is not None for m in metrics)
    bar_h = 0.14
    label_h = height - PAD - 0.2 - value_h - (0.45 if has_bar else 0.12) - 0.1
    value_pt = min(
        fit([(m.value, 1.0)], width - 2 * PAD, value_h, ctx.big_pt, 24, what="stat value")
        for m in metrics
    )
    label_pt = min(
        fit([(m.label, 1.0)], width - 2 * PAD, max(0.5, label_h), 18, 12, what="stat label")
        for m in metrics
    )
    for metric, (x, y) in zip(metrics, spots, strict=True):
        out.append(card(ctx, x, y, width, height))
        out.append(pill(ctx, x + PAD + BOX_INSET, y + PAD, 0.5, 0.07))
        out.append(
            TextBox(
                x + PAD - BOX_INSET,
                y + PAD + 0.12,
                width - 2 * (PAD - BOX_INSET),
                value_h,
                (
                    para(
                        ctx,
                        metric.value,
                        value_pt,
                        ctx.accent_text(ctx.c.card_bg),
                        bold=True,
                        font="heading",
                    ),
                ),
                anchor="middle",
                name="stat-value",
            )
        )
        out.append(
            TextBox(
                x + PAD - BOX_INSET,
                y + PAD + 0.12 + value_h,
                width - 2 * (PAD - BOX_INSET),
                max(0.5, label_h),
                (para(ctx, metric.label, label_pt, ctx.c.muted),),
                name="stat-label",
            )
        )
        if metric.progress is not None:
            out += progress_bar(
                ctx,
                x + PAD + BOX_INSET,
                y + height - PAD - bar_h,
                width - 2 * PAD - 2 * BOX_INSET,
                metric.progress,
                bar_h,
            )
    return out


# ------------------------------------------------------------------------------ icon cards
def _icon_name(card_icon: str, glyph: str) -> str:
    if card_icon and deck_icons.has_icon(card_icon):
        return card_icon
    if glyph and deck_icons.has_icon(glyph):
        return glyph
    return ""


def icon_cards(ctx: Ctx, slide: Slide) -> list[Prim]:
    cards = list(slide.cards)
    count = len(cards)
    width, xs = _row_geometry(count)
    badge = 0.95
    items = [(item.title, item.body) for item in cards]
    text_w = width - 2 * (PAD - BOX_INSET)
    full_h = 4.75 - 2 * PAD - badge - 0.2
    title_pt, body_pt = _fit_items(
        items, text_w, full_h, title_start=24, title_min=16, what="card text"
    )
    # Cards hug their text (3.4 to 4.75 in tall) and sit in the middle of the body area.
    text_h = _needed_height(items, text_w, title_pt, body_pt)
    height = max(3.4, min(4.75, 2 * PAD + badge + 0.2 + text_h + 0.2))
    top = BODY_TOP + (BODY_H - height) / 2 - 0.05
    text_y = top + PAD + badge + 0.2
    text_h = top + height - PAD - text_y
    out: list[Prim] = []
    for index, (item, x) in enumerate(zip(cards, xs, strict=True), start=1):
        out.append(card(ctx, x, top, width, height))
        icon = _icon_name(item.icon, item.glyph)
        if icon:
            out += icon_badge(ctx, icon, index, x + PAD, top + PAD, badge)
        else:
            label = item.glyph or f"{index:02d}"
            out += number_badge(ctx, x + PAD, top + PAD, badge, label)
        out.append(
            TextBox(
                x + PAD - BOX_INSET,
                text_y,
                width - 2 * (PAD - BOX_INSET),
                text_h,
                _item_paras(ctx, item.title, item.body, title_pt, body_pt),
                name="card-text",
            )
        )
    return out


# --------------------------------------------------------------------------------- process
def process(ctx: Ctx, slide: Slide) -> list[Prim]:
    steps = list(slide.steps)
    count = len(steps)
    col_w = CONTENT_W / count
    chevron_h = 1.15
    items = [(s.title, s.detail) for s in steps]
    full_h = BODY_H - 0.35 - chevron_h - 0.3
    title_pt, body_pt = _fit_items(
        items, col_w - 0.1, full_h, title_start=24, title_min=15, what="process text"
    )
    needed = _needed_height(items, col_w - 0.1, title_pt, body_pt)
    chevron_y = BODY_TOP + max(0.2, (BODY_H - (chevron_h + 0.3 + needed)) / 2 - 0.15)
    text_y = chevron_y + chevron_h + 0.3
    text_h = min(needed, BODY_BOTTOM - text_y)
    out: list[Prim] = []
    for index, step in enumerate(steps, start=1):
        x = MARGIN_X + (index - 1) * col_w
        t = (index - 1) / max(1, count - 1)
        fill = mix(ctx.accent, ctx.accent2, t)
        out.append(
            Box(
                x,
                chevron_y,
                col_w - 0.04,
                chevron_h,
                kind="pentagon" if index == 1 else "chevron",
                gradient=(
                    Gradient(fill, mix(fill, ctx.accent2, 0.25), 90)
                    if ctx.family == "glass"
                    else None
                ),
                fill=fill,
                shadow="soft" if ctx.shadow in ("soft", "lift", "glow") else "none",
                name="visual:chevron",
            )
        )
        label = f"{index:02d}"
        icon = step.icon if step.icon and deck_icons.has_icon(step.icon) else ""
        if icon:
            size = 0.5
            out.append(
                IconShape(
                    icon,
                    x + col_w / 2 - size / 2,
                    chevron_y + (chevron_h - size) / 2,
                    size,
                    best_on(fill),
                )
            )
        else:
            out.append(
                TextBox(
                    x + 0.3,
                    chevron_y,
                    col_w - 0.9,
                    chevron_h,
                    (
                        para(
                            ctx,
                            label,
                            22,
                            best_on(fill),
                            bold=True,
                            font="heading",
                            align="center",
                        ),
                    ),
                    anchor="middle",
                    inset=0.02,
                    name="step-number",
                )
            )
        out.append(
            TextBox(
                x + 0.05,
                text_y,
                col_w - 0.1,
                text_h,
                _item_paras(ctx, step.title, step.detail, title_pt, body_pt, align="center"),
                name="step-text",
            )
        )
    return out


# -------------------------------------------------------------------------------- timeline
def timeline(ctx: Ctx, slide: Slide) -> list[Prim]:
    steps = list(slide.steps)
    count = len(steps)
    col_w = CONTENT_W / count
    node = 0.62
    node_y = BODY_TOP + 0.2
    line_y = node_y + node / 2
    card_y = node_y + node + 0.35
    card_h = BODY_BOTTOM - card_y - 0.1
    title_pt, body_pt = _fit_items(
        [(s.title, s.detail) for s in steps],
        col_w - 0.3,
        card_h - 0.3,
        title_start=20,
        title_min=14,
        what="timeline text",
    )
    muted = mix(ctx.c.page_bg, ctx.c.text, 0.3)
    out: list[Prim] = [
        LineShape(
            MARGIN_X + col_w / 2,
            line_y,
            MARGIN_X + CONTENT_W - col_w / 2,
            line_y,
            muted,
            2.0,
            name="visual:timeline-line",
        )
    ]
    for index, step in enumerate(steps, start=1):
        x = MARGIN_X + (index - 1) * col_w
        cx = x + col_w / 2
        out.append(
            LineShape(
                cx, node_y + node, cx, card_y, muted, 1.25, dash=True, name="visual:connector"
            )
        )
        out += number_badge(ctx, cx - node / 2, node_y, node, str(index))
        out.append(card(ctx, x + 0.08, card_y, col_w - 0.16, card_h))
        out.append(
            TextBox(
                x + 0.08 + 0.06,
                card_y + 0.15,
                col_w - 0.28,
                card_h - 0.3,
                _item_paras(ctx, step.title, step.detail, title_pt, body_pt, align="center"),
                name="timeline-text",
            )
        )
    return out


# ------------------------------------------------------------------------------ comparison
def comparison(ctx: Ctx, slide: Slide) -> list[Prim]:
    assert slide.left is not None and slide.right is not None
    gap, head_h = 0.5, 0.75
    col_w = (CONTENT_W - gap) / 2
    top, height = BODY_TOP + 0.1, BODY_H - 0.2
    columns = (slide.left, slide.right)
    body_h = height - head_h - 0.35
    size = fit(
        [(b, 1.0) for col in columns for b in col.bullets],
        col_w - 2 * (PAD - BOX_INSET) - 0.3,
        body_h,
        24,
        14,
        space_after=8,
        what="comparison bullets",
    )
    # Fit each column on its own, then use the smaller size so both columns match.
    for col in columns:
        size = min(
            size,
            fit(
                [(b, 1.0) for b in col.bullets],
                col_w - 2 * (PAD - BOX_INSET) - 0.3,
                body_h,
                size,
                14,
                space_after=8,
                what="comparison bullets",
            ),
        )
    # Cards hug their bullets (at least 3.6 in tall) and sit in the middle of the body area.
    text_w = col_w - 2 * (PAD - BOX_INSET) - 0.3 - 2 * BOX_INSET
    needed = max(
        estimate_height_in([(plain_text(b), size) for b in col.bullets], text_w, 8.0)
        for col in columns
    )
    height = max(3.6, min(height, head_h + 0.35 + needed + 2 * BOX_INSET + 0.5))
    top = BODY_TOP + (BODY_H - height) / 2
    body_h = height - head_h - 0.35
    out: list[Prim] = []
    for index, col in enumerate(columns):
        x = MARGIN_X + index * (col_w + gap)
        head_fill = ctx.accent if index == 0 else ctx.accent2
        out.append(card(ctx, x, top, col_w, height))
        out.append(
            Box(
                x + 0.2,
                top + 0.2,
                col_w - 0.4,
                head_h - 0.1,
                kind="round",
                fill=head_fill,
                radius=(head_h - 0.1) / 2,
                name="visual:comparison-head",
            )
        )
        heading = col.heading or ("Before" if index == 0 else "After")
        out.append(
            TextBox(
                x + 0.2,
                top + 0.2,
                col_w - 0.4,
                head_h - 0.1,
                (para(ctx, heading, 20, best_on(head_fill), bold=True, align="center"),),
                anchor="middle",
                inset=0.04,
                name="comparison-heading",
            )
        )
        out.append(
            TextBox(
                x + PAD - BOX_INSET + 0.05,
                top + head_h + 0.35,
                col_w - 2 * (PAD - BOX_INSET) - 0.1,
                body_h,
                tuple(
                    para(ctx, b, size, ctx.c.text, space_after=8, bullet=True) for b in col.bullets
                ),
                name="comparison-bullets",
            )
        )
    vs = 0.62
    out += number_badge(ctx, SLIDE_W / 2 - vs / 2, top + height / 2 - vs / 2, vs, "vs")
    return out


# ---------------------------------------------------------------------------------- quote
def quote(ctx: Ctx, slide: Slide) -> list[Prim]:
    out: list[Prim] = []
    x = 1.6
    width = SLIDE_W - x - 1.1
    out.append(
        Box(
            0.95,
            1.55,
            0.09,
            3.9,
            kind="round",
            fill=ctx.accent,
            radius=0.045,
            name="visual:quote-bar",
        )
    )
    out.append(
        TextBox(
            0.85,
            0.55,
            1.6,
            1.8,
            (para(ctx, "“", 88, ctx.accent_text(), bold=True, font="heading"),),
            anchor="top",
            inset=0.02,
            name="quote-mark",
        )
    )
    if slide.title:
        out.append(
            TextBox(
                x,
                0.6,
                width,
                0.6,
                (para(ctx, slide.title, 16, ctx.c.muted, bold=True),),
                inset=0.02,
                name="quote-title",
            )
        )
    text = slide.quote
    size = fit([(text, 1.0)], width, 3.5, 36, 20, what="quote")
    out.append(
        TextBox(
            x,
            1.7,
            width,
            3.5,
            (para(ctx, text, size, ctx.title_color, italic=True, font="heading"),),
            anchor="middle",
            name="quote-text",
        )
    )
    if slide.by:
        out.append(pill(ctx, x + 0.02, 5.62, 0.5, 0.06))
        out.append(
            TextBox(
                x,
                5.75,
                width,
                0.6,
                (para(ctx, slide.by, 18, ctx.c.muted),),
                inset=0.02,
                name="quote-by",
            )
        )
    return out


# ------------------------------------------------------------------------------ image + text
def image_text(ctx: Ctx, slide: Slide) -> list[Prim]:
    assert slide.image is not None
    img_w = 5.7
    top, height = BODY_TOP + 0.1, BODY_H - 0.2
    out: list[Prim] = []
    radius = ctx.radius(img_w, height, max(ctx.style.corner_radius, 10))
    if slide.image.src:
        out.append(
            ImageShape(
                MARGIN_X,
                top,
                img_w,
                height,
                src=slide.image.src,
                alt=slide.image.alt,
                radius=radius,
            )
        )
    else:
        out.append(card(ctx, MARGIN_X, top, img_w, height))
        out.append(
            IconShape(
                "layers", MARGIN_X + img_w / 2 - 0.45, top + height / 2 - 0.8, 0.9, ctx.accent
            )
        )
        out.append(
            TextBox(
                MARGIN_X,
                top + height / 2 + 0.2,
                img_w,
                0.9,
                (para(ctx, f"Image: {slide.image.alt}", 14, ctx.c.muted, align="center"),),
                name="image-placeholder",
            )
        )
    text_x = MARGIN_X + img_w + 0.55
    text_w = SLIDE_W - MARGIN_X - text_x
    lead = slide.subtitle
    items = [(b, 1.0) for b in slide.bullets]
    if lead:
        items.insert(0, (lead, 1.0))
    size = ctx.theme.sizes.slide_body_pt
    if items:
        size = fit(
            items,
            text_w - 0.3,
            height,
            ctx.theme.sizes.slide_body_pt,
            14,
            space_after=10,
            what="text",
        )
    paras: list[Para] = []
    if lead:
        paras.append(para(ctx, lead, size, ctx.c.muted, space_after=10))
    paras += [para(ctx, b, size, ctx.c.text, space_after=10, bullet=True) for b in slide.bullets]
    if paras:
        out.append(
            TextBox(text_x, top, text_w, height, tuple(paras), anchor="middle", name="image-text")
        )
    return out


# -------------------------------------------------------------------------------- chart
def chart(ctx: Ctx, slide: Slide) -> list[Prim]:
    assert slide.chart is not None
    data = slide.chart
    top, height = BODY_TOP + 0.1, BODY_H - 0.2
    side_w = 4.2 if slide.bullets else 0.0
    chart_w = CONTENT_W - (side_w + GAP if slide.bullets else 0.0)
    out: list[Prim] = [card(ctx, MARGIN_X, top, chart_w, height)]
    caption_h = 0.5 if slide.subtitle else 0.0
    out.append(
        ChartShape(
            data.type,
            MARGIN_X + 0.25,
            top + 0.2,
            chart_w - 0.5,
            height - 0.4 - caption_h,
            tuple(plain_text(cat) for cat in data.categories),
            tuple(Series(plain_text(s.name), tuple(s.values)) for s in data.series),
            ctx.series_colors(),
            ctx.c.text,
            mix(ctx.c.page_bg, ctx.c.text, 0.18),
            ctx.theme.fonts.body_font,
            14.0,
        )
    )
    if slide.subtitle:
        out.append(
            TextBox(
                MARGIN_X + 0.2,
                top + height - caption_h - 0.1,
                chart_w - 0.4,
                caption_h,
                (para(ctx, slide.subtitle, 14, ctx.c.muted, italic=True),),
                anchor="middle",
                inset=0.04,
                name="chart-caption",
            )
        )
    if slide.bullets:
        x = MARGIN_X + chart_w + GAP
        out.append(card(ctx, x, top, side_w, height))
        size = fit(
            [(b, 1.0) for b in slide.bullets],
            side_w - 0.5,
            height - 0.5,
            18,
            13,
            space_after=10,
            what="chart notes",
        )
        out.append(
            TextBox(
                x + 0.2,
                top + 0.25,
                side_w - 0.4,
                height - 0.5,
                tuple(
                    para(ctx, b, size, ctx.c.text, space_after=10, bullet=True)
                    for b in slide.bullets
                ),
                anchor="middle",
                name="chart-notes",
            )
        )
    return out


# ------------------------------------------------------------------------------- registry
SCENE_BUILDERS: dict[str, Builder] = {
    "stat_grid": stat_grid,
    "icon_cards": icon_cards,
    "process": process,
    "image_text": image_text,
    "chart": chart,
}
# Classic layouts that use a scene builder when the deck uses a library theme.
FAMILY_BUILDERS: dict[str, Builder] = {
    "stat_callout": stat_grid,
    "cards": icon_cards,
    "timeline": timeline,
    "comparison": comparison,
    "quote": quote,
}

__all__ = ["FAMILY_BUILDERS", "SCENE_BUILDERS", "chart", "hero", "icon_cards", "process"]
