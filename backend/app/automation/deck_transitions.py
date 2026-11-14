"""Slide transitions written as PresentationML ``p:transition`` elements.

Only transition types whose XML is part of the base ECMA-376 schema are offered, so no
``mc:AlternateContent`` wrapper is needed and every PowerPoint, Keynote and LibreOffice version
reads them::

    <p:transition spd="med"><p:fade/></p:transition>
    <p:transition spd="med"><p:push dir="u"/></p:transition>
    <p:transition spd="med"><p:wipe dir="l"/></p:transition>
    <p:transition spd="med"><p:cover dir="l"/></p:transition>

In a slide the element sits after ``p:clrMapOvr`` and before ``p:timing`` (``p:sld`` content
order: ``cSld``, ``clrMapOvr``, ``transition``, ``timing``, ``extLst``). Morph, entrance and object
animations are deliberately not offered: they cannot be verified without PowerPoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lxml import etree

TRANSITION_NAMES: tuple[str, ...] = ("none", "fade", "push", "wipe", "cover")
SPEEDS: tuple[str, ...] = ("slow", "med", "fast")
_DIRECTIONAL = {"push": "u", "wipe": "l", "cover": "l"}  # element -> direction attribute

# Children of p:sld in schema order: what must come before and after p:transition.
SLIDE_CHILD_ORDER: tuple[str, ...] = ("cSld", "clrMapOvr", "transition", "timing", "extLst")

_SYNONYMS: dict[str, str] = {
    "": "",
    "off": "none",
    "no": "none",
    "nothing": "none",
    "dissolve": "fade",
    "crossfade": "fade",
    "cross-fade": "fade",
    "fade-through-black": "fade",
    "slide": "push",
    "slide-in": "push",
    "swipe": "wipe",
    "reveal": "wipe",
    "overlay": "cover",
}


def canonical_transition(value: object) -> str | None:
    """The canonical transition name for ``value`` (case, synonyms folded), or ``None`` when it is
    not a transition this engine can write. An empty value is ``""`` (use the theme default)."""
    if value is None:
        return ""
    text = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    text = _SYNONYMS.get(text, text)
    if text == "" or text in TRANSITION_NAMES:
        return text
    return None


def build_transition(name: str, *, speed: str = "med") -> etree._Element | None:
    """The ``p:transition`` element for ``name`` (``None`` for ``none``)."""
    if name == "none" or name == "":
        return None
    if name not in TRANSITION_NAMES:
        raise ValueError(f"unknown transition '{name}'; allowed: {', '.join(TRANSITION_NAMES)}")
    from lxml import etree  # local: this module is imported by the light-weight document model
    from pptx.oxml.ns import qn

    element = etree.Element(qn("p:transition"))
    element.set("spd", speed if speed in SPEEDS else "med")
    child = etree.SubElement(element, qn(f"p:{name}"))
    if name in _DIRECTIONAL:
        child.set("dir", _DIRECTIONAL[name])
    return element


def apply_transition(slide: Any, name: str, *, speed: str = "med") -> bool:
    """Write ``name`` into ``slide`` in the schema position (after ``p:clrMapOvr`` and before
    ``p:timing``). Replaces an existing transition. Returns ``True`` when a transition was
    written."""
    from pptx.oxml.ns import qn

    root = slide._element
    for old in root.findall(qn("p:transition")):
        root.remove(old)
    element = build_transition(name, speed=speed)
    if element is None:
        return False
    anchor = root.find(qn("p:clrMapOvr"))
    if anchor is None:
        anchor = root.find(qn("p:cSld"))
    if anchor is not None:
        anchor.addnext(element)
    else:  # pragma: no cover - a python-pptx slide always has p:cSld
        root.insert(0, element)
    return True


def slide_child_order(slide_xml_root: Any) -> list[str]:
    """Local names of the direct children of a ``p:sld`` element (for order checks)."""
    from lxml import etree

    return [etree.QName(child).localname for child in slide_xml_root]


def order_is_valid(names: list[str]) -> bool:
    """True when ``names`` follow :data:`SLIDE_CHILD_ORDER` (each name at most once)."""
    positions = [SLIDE_CHILD_ORDER.index(n) for n in names if n in SLIDE_CHILD_ORDER]
    return positions == sorted(positions) and len(set(positions)) == len(positions)


__all__ = [
    "SLIDE_CHILD_ORDER",
    "SPEEDS",
    "TRANSITION_NAMES",
    "apply_transition",
    "build_transition",
    "canonical_transition",
    "order_is_valid",
    "slide_child_order",
]
