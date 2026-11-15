"""Candidate pages: merge search hits, score them, pick a varied set.

The score is a transparent heuristic (authority of the site, recency, how many queries returned the
page, how much of the topic its title and snippet cover). Spam-looking pages and file types that
cannot be read are dropped. The pick takes the best page of every site first and a second page of a
site only afterwards, so a run reaches as many different sites as the search found.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

from app.research.models import Candidate, site_of
from app.search.chain import normalise_url
from app.search.models import SearchHit

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SKIP_SITES: Final = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "pinterest.com",
        "linkedin.com",
        "reddit.com",
        "quora.com",
        "amazon.com",
        "ebay.com",
    }
)
_BAD_EXTENSIONS: Final = (
    ".zip",
    ".rar",
    ".exe",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".mp4",
    ".mp3",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
)
_THIN_PATHS: Final = ("/tag/", "/tags/", "/category/", "/categories/", "/search", "/author/")
_AUTHORITY_SITES: Final = frozenset(
    {
        "wikipedia.org",
        "nature.com",
        "science.org",
        "who.int",
        "un.org",
        "worldbank.org",
        "imf.org",
        "oecd.org",
        "europa.eu",
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "britannica.com",
        "nih.gov",
        "arxiv.org",
        "ieee.org",
        "acm.org",
    }
)
_SPAM_TITLE = re.compile(
    r"\b(top\s*\d+\s+best|coupon|promo code|casino|buy now|click here)\b", re.I
)


def terms_of(text: str) -> frozenset[str]:
    return frozenset(w for w in (m.lower() for m in _WORD.findall(text)) if len(w) > 2)


def usable(url: str) -> bool:
    """False for a URL that is not a readable public page (scheme, file type, social site)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if site_of(url) in _SKIP_SITES:
        return False
    return not parts.path.lower().endswith(_BAD_EXTENSIONS)


def merge_hits(found: dict[str, Candidate], hits: Iterable[SearchHit]) -> int:
    """Add hits to ``found`` keyed by canonical URL; a page returned again counts one more hit.
    Returns how many new pages were added."""
    added = 0
    for hit in hits:
        if not hit.url or not usable(hit.url):
            continue
        key = normalise_url(hit.url)
        existing = found.get(key)
        if existing is not None:
            existing.hits += 1
            if not existing.snippet and hit.snippet:
                existing.snippet = hit.snippet
            continue
        found[key] = Candidate(
            url=hit.url,
            title=hit.title or "",
            snippet=hit.snippet or "",
            published=hit.published,
            domain=site_of(hit.url),
        )
        added += 1
    return added


def _year(published: str | None) -> int | None:
    match = re.search(r"(19|20)\d{2}", published or "")
    return int(match.group(0)) if match else None


def score_candidate(
    candidate: Candidate, topic: frozenset[str], now: datetime | None = None
) -> float:
    """Higher is better. Authority, on-topic coverage, recency and repeated hits."""
    now = now or datetime.now(UTC)
    score = 0.0
    domain = candidate.domain
    parts = urlsplit(candidate.url)
    if domain.endswith((".gov", ".edu", ".int")) or ".gov." in domain or ".ac." in domain:
        score += 3.0
    elif domain in _AUTHORITY_SITES:
        score += 2.5
    elif domain.endswith(".org"):
        score += 1.0
    if topic:
        seen = terms_of(f"{candidate.title} {candidate.snippet}")
        score += 3.0 * len(topic & seen) / len(topic)
    year = _year(candidate.published)
    if year is not None:
        age = now.year - year
        score += 1.0 if age <= 1 else 0.5 if age <= 3 else 0.0
    score += 0.5 * min(candidate.hits - 1, 4)
    path = parts.path.lower()
    if any(p in path for p in _THIN_PATHS) or "?s=" in candidate.url:
        score -= 3.0
    if _SPAM_TITLE.search(candidate.title):
        score -= 2.0
    if not candidate.title.strip():
        score -= 0.5
    return score


def rank(found: Iterable[Candidate], topic_text: str) -> list[Candidate]:
    topic = terms_of(topic_text)
    ranked = list(found)
    for candidate in ranked:
        candidate.score = score_candidate(candidate, topic)
    ranked.sort(key=lambda c: (-c.score, c.url))
    return ranked


def pick(
    ranked: Sequence[Candidate],
    limit: int,
    *,
    per_domain: int = 2,
    taken: frozenset[str] = frozenset(),
    domain_counts: dict[str, int] | None = None,
) -> list[Candidate]:
    """Up to ``limit`` candidates, best page per site first, then second pages (at most
    ``per_domain`` per site, counting ``domain_counts``: pages of a site already tried).
    ``taken`` holds canonical URLs already read or tried."""
    chosen: list[Candidate] = []
    counts: dict[str, int] = dict(domain_counts or {})
    for round_number in range(per_domain):
        for candidate in ranked:
            if len(chosen) >= limit:
                return chosen
            if normalise_url(candidate.url) in taken or candidate in chosen:
                continue
            if counts.get(candidate.domain, 0) != round_number:
                continue
            if candidate.score < -2.0:
                continue  # clear spam or a thin listing page: never worth a request
            counts[candidate.domain] = round_number + 1
            chosen.append(candidate)
    return chosen
