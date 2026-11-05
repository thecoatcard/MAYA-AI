from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.tenant import UnsafeDatabaseRole, tenant_transaction, verify_runtime_role


def engine_returning(row: tuple[bool, bool] | None) -> MagicMock:
    connection = AsyncMock()
    connection.execute.return_value.one_or_none = MagicMock(return_value=row)
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    return engine


async def test_restricted_role_passes() -> None:
    await verify_runtime_role(engine_returning((False, False)))


@pytest.mark.parametrize("row", [(True, False), (False, True), (True, True), None])
async def test_privileged_or_owning_or_unknown_role_is_refused(
    row: tuple[bool, bool] | None,
) -> None:
    with pytest.raises(UnsafeDatabaseRole, match="not restricted"):
        await verify_runtime_role(engine_returning(row))


async def test_context_is_set_with_a_bound_parameter_inside_the_transaction() -> None:
    connection = AsyncMock()
    begin = MagicMock()
    begin.__aenter__ = AsyncMock()
    begin.__aexit__ = AsyncMock(return_value=False)
    connection.begin = MagicMock(return_value=begin)
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    workspace = uuid4()
    async with tenant_transaction(engine, workspace):
        pass
    statement, parameters = connection.execute.call_args.args
    assert "set_config('app.workspace_id', :workspace_id, true)" in str(statement)
    assert parameters == {"workspace_id": str(workspace)}
    begin.__aenter__.assert_awaited_once()


async def test_no_context_statement_is_issued_without_a_workspace() -> None:
    connection = AsyncMock()
    begin = MagicMock()
    begin.__aenter__ = AsyncMock()
    begin.__aexit__ = AsyncMock(return_value=False)
    connection.begin = MagicMock(return_value=begin)
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    async with tenant_transaction(engine, None):
        pass
    connection.execute.assert_not_awaited()
