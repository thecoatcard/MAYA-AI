"""Which kind of file a request needs: decided in one place, never a silent default to docx.

The model chooses the file type from the request (the document prompt says so). This module is
the deterministic cross-check: :func:`detect_file_type` reads the user's own words and returns
the type they point to (or ``None`` when the request does not say), and :func:`needs_project`
tells a multi-file website request from a single web page. ``TEXT_TYPES`` are the types written
as plain text (the model fills ``text``); the office types keep their structured specs.
"""

from __future__ import annotations

import re

OFFICE_TYPES: tuple[str, ...] = ("pdf", "docx", "xlsx", "pptx")
# Plain-text deliverables: extension -> short label. Code files keep their correct extension.
TEXT_TYPES: dict[str, str] = {
    "md": "Markdown",
    "txt": "plain text",
    "html": "single-file web page",
    "csv": "CSV table",
    "json": "JSON",
    "py": "Python",
    "js": "JavaScript",
    "ts": "TypeScript",
    "jsx": "React JSX",
    "tsx": "React TSX",
    "css": "CSS",
    "sql": "SQL",
    "sh": "shell script",
    "ps1": "PowerShell",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
    "cs": "C#",
    "go": "Go",
    "rs": "Rust",
    "rb": "Ruby",
    "php": "PHP",
    "yaml": "YAML",
    "xml": "XML",
}
ALL_TYPES: tuple[str, ...] = (*OFFICE_TYPES, *TEXT_TYPES)
MAX_TEXT_CHARS = 400_000

_RULES: tuple[tuple[str, str], ...] = (
    # Most specific first: a named language or format beats a generic word.
    (r"\bpython\b|\.py\b|\bpy script\b", "py"),
    (r"\btypescript\b|\.ts\b", "ts"),
    (r"\bjavascript\b|\bnode script\b|\.js\b|\bjs file\b", "js"),
    (r"\bsql\b", "sql"),
    (r"\bbash\b|\bshell script\b|\.sh\b", "sh"),
    (r"\bpowershell\b|\.ps1\b", "ps1"),
    (r"\bcss (?:file|stylesheet)\b|\bstylesheet\b|\.css\b", "css"),
    (r"\bcsv\b", "csv"),
    (r"\bjson\b", "json"),
    (r"\bmarkdown\b|\.md\b|\breadme\b", "md"),
    (r"\bplain text\b|\btext file\b|\.txt\b", "txt"),
    (
        r"\bpowerpoint\b|\bpptx?\b|\bslides?\b|\bslide deck\b|\bpresentation\b|\bpitch deck\b",
        "pptx",
    ),
    (r"\bexcel\b|\bspreadsheet\b|\bxlsx\b|\bworkbook\b", "xlsx"),
    (r"\bpdf\b", "pdf"),
    (r"\bword (?:doc|document)\b|\bdocx\b", "docx"),
    (r"\bweb ?page\b|\bhtml (?:page|file|document)\b|\blanding page\b|\.html\b|\bhtml\b", "html"),
)
_PROJECT = re.compile(
    r"\bweb ?site\b|\bmulti[- ]?(?:page|file)\b|\bseparate (?:css|js|files?)\b|"
    r"\bvanilla\s+(?:html|js|javascript|web ?(?:page|site|app))|\bproject folder\b|"
    r"\bweb ?app(?:lication)?\b|\bportfolio site\b|"
    r"\bhtml,? (?:css|and css)(?:,| and| &)? ?(?:js|javascript)\b",
    re.IGNORECASE,
)
_SINGLE_FILE = re.compile(r"\bsingle[- ]file\b|\bone file\b|\bone[- ]pager\b|\bsingle html\b", re.I)


def needs_project(text: str | None) -> bool:
    """True for a multi-file website request (HTML + CSS + JS + assets): it is delivered as a
    project (a .zip, or a folder when one is connected), not as one file."""
    value = text or ""
    return bool(_PROJECT.search(value)) and not _SINGLE_FILE.search(value)


def detect_file_type(text: str | None) -> str | None:
    """The file type the user's words point to, ``"project"`` for a multi-file website, or
    ``None`` when the request does not say (the model then decides from what it needs)."""
    value = text or ""
    if needs_project(value):
        return "project"
    if _SINGLE_FILE.search(value) and re.search(r"web ?(?:site|page)|html|landing", value, re.I):
        return "html"
    for pattern, kind in _RULES:
        if re.search(pattern, value, re.IGNORECASE):
            return kind
    return None


def type_guidance() -> str:
    """The prompt rule: choose the file type from what the request needs."""
    return (
        "Choose file_type from what the request actually needs, in this order: the type the "
        "user named (a .py script is py, a web page is html, 'markdown' is md, a table to import "
        "is csv, data is json); otherwise the natural form (slides -> pptx, numbers/tables to "
        "work with -> xlsx, formal print-ready report -> pdf, an editable letter or memo -> "
        "docx, quick notes or a README -> md or txt, a script or program -> its language's "
        "extension such as py, js, ts, sql, css, sh). Never default to docx: use docx only when "
        "the user wants a Word document. Allowed: " + ", ".join(ALL_TYPES) + ".\n"
        "For md, txt, html, csv, json and every code type put the COMPLETE file in 'text' "
        "(never in blocks or slides) and name the file with the matching extension. html is ONE "
        "self-contained page (inline <style> and <script>, no CDN unless the user asked). A "
        "code file must be complete and runnable, with no placeholders. csv uses a header row "
        "and commas; json must parse."
    )
