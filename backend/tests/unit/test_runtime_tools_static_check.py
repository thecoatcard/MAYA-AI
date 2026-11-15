"""Static checks on model-written tool source (defense in depth; the sandbox is the boundary)."""

from __future__ import annotations

import pytest

from app.runtime_tools.static_check import MAX_SOURCE_BYTES, check_source

GOOD = """\
import csv
import json
from pathlib import Path


def main(args):
    rows = list(csv.reader(open("in/data.csv", newline="", encoding="utf-8")))
    Path("out").mkdir(exist_ok=True)
    return {"rows": len(rows), "text": json.dumps(args)}
"""


def wrap(body: str, imports: str = "") -> str:
    indented = "\n".join("    " + line for line in body.splitlines())
    return f"{imports}\n\ndef main(args):\n{indented}\n"


def test_a_plain_data_tool_passes() -> None:
    result = check_source(GOOD)
    assert result.ok, result.problems


@pytest.mark.parametrize(
    "module",
    [
        "os",
        "sys",
        "subprocess",
        "socket",
        "ctypes",
        "shutil",
        "importlib",
        "urllib.request",
        "http.client",
        "requests",
        "pickle",
        "threading",
        "multiprocessing",
        "builtins",
        "PIL",
        "numpy",
    ],
)
def test_imports_outside_the_allowlist_are_refused(module: str) -> None:
    for source in (
        wrap("return {}", f"import {module}"),
        wrap("return {}", f"from {module} import x"),
        wrap("return {}", f"import {module} as safe_name"),
    ):
        result = check_source(source)
        assert not result.ok and any("not allowed" in p for p in result.problems), module


@pytest.mark.parametrize(
    "body",
    [
        "return eval('1')",
        "exec('x=1')\n    return {}",
        "return compile('1', 'x', 'eval')",
        "return __import__('os')",
        "return getattr(__builtins__, 'open')",
        "return getattr(args, 'x')",
        "setattr(args, 'x', 1)\n    return {}",
        "return globals()",
        "return locals()",
        "return vars(args)",
        "return type(args)",
        "return input()",
        "breakpoint()\n    return {}",
        "return __builtins__",
        "return ().__class__.__bases__[0].__subclasses__()",
        "return args.__class__",
        "return main.__globals__",
        "return main.__code__",
        "return (lambda: 1).__globals__['x']",
        "return pathlib.os.system('id')",
        "return zipfile.sys.modules",
        "return Path.home()",
        "return Path('x').expanduser()",
        "return open('/etc/passwd').read()",
        "return open('C:" + chr(92) * 2 + "Windows" + chr(92) * 2 + "win.ini').read()",
        "return open('C:/Windows/win.ini').read()",
        "return open('../secret').read()",
        "return open('a/../../b').read()",
        "return Path('/etc/passwd').read_text()",
        "return Path('x').joinpath('..', 'y')",
        "return io.open('/etc/passwd')",
    ],
)
def test_dynamic_code_and_escapes_are_refused(body: str) -> None:
    source = wrap(body, "import io\nimport pathlib\nimport zipfile\nfrom pathlib import Path")
    result = check_source(source)
    assert not result.ok, body


@pytest.mark.parametrize(
    "body",
    [
        "name = '__imp' + 'ort__'\n    return getattr(__builtins__, name)('os')",
        "return globals()['__built' + 'ins__']",
        "return '__import__'",
        "return '{0.__class__}'.format(args)",
        "f = getattr\n    return f(args, 'x')",
        "return vars(__builtins__)['ev' + 'al']('1')",
    ],
)
def test_obfuscated_variants_are_refused(body: str) -> None:
    assert not check_source(wrap(body)).ok, body


def test_import_inside_a_function_and_star_and_relative_are_refused() -> None:
    assert not check_source(wrap("import os\n    return {}")).ok
    assert not check_source(wrap("return {}", "from json import *")).ok
    assert not check_source(wrap("return {}", "from . import sibling")).ok
    assert not check_source(wrap("return {}", "from pathlib import os")).ok


def test_the_source_must_parse_define_main_and_stay_small() -> None:
    assert not check_source("def main(args:").ok
    assert not check_source("").ok
    assert not check_source("x = 1\n").ok  # no main
    assert not check_source("def main(a, b):\n    return {}\n").ok  # two arguments
    assert not check_source("def main(args):\n    return 1\x00\n").ok
    padding = "# " + "x" * 200 + "\n"
    big = "def main(args):\n    return {}\n" + padding * (MAX_SOURCE_BYTES // 200 + 5)
    result = check_source(big)
    assert not result.ok and "larger than" in result.problems[0]


def test_every_listed_library_is_allowed() -> None:
    for module in ("openpyxl", "docx", "pypdf", "reportlab", "zipfile", "hashlib", "decimal"):
        assert check_source(wrap("return {}", f"import {module}")).ok, module
