"""Tenant context for row-level security, and the startup guard for the runtime role."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger("maya")

# SET LOCAL cannot take bind parameters; set_config(..., true) is the parameterized equivalent
# and, like SET LOCAL, lasts only until the end of the current transaction.
_SET_WORKSPACE = text("SELECT set_config('app.workspace_id', :workspace_id, true)")

_RUNTIME_ROLE_CHECK = text(
    """
    SELECT (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication),
           EXISTS (
             SELECT 1 FROM pg_class c
             WHERE c.relowner = r.oid AND c.relnamespace = 'public'::regnamespace
           )
    FROM pg_roles r WHERE r.rolname = current_user
    """
)


class UnsafeDatabaseRole(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The runtime database role is not restricted")


@asynccontextmanager
async def tenant_transaction(
    engine: AsyncEngine, workspace_id: UUID | None
) -> AsyncIterator[AsyncConnection]:
    """One transaction whose queries see only ``workspace_id``'s rows.

    The context is transaction-local, so a pooled connection never carries it to the next
    request. ``None`` opens a transaction with no context: tenant tables return no rows, and
    only the pre-login lookup functions can find accounts. The transaction commits when the
    block ends normally and rolls back on any exception.
    """
    async with engine.connect() as connection, connection.begin():
        if workspace_id is not None:
            await connection.execute(_SET_WORKSPACE, {"workspace_id": str(workspace_id)})
        yield connection


async def verify_runtime_role(engine: AsyncEngine) -> None:
    """Refuse to run as a role that could bypass row-level security.

    A misconfigured runtime URL (for example the migration administrator) would silently turn
    the policies off while every other check kept passing.
    """
    async with engine.connect() as connection:
        row = (await connection.execute(_RUNTIME_ROLE_CHECK)).one_or_none()
    if row is None or row[0] or row[1]:
        logger.error("unsafe_database_role")
        raise UnsafeDatabaseRole
