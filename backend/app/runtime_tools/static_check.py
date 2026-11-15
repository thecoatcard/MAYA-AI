"""Static checks for model-written tool source (ADR-0027). Defense in depth, NOT the boundary.

The isolated container is the real security boundary. These checks exist so that obviously wrong
or hostile code is refused BEFORE the user is asked to read it, and so the approval card shows
code that already passed a sanity filter. A determined author can find gaps in any AST filter;
nothing here may be relied on to stop a hostile program.

Rules (every one is a plain-language problem string):

* source at most ``MAX_SOURCE_BYTES``, valid UTF-8 Python that parses;
* imports only from ``ALLOWED_IMPORTS`` (no relative imports, no star imports);
* no dynamic code (``eval``/``exec``/``compile``/``__import__``/``importlib``), no ``getattr``
  family, ``globals``/``locals``/``vars``, ``type``, ``input``, ``breakpoint``;
* no ``os``/``sys``/``subprocess``/``socket``/``ctypes``... as a name OR as an attribute (so
  ``pathlib.os`` and ``zipfile.sys`` are refused too);
* no dunder attribute access (``().__class__``, ``__subclasses__``, ``__globals__``...) except a
  few harmless ones, and no string constant that spells a dunder escape;
* ``open``/``Path`` style calls with a literal path must use a relative path without ``..``;
* the module must define ``main(args)``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Final

MAX_SOURCE_BYTES: Final = 20 * 1024

# Standard-library modules that cannot reach the host or the network by themselves, plus the
# document libraries that are declared in backend/pyproject.toml. PIL (Pillow) is NOT a declared
# dependency, so it is not allowed. Whether the SANDBOX IMAGE ships these libraries is a property
# of the image (not verified here); a missing module fails at run time inside the container.
ALLOWED_IMPORTS: Final = frozenset(
    {
        "math",
        "statistics",
        "json",
        "csv",
        "re",
        "datetime",
        "itertools",
        "collections",
        "decimal",
        "fractions",
        "textwrap",
        "unicodedata",
        "hashlib",
        "base64",
        "zipfile",
        "io",
        "pathlib",
        "openpyxl",
        "docx",
        "pypdf",
        "reportlab",
    }
)

_BANNED_NAMES: Final = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "importlib",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "type",
        "input",
        "breakpoint",
        "__builtins__",
        "builtins",
        "os",
        "sys",
        "subprocess",
        "socket",
        "ctypes",
        "shutil",
        "posix",
        "nt",
    }
)
_BANNED_ATTRS: Final = frozenset(
    _BANNED_NAMES
    | {"modules", "home", "expanduser", "system", "popen", "f_globals", "gi_frame", "cr_frame"}
)
_ALLOWED_DUNDER_ATTRS: Final = frozenset({"__init__", "__name__", "__doc__"})
_ALLOWED_DUNDER_NAMES: Final = frozenset({"__name__"})
_DUNDER_STRING: Final = re.compile(
    r"__(?:import|builtins|class|subclasses|globals|bases|mro|dict|code|getattribute|loader|spec)__"
)
_FORMAT_FIELD_DUNDER: Final = re.compile(r"\{[^{}]*__")
_PATH_CALLS: Final = frozenset({"open", "Path", "PurePath", "PosixPath", "WindowsPath"})
_PATH_METHODS: Final = frozenset(
    {"open", "read_text", "write_text", "read_bytes", "write_bytes", "joinpath"}
)
_WINDOWS_DRIVE: Final = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class StaticCheckResult:
    ok: bool
    problems: tuple[str, ...]


def _literal_path_problem(value: str) -> str | None:
    text = value.replace("\\", "/")
    if text.startswith("/") or _WINDOWS_DRIVE.match(text) or text.startswith("~"):
        return f"the path {value!r} is absolute; use a relative path inside the scratch folder"
    if ".." in text.split("/"):
        return f"the path {value!r} uses '..'; paths must stay inside the scratch folder"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []

    def _add(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", 0)
        text = f"line {line}: {message}" if line else message
        if text not in self.problems:
            self.problems.append(text)

    def _check_module(self, node: ast.AST, dotted: str) -> None:
        top = dotted.split(".")[0]
        if top not in ALLOWED_IMPORTS:
            self._add(node, f"import of '{top}' is not allowed")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(node, alias.name)
            if alias.asname in _BANNED_NAMES:
                self._add(node, f"importing as '{alias.asname}' is not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._add(node, "relative imports are not allowed")
            return
        self._check_module(node, node.module or "")
        for alias in node.names:
            if alias.name == "*":
                self._add(node, "'from ... import *' is not allowed")
            elif alias.name in _BANNED_ATTRS or alias.name.startswith("__"):
                self._add(node, f"importing '{alias.name}' is not allowed")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _BANNED_NAMES:
            self._add(node, f"use of '{node.id}' is not allowed")
        elif node.id.startswith("__") and node.id not in _ALLOWED_DUNDER_NAMES:
            self._add(node, f"use of '{node.id}' is not allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr in _BANNED_ATTRS:
            self._add(node, f"access to '.{attr}' is not allowed")
        elif attr.startswith("__") and attr not in _ALLOWED_DUNDER_ATTRS:
            self._add(node, f"access to '.{attr}' is not allowed")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, str):
            if _DUNDER_STRING.search(value):
                self._add(node, "a string that names an interpreter internal is not allowed")
            elif _FORMAT_FIELD_DUNDER.search(value):
                self._add(node, "a format string that reaches an attribute is not allowed")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name) and func.id in _PATH_CALLS:
            name = func.id
        elif isinstance(func, ast.Attribute) and (
            func.attr in _PATH_METHODS or func.attr in _PATH_CALLS
        ):
            name = func.attr
        if name is not None:
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    problem = _literal_path_problem(argument.value)
                    if problem:
                        self._add(node, problem)
                    if name != "joinpath":
                        break
        self.generic_visit(node)


def _has_main(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            positional = len(node.args.posonlyargs) + len(node.args.args)
            return positional == 1 and node.args.vararg is None
    return False


def check_source(source: str) -> StaticCheckResult:
    """Return every problem found (empty and ``ok`` when the source may be shown for approval)."""
    if not isinstance(source, str) or not source.strip():
        return StaticCheckResult(False, ("the source is empty",))
    if len(source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
        return StaticCheckResult(
            False, (f"the source is larger than {MAX_SOURCE_BYTES // 1024} KB",)
        )
    if "\x00" in source:
        return StaticCheckResult(False, ("the source contains a NUL character",))
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return StaticCheckResult(False, (f"the source does not parse: {type(error).__name__}",))
    visitor = _Visitor()
    visitor.visit(tree)
    problems = list(visitor.problems)
    if not _has_main(tree):
        problems.append("the source must define main(args) taking exactly one argument")
    return StaticCheckResult(not problems, tuple(problems[:20]))


__all__ = ["ALLOWED_IMPORTS", "MAX_SOURCE_BYTES", "StaticCheckResult", "check_source"]
