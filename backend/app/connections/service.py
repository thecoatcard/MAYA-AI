"""Per-user connections: store (encrypted), resolve at run time, test, disconnect.

Storage is the caller's own ``user_settings`` row (no migration): one text key per provider
(``connection:<provider>``) holding ``v1.<base64>`` AES-GCM ciphertext of a small JSON document
(token, optional database id, and what the last test learned). The ciphertext is bound to the
workspace, user and provider, so it cannot be replayed into another row.

Resolution order for a request made by user U: U's own saved connection, else (ONLY when U is the
deployment owner, ``MAYA_ENV_CONNECTIONS_USERS``) the deployment's environment value
(``MAYA_GITHUB_TOKEN`` and friends), else none. Everyone else gets none and must connect their own.
A user never resolves another user's connection: every read names the caller's workspace and
user, and the database row level security repeats that.

Secrets are never returned by ``status``/``save``/``test``, never logged, and never part of an
error message. ``ResolvedConnection.token`` is excluded from ``repr``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from app.connections.crypto import (
    ConnectionCipher,
    ConnectionsNotConfigured,
    CredentialDecryptError,
)
from app.connections.mailbox import (
    check_mailbox,
    normalize_app_password,
    validate_mailbox_input,
)
from app.connections.providers import (
    PROVIDERS,
    ConnectionInputError,
    Credentials,
    HttpFactory,
    ProviderSpec,
    TestResult,
    default_http,
    normalize_notion_database_id,
)
from app.mail.config import MailboxConfig, mailbox_from_settings, mask_address

if TYPE_CHECKING:
    from app.connections.ownership import DeploymentOwnership
    from app.mail.service import MailService
    from app.mcp.connectors.github import GitHubConnector
    from app.mcp.connectors.notion import NotionConnector

logger = logging.getLogger(__name__)

SETTINGS_PREFIX = "connection:"
Source = Literal["user", "environment"]
State = Literal["not_connected", "saved", "connected", "error", "server_default", "unreadable"]


class UnknownProvider(LookupError):
    """The provider key is not one of ``PROVIDERS``."""


class TextStore(Protocol):
    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None: ...
    async def set_text(self, workspace_id: UUID, user_id: UUID, key: str, value: str) -> None: ...
    async def remove_key(self, workspace_id: UUID, user_id: UUID, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class EnvDefaults:
    """The deployment-wide single-user defaults (read once from settings)."""

    github_token: str | None = None
    notion_token: str | None = None
    notion_database_id: str | None = None
    mailbox: MailboxConfig | None = None  # MAYA_MAIL_*: the single-user default mailbox

    @classmethod
    def from_settings(cls, settings: Any) -> EnvDefaults:
        def secret(value: Any) -> str | None:
            text = value.get_secret_value().strip() if value is not None else ""
            return text or None

        db = (getattr(settings, "notion_database_id", None) or "").strip()
        return cls(
            github_token=secret(settings.github_token),
            notion_token=secret(settings.notion_token),
            notion_database_id=db or None,
            mailbox=mailbox_from_settings(settings),
        )

    def for_provider(self, provider: str) -> tuple[str, str | None] | None:
        if provider == "github" and self.github_token:
            return self.github_token, None
        if provider == "notion" and self.notion_token:
            return self.notion_token, self.notion_database_id
        if provider == "mail" and self.mailbox is not None:
            return self.mailbox.password, None
        return None


@dataclass(frozen=True, slots=True)
class ResolvedConnection:
    provider: str
    token: str = field(repr=False)
    database_id: str | None
    source: Source


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    """Safe to send to the browser: no secret, only what the user needs to see."""

    provider: str
    label: str
    state: State
    source: Source | None
    message: str
    account: str | None = None
    scopes: tuple[str, ...] | None = None
    database_id: str | None = None
    database_title: str | None = None
    last_tested_at: str | None = None
    takes_database_id: bool = False
    # Mailbox only: what the form shows back. Never the password; the address is masked.
    mailbox: dict[str, Any] | None = None
    kind: str = "token"


class ConnectionService:
    def __init__(
        self,
        store: TextStore,
        cipher: ConnectionCipher | None,
        env: EnvDefaults | None = None,
        http: HttpFactory = default_http,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        deployment_owner: DeploymentOwnership | None = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._env = env or EnvDefaults()
        self._http = http
        self._clock = clock
        # None => nobody is the deployment owner: the server-level values are never used.
        self._owner = deployment_owner

    async def is_deployment_owner(self, workspace_id: UUID, user_id: UUID) -> bool:
        """True when this caller may use the server-level (environment) connections."""
        if self._owner is None:
            return False
        return await self._owner.is_owner(workspace_id, user_id)

    @property
    def server_defaults_present(self) -> bool:
        """Server-level tokens exist (booleans only; used for the owner hint)."""
        env = self._env
        return bool(env.github_token or env.notion_token or env.mailbox)

    @property
    def server_defaults_blocked(self) -> bool:
        """Server tokens exist but no owner is named, so nobody may use them."""
        return self.server_defaults_present and (self._owner is None or not self._owner.configured)

    # ------------------------------------------------------------------------------ helpers

    @property
    def encryption_available(self) -> bool:
        return self._cipher is not None

    @staticmethod
    def spec(provider: str) -> ProviderSpec:
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise UnknownProvider(provider)
        return spec

    def _aad(self, workspace_id: UUID, user_id: UUID, provider: str) -> bytes:
        return ConnectionCipher.aad(workspace_id, user_id, provider)

    async def _load(
        self, workspace_id: UUID, user_id: UUID, provider: str
    ) -> dict[str, Any] | None:
        """The saved document. ``None`` when nothing is saved; ``CredentialDecryptError`` when it
        is saved but unreadable (no key, key changed, tampered)."""
        blob = await self._store.get_text(workspace_id, user_id, SETTINGS_PREFIX + provider)
        if blob is None:
            return None
        if self._cipher is None:
            raise CredentialDecryptError("no encryption key")
        plain = self._cipher.decrypt(blob, self._aad(workspace_id, user_id, provider))
        try:
            data = json.loads(plain)
        except ValueError:
            raise CredentialDecryptError("credential is corrupt") from None
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            raise CredentialDecryptError("credential is corrupt")
        return data

    async def _write(
        self, workspace_id: UUID, user_id: UUID, provider: str, data: dict[str, Any]
    ) -> None:
        if self._cipher is None:
            raise ConnectionsNotConfigured()
        sealed = self._cipher.encrypt(
            json.dumps(data, separators=(",", ":")), self._aad(workspace_id, user_id, provider)
        )
        await self._store.set_text(workspace_id, user_id, SETTINGS_PREFIX + provider, sealed)

    # ------------------------------------------------------------------------------ resolve

    async def resolve(
        self, workspace_id: UUID, user_id: UUID, provider: str
    ) -> ResolvedConnection | None:
        """The credentials this user's run should use, or ``None`` (never another user's)."""
        self.spec(provider)
        try:
            data = await self._load(workspace_id, user_id, provider)
        except CredentialDecryptError:
            # Saved but unreadable: do not silently fall back to the shared default, the user
            # must reconnect (the status says so).
            logger.warning("connection_unreadable provider=%s", provider)
            return None
        if data is not None:
            database_id = data.get("database_id")
            return ResolvedConnection(
                provider=provider,
                token=data["token"],
                database_id=database_id if isinstance(database_id, str) and database_id else None,
                source="user",
            )
        default = self._env.for_provider(provider)
        if default is None or not await self.is_deployment_owner(workspace_id, user_id):
            return None  # the server-level values belong to the deployment owner only
        return ResolvedConnection(provider, default[0], default[1], "environment")

    async def is_connected(self, workspace_id: UUID, user_id: UUID, provider: str) -> bool:
        return await self.resolve(workspace_id, user_id, provider) is not None

    async def github_connector(self, workspace_id: UUID, user_id: UUID) -> GitHubConnector | None:
        from app.mcp.connectors.github import GitHubConnector

        conn = await self.resolve(workspace_id, user_id, "github")
        return GitHubConnector(token=conn.token) if conn else None

    async def notion_connector(
        self, workspace_id: UUID, user_id: UUID
    ) -> tuple[NotionConnector, str | None] | None:
        from app.mcp.connectors.notion import NotionConnector

        conn = await self.resolve(workspace_id, user_id, "notion")
        return (NotionConnector(token=conn.token), conn.database_id) if conn else None

    # ------------------------------------------------------------------------------- status

    async def status(self, workspace_id: UUID, user_id: UUID) -> list[ConnectionStatus]:
        return [await self.status_of(workspace_id, user_id, key) for key in PROVIDERS]

    async def status_of(self, workspace_id: UUID, user_id: UUID, provider: str) -> ConnectionStatus:
        spec = self.spec(provider)
        base: dict[str, Any] = {
            "provider": provider,
            "label": spec.label,
            "takes_database_id": spec.has_database_id,
            "kind": spec.kind,
        }
        try:
            data = await self._load(workspace_id, user_id, provider)
        except CredentialDecryptError:
            return ConnectionStatus(
                **base,
                state="unreadable",
                source="user",
                message=(
                    "A saved token cannot be read (the server key changed). Disconnect and "
                    "connect again."
                ),
            )
        if data is None:
            default = self._env.for_provider(provider)
            if default is not None and not await self.is_deployment_owner(workspace_id, user_id):
                default = None  # not the deployment owner: never "environment"
            if provider == "mail":
                env_box = self._env.mailbox if default is not None else None
                if env_box is None:
                    return ConnectionStatus(
                        **base,
                        state="not_connected",
                        source=None,
                        message="No mailbox connected. Add your address and app password.",
                    )
                return ConnectionStatus(
                    **base,
                    state="server_default",
                    source="environment",
                    account=mask_address(env_box.address),
                    message=(
                        "Using the mailbox set on the server. Save your own mailbox to use "
                        "your own account instead."
                    ),
                )
            if default is None:
                return ConnectionStatus(
                    **base,
                    state="not_connected",
                    source=None,
                    message=f"Not connected. Add your {spec.label} token to use it.",
                )
            return ConnectionStatus(
                **base,
                state="server_default",
                source="environment",
                database_id=default[1],
                message=(
                    f"Using the {spec.label} token set on the server. Add your own token to use "
                    "your own account instead."
                ),
            )
        tested = data.get("tested_ok")
        account = data.get("account") if isinstance(data.get("account"), str) else None
        scopes_raw = data.get("scopes")
        scopes = tuple(str(s) for s in scopes_raw) if isinstance(scopes_raw, list) else None
        db_title = data.get("database_title")
        common: dict[str, Any] = {
            **base,
            "source": "user",
            "account": account,
            "scopes": scopes,
            "database_id": data.get("database_id") or None,
            "database_title": db_title if isinstance(db_title, str) else None,
            "last_tested_at": data.get("tested_at"),
        }
        if provider == "mail":
            common["mailbox"] = _mailbox_view(data)
            common["account"] = common["mailbox"]["address"]
        if tested is True:
            who = f" as {account}" if account else ""
            return ConnectionStatus(**common, state="connected", message=f"Connected{who}.")
        if tested is False:
            return ConnectionStatus(
                **common,
                state="error",
                message=str(data.get("last_error") or "The last test failed."),
            )
        return ConnectionStatus(
            **common, state="saved", message="Saved. Press Test connection to check it."
        )

    # -------------------------------------------------------------------------------- save

    async def save(
        self,
        workspace_id: UUID,
        user_id: UUID,
        provider: str,
        *,
        token: str | None,
        database_id: str | None,
    ) -> ConnectionStatus:
        """Store the caller's credentials. ``token=None`` keeps the saved token (Notion: change
        only the database); ``database_id=''`` clears it. Raises ``ConnectionsNotConfigured``
        (no key) or ``ConnectionInputError`` (unusable input)."""
        spec = self.spec(provider)
        if self._cipher is None:
            raise ConnectionsNotConfigured()
        try:
            existing = await self._load(workspace_id, user_id, provider)
        except CredentialDecryptError:
            existing = None  # unreadable: replaced by what the user is saving now
        clean_token: str | None = spec.normalize_token(token) if token else None
        if clean_token is None and existing is None:
            raise ConnectionInputError("token_missing", f"Paste your {spec.label} token first.")
        clean_db: str | None
        if not spec.has_database_id or database_id is None:
            clean_db = (existing or {}).get("database_id") if spec.has_database_id else None
        elif not database_id.strip():
            clean_db = None
        else:
            clean_db = normalize_notion_database_id(database_id)
        data: dict[str, Any] = {
            "token": clean_token or (existing or {})["token"],
            "database_id": clean_db,
        }
        if existing is not None and clean_token is None and clean_db == existing.get("database_id"):
            data.update({k: existing.get(k) for k in _VERIFIED_KEYS})  # nothing changed
        await self._write(workspace_id, user_id, provider, data)
        return await self.status_of(workspace_id, user_id, provider)

    async def save_mailbox(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        address: str | None,
        app_password: str | None,
        imap_host: str | None,
        imap_port: int | None,
        smtp_host: str | None,
        smtp_port: int | None,
    ) -> ConnectionStatus:
        """Store the caller's mailbox as ONE sealed document. ``app_password=None`` keeps the
        saved password (change only hosts or address); ``address=None`` keeps the saved address.
        The password is write-only: no status, test or error ever contains it."""
        if self._cipher is None:
            raise ConnectionsNotConfigured()
        try:
            existing = await self._load(workspace_id, user_id, "mail")
        except CredentialDecryptError:
            existing = None
        old_address = str(existing.get("address") or "") if existing else ""
        box = validate_mailbox_input(
            address=address if address else old_address,
            imap_host=imap_host or (existing or {}).get("imap_host"),
            imap_port=imap_port or (existing or {}).get("imap_port"),
            smtp_host=smtp_host or (existing or {}).get("smtp_host"),
            smtp_port=smtp_port or (existing or {}).get("smtp_port"),
        )
        password = normalize_app_password(app_password) if app_password else None
        if password is None and existing is None:
            raise ConnectionInputError("password_missing", "Paste your app password first.")
        data: dict[str, Any] = {
            "token": password or (existing or {})["token"],
            "address": box.address,
            "imap_host": box.imap_host,
            "imap_port": box.imap_port,
            "smtp_host": box.smtp_host,
            "smtp_port": box.smtp_port,
        }
        unchanged = (
            existing is not None
            and password is None
            and all(existing.get(k) == data[k] for k in _MAILBOX_KEYS)
        )
        if unchanged and existing is not None:
            data.update({k: existing.get(k) for k in _VERIFIED_KEYS})
        await self._write(workspace_id, user_id, "mail", data)
        return await self.status_of(workspace_id, user_id, "mail")

    async def resolve_mailbox(self, workspace_id: UUID, user_id: UUID) -> MailboxConfig | None:
        """The mailbox this user's run must use: their own saved one, else the server's
        (MAYA_MAIL_*), else ``None``. Never another user's. An unreadable saved mailbox does not
        fall back to the shared one."""
        try:
            data = await self._load(workspace_id, user_id, "mail")
        except CredentialDecryptError:
            logger.warning("connection_unreadable provider=mail")
            return None
        if data is None:
            if self._env.mailbox is None or not await self.is_deployment_owner(
                workspace_id, user_id
            ):
                return None  # the server's mailbox belongs to the deployment owner only
            return self._env.mailbox
        try:
            return _config_from(data)
        except (KeyError, TypeError, ValueError):
            logger.warning("connection_unreadable provider=mail")
            return None

    async def mail_service(self, workspace_id: UUID, user_id: UUID) -> MailService | None:
        from app.mail.service import MailService

        config = await self.resolve_mailbox(workspace_id, user_id)
        return MailService(config) if config is not None else None

    # -------------------------------------------------------------------------------- test

    async def test(self, workspace_id: UUID, user_id: UUID, provider: str) -> TestResult:
        spec = self.spec(provider)
        conn = await self.resolve(workspace_id, user_id, provider)
        if conn is None:
            try:
                await self._load(workspace_id, user_id, provider)
            except CredentialDecryptError:
                return TestResult(
                    False,
                    "needs_reconnect",
                    "The saved token cannot be read any more. Disconnect and connect again.",
                )
            return TestResult(
                False,
                "token_missing",
                f"No {spec.label} token is saved yet. Paste your token and press Save first.",
            )
        if spec.kind == "mailbox":
            box = await self.resolve_mailbox(workspace_id, user_id)
            if box is None:
                return TestResult(False, "token_missing", "No mailbox is saved yet.")
            result = await check_mailbox(box)
        else:
            result = await spec.check(Credentials(conn.token, conn.database_id), self._http)
        if conn.source == "user":
            await self._remember(workspace_id, user_id, provider, conn.token, result)
        return result

    async def _remember(
        self,
        workspace_id: UUID,
        user_id: UUID,
        provider: str,
        token: str,
        result: TestResult,
    ) -> None:
        """Keep what the test learned (account, scopes, last error), only for the same token."""
        try:
            data = await self._load(workspace_id, user_id, provider)
            if data is None or data["token"] != token:
                return  # changed or removed while the test ran
            data["tested_ok"] = result.ok
            data["tested_at"] = self._clock().isoformat(timespec="seconds")
            data["account"] = result.account
            data["scopes"] = list(result.scopes) if result.scopes is not None else None
            data["database_title"] = result.database_title
            data["last_error"] = None if result.ok else result.message
            await self._write(workspace_id, user_id, provider, data)
        except (CredentialDecryptError, ConnectionsNotConfigured):
            return

    # ---------------------------------------------------------------------------- disconnect

    async def disconnect(self, workspace_id: UUID, user_id: UUID, provider: str) -> None:
        self.spec(provider)
        await self._store.remove_key(workspace_id, user_id, SETTINGS_PREFIX + provider)


_VERIFIED_KEYS = ("tested_ok", "tested_at", "account", "scopes", "database_title", "last_error")

_MAILBOX_KEYS = ("address", "imap_host", "imap_port", "smtp_host", "smtp_port")


def _config_from(data: dict[str, Any]) -> MailboxConfig:
    return MailboxConfig(
        address=str(data["address"]),
        password=str(data["token"]),
        imap_host=str(data["imap_host"]),
        imap_port=int(data["imap_port"]),
        smtp_host=str(data["smtp_host"]),
        smtp_port=int(data["smtp_port"]),
    )


def _mailbox_view(data: dict[str, Any]) -> dict[str, Any]:
    """What the form may show back: masked address and hosts. Never the password."""
    return {
        "address": mask_address(str(data.get("address") or "")),
        "imap_host": data.get("imap_host"),
        "imap_port": data.get("imap_port"),
        "smtp_host": data.get("smtp_host"),
        "smtp_port": data.get("smtp_port"),
        "password_saved": True,
    }
