"""Static guards on the authentication SQL: only literal statements, never SELECT *."""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
SQL_MODULES = [
    APP / "auth" / "postgres.py",
    APP / "db" / "tenant.py",
    APP / "db" / "session.py",
    APP / "core" / "resources.py",
]


def text_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "text"
    ]


@pytest.mark.parametrize("path", SQL_MODULES, ids=lambda p: p.name)
def test_sql_text_is_always_a_literal_so_values_can_only_travel_as_bind_parameters(
    path: Path,
) -> None:
    for call in text_calls(path):
        assert len(call.args) == 1
        argument = call.args[0]
        assert isinstance(argument, ast.Constant) and isinstance(argument.value, str), (
            f"{path.name}:{call.lineno} builds SQL from a non-literal"
        )


def test_the_checker_would_catch_an_interpolated_statement() -> None:
    tree = ast.parse('value = 1\nstatement = text(f"SELECT {value}")\n')
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert not isinstance(call.args[0], ast.Constant)


def test_authentication_sql_never_selects_star_from_users() -> None:
    source = (APP / "auth" / "postgres.py").read_text(encoding="utf-8")
    assert re.search(r"select\s+\*", source, re.IGNORECASE) is None
    assert "hashed_password" in source  # only through the definer lookup and explicit updates


def test_the_password_hash_is_only_selected_through_the_definer_lookup_functions() -> None:
    statements: list[str] = []
    for call in text_calls(APP / "auth" / "postgres.py"):
        argument = call.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            statements.append(argument.value)
    selecting = [
        sql
        for sql in statements
        if re.match(r"\s*select\b", sql, re.IGNORECASE) and "hashed_password" in sql
    ]
    assert len(selecting) == 1  # only the by-username lookup (login) returns the hash
    assert "FROM public.auth_find_account_by_username" in selecting[0]
