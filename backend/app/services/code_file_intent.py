"""Put CODE that is already in the conversation into a file, deterministically (no model call).

Owner bug: after MAYA showed a Python script, "give in a file" and then "give main.py file" produced
``main.txt`` twice. The file tools had no way to know the language or the name, so a model guess
(plain text) won. This module decides the name, the type and the content from the conversation:

- The content is the last code block the assistant wrote, verbatim (fences removed). Nothing is
  rewritten by a model.
- The name: a file name in the current message wins (``main.py``); else one the user named in an
  earlier message; else the language of the code block (python -> ``main.py``, a Java class ->
  ``<Class>.java``). Code never becomes ``.txt``; plain text is only for a block that is not code.
- Safety: only extensions of the inline allowlist (``INLINE_FILE_EXTENSIONS``) are produced, so no
  shell, batch, PowerShell or executable file is ever created here. A request that names such a
  type, or any other deliverable (PDF, Word, slides, Notion, mail), is not handled by this module.

It acts only on a SHORT delivery request ("give in a file", "isko main.py me save kar do"); a
request that carries its own topic is a new job for the normal flow.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.services.inline_files import INLINE_FILE_EXTENSIONS, InlineFileError, sanitize_inline_name

MAX_REQUEST_TOKENS: Final = 12
_FENCE = re.compile(r"(?:^|\n)[ \t]*```([\w+#.\-]*)[^\S\n]*\n(.*?)\n?[ \t]*```", re.DOTALL)
_NAME = re.compile(r"(?<![\w.])([A-Za-z0-9][\w\-]{0,60})\.([A-Za-z0-9]{1,5})\b")
_BARE_EXT = re.compile(r"(?<![\w])\.([A-Za-z0-9]{1,5})\b")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

_LANGUAGE_EXT: Final[dict[str, str]] = {
    "python": "py",
    "py": "py",
    "python3": "py",
    "javascript": "js",
    "js": "js",
    "node": "js",
    "typescript": "ts",
    "ts": "ts",
    "tsx": "tsx",
    "jsx": "jsx",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cs": "cs",
    "csharp": "cs",
    "c#": "cs",
    "go": "go",
    "golang": "go",
    "rust": "rs",
    "rs": "rs",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yml",
    "xml": "xml",
    "toml": "toml",
    "ini": "ini",
    "csv": "csv",
    "markdown": "md",
    "md": "md",
    "text": "txt",
    "txt": "txt",
    "plaintext": "txt",
}
# A named language in the request ("java me file de do", "as a javascript file").
_REQUEST_LANGUAGES: Final = {
    word: ext
    for word, ext in _LANGUAGE_EXT.items()
    if word not in {"c", "go", "text", "node", "md", "ts", "js", "py", "rs", "cs"}
}
_OTHER_DELIVERABLE = re.compile(
    r"\b(?:pdf|docx?|word|pptx?|power\s*point|slides?|deck|xlsx?|excel|sheet|notion|e-?mail|"
    r"mail|zip|image|png|jpg)\b|\.(?:pdf|docx|pptx|xlsx|zip)\b"
)
_FILE_ASK = re.compile(r"\b(?:files?|download\w*|save|saved|saving)\b|फाइल")
# Words a delivery request may be made of. Anything else is a topic of its own (a new job).
_FILLER: Final = frozenset(
    "give send provide get gimme want need dena de do dedo de-do bhej bhejo me mein mai main kar "
    "karo kardo krdo save file files download in into as a an the it this that isko ise isey ye "
    "yeh same code script program above last previous pls please plz bhai bro just only ek one "
    "my to for of now then also and or with name named called wala wali naam se yahi uska iska "
    "use put make bana banao banado convert export keep store hai h ok okay yes haan han ji".split()
)
_HINGLISH = re.compile(r"\b(?:me|mein|mai|kar|karo|kardo|do|dedo|isko|ise|ye|yeh|bhej\w*)\b")

_SHAPES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"^\s*(?:def |class \w+|import \w+|from \w+ import |if __name__)", re.M), "py"),
    (re.compile(r"^\s*#include\s*[<\"]", re.M), "cpp"),
    (re.compile(r"\b(?:public\s+(?:static\s+)?class|public\s+static\s+void\s+main)\b"), "java"),
    (re.compile(r"^\s*(?:package\s+main\b|func\s+\w+\()", re.M), "go"),
    (re.compile(r"\b(?:console\.log|const\s+\w+\s*=|let\s+\w+\s*=|function\s+\w+\()"), "js"),
    (re.compile(r"^\s*(?:SELECT|INSERT|CREATE\s+TABLE|UPDATE)\b", re.I | re.M), "sql"),
    (re.compile(r"<!doctype html|<html\b", re.I), "html"),
)


@dataclass(frozen=True, slots=True)
class CodeBlock:
    language: str
    code: str


@dataclass(frozen=True, slots=True)
class CodeFile:
    """What to write: a safe name, the exact code, and its line count."""

    name: str
    content: str
    lines: int
    hinglish: bool


def code_blocks(text: str) -> list[CodeBlock]:
    """The fenced code blocks of one message, in order, without their fences."""
    return [
        CodeBlock(match.group(1).lower(), match.group(2))
        for match in _FENCE.finditer(text or "")
        if match.group(2).strip()
    ]


def _named_file(text: str) -> tuple[str, str] | None:
    """``(stem, extension)`` of the last ``name.ext`` in ``text`` whose extension looks like a
    file extension of a code or text file (an allowlisted one, or one a refusal must catch)."""
    found: tuple[str, str] | None = None
    for match in _NAME.finditer(text or ""):
        stem, ext = match.group(1), match.group(2).lower()
        if ext in INLINE_FILE_EXTENSIONS or ext in _REFUSED_EXTENSIONS:
            found = (stem, ext)
    return found


# A named type this module must not create: the request is left to the normal flow, which has its
# own rules (never widened here).
_REFUSED_EXTENSIONS: Final = frozenset(
    "sh bash zsh bat cmd ps1 exe dll com msi vbs jar apk app scr php rb pl lua".split()
)


def _last_assistant_code(assistant_texts: Sequence[str]) -> list[CodeBlock]:
    """The code blocks of the newest of the last three assistant messages that has any."""
    for text in reversed(list(assistant_texts)[-3:]):
        blocks = code_blocks(text)
        if blocks:
            return blocks
    return []


def _guess_extension(code: str) -> str | None:
    for pattern, ext in _SHAPES:
        if pattern.search(code):
            return ext
    return None


def _java_stem(code: str) -> str:
    match = re.search(r"\bpublic\s+(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)", code)
    if match is None:
        match = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)", code)
    return match.group(1) if match else "Main"


def _default_stem(ext: str, code: str) -> str:
    if ext == "java":
        return _java_stem(code)
    return {"html": "index", "css": "styles", "sql": "query"}.get(ext, "main")


def _is_delivery_request(request: str) -> bool:
    """True for a short message that only asks for the earlier content as a file."""
    lower = " ".join((request or "").split()).lower()
    if not lower or _OTHER_DELIVERABLE.search(lower):
        return False
    stripped = _NAME.sub(" ", lower)
    stripped = _BARE_EXT.sub(" ", stripped)
    tokens = [t for t in _TOKEN.findall(stripped)]
    if len(tokens) > MAX_REQUEST_TOKENS:
        return False
    named = _named_file(lower) is not None or _BARE_EXT.search(lower) is not None
    if not (_FILE_ASK.search(lower) or named):
        return False
    languages = set(_REQUEST_LANGUAGES) | set(_LANGUAGE_EXT)
    return all(token in _FILLER or token in languages for token in tokens)


def plan_code_file(
    request: str, user_history: Sequence[str], assistant_texts: Sequence[str]
) -> CodeFile | None:
    """The file to write for ``request``, or ``None`` when this module does not apply (no code to
    save, a different deliverable, a type that is not allowed, or a request with its own topic)."""
    if not _is_delivery_request(request):
        return None
    blocks = _last_assistant_code(assistant_texts)
    if not blocks:
        return None
    lower = " ".join((request or "").split()).lower()
    current = _named_file(lower)
    bare = _BARE_EXT.search(lower)
    earlier = None
    for message in reversed(list(user_history)):
        earlier = _named_file(" ".join((message or "").split()).lower())
        if earlier is not None:
            break
    # The extension: the current message first, then the language the user names, then an earlier
    # name, then the code's own language.
    explicit_ext = current[1] if current else (bare.group(1).lower() if bare else None)
    if explicit_ext is not None and explicit_ext not in INLINE_FILE_EXTENSIONS:
        return None  # an executable or unknown type is never created here
    wanted_ext = explicit_ext
    if wanted_ext is None:
        for token in _TOKEN.findall(lower):
            if token in _REQUEST_LANGUAGES:
                wanted_ext = _REQUEST_LANGUAGES[token]
                break
    if wanted_ext is None and earlier is not None:
        if earlier[1] not in INLINE_FILE_EXTENSIONS:
            return None
        wanted_ext = earlier[1]
    chosen = _pick_block(blocks, wanted_ext)
    block_ext = _LANGUAGE_EXT.get(chosen.language) or _guess_extension(chosen.code)
    if chosen.language and chosen.language not in _LANGUAGE_EXT and block_ext is None:
        return None  # a fence tagged with a language this module does not know (bash, powershell)
    ext = wanted_ext or block_ext or "txt"
    if explicit_ext is None and block_ext not in (None, "txt") and ext != block_ext:
        # A language word or an old name never turns the code into another language's file: the
        # extension follows the code that is saved. Only a name in THIS message overrides it.
        ext = block_ext
    stem = (
        current[0]
        if current and current[1] == ext
        else earlier[0]
        if earlier and earlier[1] == ext
        else _default_stem(ext, chosen.code)
    )
    try:
        name = sanitize_inline_name(f"{stem}.{ext}")
    except InlineFileError:
        return None
    content = chosen.code.rstrip("\n") + "\n"
    return CodeFile(
        name=name,
        content=content,
        lines=content.count("\n"),
        hinglish=_HINGLISH.search(lower) is not None,
    )


def _pick_block(blocks: Sequence[CodeBlock], wanted_ext: str | None) -> CodeBlock:
    """The last block whose language is the wanted one, else the last block that is real code."""
    if wanted_ext is not None:
        for block in reversed(blocks):
            if _LANGUAGE_EXT.get(block.language) == wanted_ext:
                return block
    for block in reversed(blocks):
        if block.language not in ("", "text", "txt", "plaintext", "output", "console"):
            return block
    return blocks[-1]


def requested_code_name(request: str, file_type: str) -> tuple[str, str] | None:
    """``(name, extension)`` when the user's own message names a code file (``main.py``) but the
    model built a ``txt`` or ``md`` file for it: the named file wins, the text is kept as it is.
    ``None`` otherwise (other formats, no name, an extension outside the inline allowlist)."""
    if file_type not in ("txt", "md"):
        return None
    named = _named_file(" ".join((request or "").split()).lower())
    if named is None or named[1] in ("txt", "md") or named[1] not in INLINE_FILE_EXTENSIONS:
        return None
    try:
        name = sanitize_inline_name(f"{named[0]}.{named[1]}")
    except InlineFileError:
        return None
    return name, named[1]


def delivered_text(file: CodeFile) -> str:
    """The reply written by code: the real file name and line count."""
    count = f"{file.lines} line" + ("" if file.lines == 1 else "s")
    if file.hinglish:
        return f"{file.name} ({count}) taiyaar hai. Isme wahi code hai jo upar dikhaya tha."
    return f"{file.name} ({count}) is ready. It holds exactly the code shown above."


__all__ = [
    "CodeBlock",
    "CodeFile",
    "code_blocks",
    "delivered_text",
    "plan_code_file",
    "requested_code_name",
]
