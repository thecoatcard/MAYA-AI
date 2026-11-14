"""The written result of a run: verification of the model's text and the parts made by code.

The model writes only the body. Code adds what must not depend on a model: the honest status line,
contradictions, the evidence table, the confidence and limits section and the numbered source list
(only sources that were read). ``verify_body`` checks every claim sentence: it needs a citation to a
source that was read, or it is marked as an inference; a number that the cited sources do not
contain is flagged. Nothing is silently kept and nothing is silently removed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.research.fetcher import reason_label
from app.research.models import Read, Skip
from app.research.schemas import Synthesis

_MARKER = re.compile(r"\[(\d{1,3})\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9])")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_INFERENCE = re.compile(r"^\W*inference\b", re.IGNORECASE)
INFERENCE_TAG: Final = " [inference: no source cited]"
FIGURE_TAG: Final = " [figure not found in the cited source]"
_MIN_CLAIM_WORDS: Final = 7


@dataclass(slots=True)
class Verification:
    text: str
    inferences: int = 0
    stripped_markers: int = 0
    unmatched_figures: list[str] = field(default_factory=list)
    cited: set[int] = field(default_factory=set)


def _norm_figure(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def _significant(token: str) -> bool:
    digits = _norm_figure(token)
    return len(digits.replace(".", "")) >= 2 and not (
        len(digits) == 4 and digits.isdigit() and digits[:2] in ("19", "20")
    )


def verify_body(body: str, reads: Sequence[Read]) -> Verification:
    """Check each claim sentence of ``body`` against the sources that were read."""
    by_index = {r.index: r for r in reads}
    texts = {i: _norm_text(r.source.text) for i, r in by_index.items()}
    result = Verification(text="")
    lines_out: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ">", "```", "---")):
            lines_out.append(line)
            continue
        prefix = line[: len(line) - len(line.lstrip())]
        bullet = re.match(r"^([-*+]|\d+[.)])\s+", stripped)
        lead = ""
        content = stripped
        if bullet is not None:
            lead = stripped[: bullet.end()]
            content = stripped[bullet.end() :]
        sentences = _SENTENCE_SPLIT.split(content)
        fixed = [_check_sentence(s, by_index, texts, result) for s in sentences]
        lines_out.append(prefix + lead + " ".join(fixed))
    result.text = "\n".join(lines_out)
    return result


def _norm_text(text: str) -> str:
    return text.lower().replace(",", "")


def _check_sentence(
    sentence: str, by_index: dict[int, Read], texts: dict[int, str], result: Verification
) -> str:
    def _valid(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number in by_index:
            return match.group(0)
        result.stripped_markers += 1
        return ""

    cleaned = _MARKER.sub(_valid, sentence).replace("  ", " ")
    markers = [int(m) for m in _MARKER.findall(cleaned)]
    if markers:
        result.cited.update(markers)
        missing = [
            t
            for t in _NUMBER.findall(_MARKER.sub("", cleaned))
            if _significant(t) and not any(_norm_figure(t) in texts[i] for i in markers)
        ]
        if missing:
            result.unmatched_figures.extend(missing)
            return cleaned.rstrip() + FIGURE_TAG
        return cleaned
    if _INFERENCE.match(cleaned):
        result.inferences += 1
        return cleaned
    words = len(cleaned.split())
    if words >= _MIN_CLAIM_WORDS or (_NUMBER.search(cleaned) and words >= 3):
        result.inferences += 1
        return cleaned.rstrip() + INFERENCE_TAG
    return cleaned


def markers_in(text: str) -> set[int]:
    return {int(m) for m in _MARKER.findall(text)}


def _cell(text: str, limit: int = 240) -> str:
    one = " ".join(text.split()).replace("|", "/")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _refs(indexes: Sequence[int]) -> str:
    return ", ".join(f"[{i}]" for i in indexes)


def confidence_label(domains: int) -> str:
    return "high" if domains >= 3 else "medium" if domains == 2 else "low (one site)"


def evidence_table(synthesis: Synthesis, reads: Sequence[Read], limit: int = 15) -> str:
    domain_of = {r.index: r.source.domain for r in reads}
    rows: list[str] = []
    for finding in synthesis.findings[:limit]:
        sources = sorted({i for i in finding.sources if i in domain_of})
        if not finding.claim.strip() or not sources:
            continue
        sites = {domain_of[i] for i in sources}
        rows.append(
            f"| {_cell(finding.claim)} | {_refs(sources)} | {len(sites)} site(s): "
            f"{confidence_label(len(sites))} |"
        )
    if not rows:
        return ""
    return "\n".join(["| Claim | Sources | Support |", "| --- | --- | --- |", *rows])


def contradictions_block(synthesis: Synthesis, reads: Sequence[Read]) -> str:
    valid = {r.index for r in reads}
    parts: list[str] = []
    for item in synthesis.contradictions:
        positions = [
            f"- {_cell(p.claim, 400)} {_refs(sorted({i for i in p.sources if i in valid}))}"
            for p in item.positions
            if p.claim.strip() and any(i in valid for i in p.sources)
        ]
        if len(positions) >= 2 and item.topic.strip():
            parts.append(f"**{_cell(item.topic, 200)}**\n" + "\n".join(positions))
    return "\n\n".join(parts)


def skip_summary(skips: Sequence[Skip]) -> str:
    counts = Counter(s.code for s in skips)
    return ", ".join(f"{n} {reason_label(code)}" for code, n in counts.most_common())


def status_line(
    reads: Sequence[Read], skips: Sequence[Skip], *, not_reached: int, min_domains: int
) -> str:
    domains = len({r.source.domain for r in reads})
    line = f"Read {len(reads)} sources from {domains} sites ({len(skips)} skipped)."
    if skips:
        line += f" Skipped: {skip_summary(skips)}."
    if domains < min_domains:
        line += (
            f" Fewer than the {min_domains} sites I aimed for could be read; "
            "this result rests on what is listed below, not on more."
        )
    if not_reached:
        line += f" {not_reached} more pages were found but not reached (time or call limit)."
    return line


def limits_block(
    reads: Sequence[Read],
    skips: Sequence[Skip],
    verification: Verification,
    *,
    stopped: str,
    flagged_pages: int,
    advisory_unsupported: bool,
    rounds: int,
) -> str:
    domains = len({r.source.domain for r in reads})
    dated = sum(1 for r in reads if r.note.date or r.source.published)
    lines = [
        f"- Based on {len(reads)} pages from {domains} sites, read {rounds} search round(s). "
        "Search results and pages came from public sites and are not independently verified.",
        f"- {dated} of {len(reads)} sources state a date; undated ones may be out of date.",
    ]
    if skips:
        lines.append(f"- Pages that could not be read: {skip_summary(skips)}.")
    if verification.inferences:
        lines.append(
            f"- {verification.inferences} sentence(s) are marked as inference: reasoning or "
            "statements without a source."
        )
    if verification.unmatched_figures:
        figures = ", ".join(dict.fromkeys(verification.unmatched_figures[:8]))
        lines.append(f"- Figures not found in the cited source (check before use): {figures}.")
    if verification.stripped_markers:
        lines.append(
            f"- {verification.stripped_markers} citation(s) pointed to no source I read and "
            "were removed."
        )
    if flagged_pages:
        lines.append(
            f"- {flagged_pages} page(s) tried to give instructions to an AI; they were ignored "
            "and not used as sources."
        )
    if advisory_unsupported:
        lines.append(
            "- An automatic check found that some details may not be backed by the sources: "
            "treat them as unverified."
        )
    if stopped == "time_cap":
        lines.append("- The time cap was reached; the result is written from what was read.")
    elif stopped == "budget":
        lines.append("- The model-call cap was reached; fewer pages were analysed than found.")
    return "\n".join(lines)


def sources_block(reads: Sequence[Read]) -> str:
    rows = []
    for r in reads:
        date = r.note.date or r.source.published or "no date"
        rows.append(
            f"{r.index}. {_cell(r.source.title, 160)} - {r.source.domain} - {date[:10]} - "
            f"{r.source.final_url}"
        )
    return "\n".join(rows)


def assemble(
    *,
    status: str,
    body: str,
    synthesis: Synthesis,
    reads: Sequence[Read],
    limits_text: str,
) -> str:
    parts = [status, "", body.strip()]
    contradictions = contradictions_block(synthesis, reads)
    if contradictions:
        parts += ["", "## Where sources disagree", contradictions]
    table = evidence_table(synthesis, reads)
    if table:
        parts += ["", "## Evidence", table]
    parts += ["", "## Confidence and limits", limits_text, "", "## Sources", sources_block(reads)]
    return "\n".join(parts).strip() + "\n"
