import ast
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.api_key_text import clean_api_key
from app.gateway.registry import (
    ALLOWED_OVERRIDE_ALIASES,
    MODEL_ID_PATTERN,
    SEARCH_ALIAS_CHOICES,
    validate_agent_alias,
)
from app.search.names import (
    DDGS_ENGINES,
    DEFAULT_PROVIDERS,
    parse_engine_list,
    parse_provider_list,
)

MIN_SECRET_KEY_BYTES = 32
SENDER_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")


def is_placeholder_key(key: str) -> bool:
    """Return True if the key is empty, whitespace, or a placeholder/example."""
    cleaned = key.strip()
    if not cleaned:
        return True
    lower = cleaned.lower()
    if "not-real" in lower or "placeholder" in lower:
        return True
    if lower.startswith(("your-", "your_", "<your")):
        return True
    if lower.startswith("example-") and "for-test" not in lower:
        return True
    return False


def _validate_secret_key(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value().encode()) < MIN_SECRET_KEY_BYTES:
        raise ValueError(f"Use at least {MIN_SECRET_KEY_BYTES} bytes of random key material")
    return value


def _split_list(value: Any) -> Any:
    """Accept a JSON list or a comma-separated string for list settings."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                raise ValueError("Invalid list value") from None
            return parsed
        return [part.strip() for part in text.split(",") if part.strip()]
    return value


def _validate_migration_url(value: SecretStr) -> SecretStr:
    try:
        url = make_url(value.get_secret_value())
        valid = (
            url.drivername == "postgresql+asyncpg"
            and bool(url.username and url.password and url.host and url.database)
            and (url.port is None or 0 < url.port <= 65535)
        )
    except (ArgumentError, ValueError):
        raise ValueError("Invalid migration database URL") from None
    if not valid:
        raise ValueError("Invalid migration database URL")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAYA_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    redis_url: SecretStr
    migration_database_url: SecretStr | None = None
    probe_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Authentication. The two keys protect different things and must differ.
    jwt_signing_key: SecretStr
    otp_hmac_key: SecretStr
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=604_800, ge=3600, le=2_592_000)
    refresh_absolute_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    auth_cookie_secure: bool = True
    otp_resend_cooldown_seconds: int = Field(default=60, ge=1, le=3600)

    # Deployment profile. Anything but "development" is treated as production-like: the
    # interactive API documentation is off unless explicitly enabled.
    environment: Literal["development", "production"] = "production"
    api_docs_enabled: bool | None = None
    # Hostnames the API answers to (Host header allow-list, enforced by the application).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    # Live voice watches the user's background tasks and speaks the milestones (done, failed,
    # cancelled, approval needed, input needed) without any chat message. false: no spoken updates.
    voice_task_announce: bool = True
    # Origins allowed to open the live voice WebSocket (compared to the Origin header).
    voice_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    # Directory of the exported web app. When set the API serves it (see core/static_web.py).
    web_static_dir: Path | None = None
    # Local-owner bootstrap for the desktop app (ADR-0014). Off unless explicitly enabled.
    local_owner_bootstrap: bool = False

    # Outbound e-mail for one-time codes. All optional; absent means e-mail is unavailable.
    smtp_host: str | None = Field(default=None, min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: SecretStr | None = None
    smtp_from_address: str | None = Field(default=None, max_length=320)
    smtp_security: Literal["starttls", "ssl"] = "starttls"

    # The user's own mailbox (an app password) for reading and sending mail from chat. Separate
    # from the system mailer above, which only sends login codes. All optional.
    mail_address: str | None = Field(default=None, max_length=320)
    mail_app_password: SecretStr | None = None
    mail_imap_host: str = Field(default="imap.gmail.com", min_length=1, max_length=253)
    mail_imap_port: int = Field(default=993, ge=1, le=65535)
    mail_smtp_host: str = Field(default="smtp.gmail.com", min_length=1, max_length=253)
    # 465 = implicit TLS; 587 = STARTTLS. Plaintext is never used.
    mail_smtp_port: int = Field(default=465, ge=1, le=65535)
    # Kill switch. None (unset) means on when the address and the app password are both set.
    mail_enabled: bool | None = None

    # Model Gateway (Phase 03)
    gemini_api_key: SecretStr | None = None
    gemini_api_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    gemini_model_overrides: Any = Field(default_factory=dict)
    gemini_verify_models_on_startup: bool = False
    gemini_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    # Most DIFFERENT keys one call may try before failing honestly (a pool can hold 17+ keys).
    # Applies to overload (5xx, timeout): one overloaded MODEL fails on every key, so after two
    # different keys fail the chain moves to the next model anyway (see MODEL_GATEWAY.md).
    gemini_max_key_attempts: int = Field(default=4, ge=1, le=17)
    # Most different keys one call may try on a QUOTA (429) failure before it moves to the next
    # model. Quota is per key, so another key often works. Never lower than gemini_max_key_attempts.
    gemini_max_quota_key_attempts: int = Field(default=8, ge=1, le=64)
    # Interactive chat only: the cap on each model attempt until its first chunk, and the budget
    # for the whole failover chain. A dead or overloaded key then costs seconds, not a minute.
    gateway_first_chunk_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    gateway_total_deadline_seconds: float = Field(default=75.0, gt=0, le=600)
    # Per-attempt cap for agent and task model calls (chapter writes, long specs, grounded
    # synthesis legitimately take 30-120 s). Chat, embeddings, memory and voice keep
    # gemini_request_timeout_seconds.
    agent_request_timeout_seconds: float = Field(default=150.0, gt=0, le=600)
    # Model alias for every agent and task model call (research, document, email, executor,
    # planner, long documents). Chat has its own alias. "live" is refused: it is a stateful audio
    # WebSocket API. The fallback alias is empty by default: agent calls then never fall back.
    agent_model_alias: str = "lite"
    agent_fallback_alias: str = ""
    # Grounded (Google Search) calls: the research agent and the MCP web_search tool. Grounding
    # has its own quota per model, so it uses the dedicated "search" alias first
    # (gemini-2.5-flash). Unset (None) or empty means no fallback: the agent alias
    # (3.5-flash-lite) has no grounding quota. REST aliases only.
    agent_search_alias: str = "search"
    agent_search_fallback_alias: str | None = None
    budget_enforcement_mode: Literal["log_only", "enforce"] = "log_only"
    gemini_budget_task_usd_alert: Decimal = Field(default=Decimal("1.50"), ge=0)
    gemini_max_tool_turns_alert: int = Field(default=10, ge=1)
    gemini_budget_task_usd_cap: Decimal | None = Field(default=None, gt=0)
    gemini_max_tool_turns_cap: int | None = Field(default=None, ge=1)

    # Task engine: how many subtasks (of any task) the in-process worker pool runs side by side.
    task_worker_concurrency: int = Field(default=3, ge=1, le=8)

    # Tool credentials for the MCP gateway (all optional). The gateway injects them into the
    # tool server's own environment; they never reach model context, logs or audit rows.
    # A missing credential makes the dependent tool fail with a clear error, never fake success.
    github_token: SecretStr | None = None
    notion_token: SecretStr | None = None
    # The Notion database the project-manager agent reads tasks from (not a secret). Without it
    # the agent has nothing valid to query and reports how to set it.
    notion_database_id: str | None = Field(default=None, max_length=64)
    # Native web search (app/search): the providers to try, in order. ddgs (an explicit rotation
    # of free non-Google engines), searxng (only with MAYA_SEARXNG_URLS) and wikipedia need no
    # key; hn, arxiv and stackexchange answer only a query with a clear signal for them; brave and
    # tavily are skipped silently without their key. ``gemini`` (Google Search grounding) is NOT in
    # the default: name it here to opt in. An unknown name is refused at startup.
    search_providers: str = DEFAULT_PROVIDERS
    # Comma list of ddgs engines in rotation order (google is not allowed), and SearXNG
    # instances (comma list of base URLs, empty = no SearXNG).
    ddgs_engines: str = ",".join(DDGS_ENGINES)
    searxng_urls: str = ""
    # Provider calls at once in this process, and the minimum gap between two requests to one
    # engine (a little jitter is added): keeps bursts from getting an engine to block MAYA.
    search_concurrency: int = Field(default=2, ge=1, le=8)
    search_engine_spacing_ms: int = Field(default=400, ge=0, le=5000)
    brave_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    # Searches one user may run per minute across chat tools (a guard against loops).
    search_rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    # Backend of ordinary research steps (not deep research): ``native`` = MAYA's search + page
    # reading only; ``grounding`` = Gemini Google Search first; ``auto`` = native, then grounding
    # only when native found nothing. Grounding quota errors start a 30-minute shared cooldown.
    research_backend: Literal["native", "grounding", "auto"] = "native"
    # Deep research (app/research, ADR-0032): the composer's ``mode: "deep_research"``. All caps
    # are per run. ``deep_research_enabled`` is the kill switch; the run also refuses to start
    # when no search provider or no model client is available (fail closed, plain message).
    deep_research_enabled: bool = True
    deep_research_min_domains: int = Field(default=20, ge=1, le=60)
    deep_research_max_sources: int = Field(default=40, ge=1, le=60)
    deep_research_hard_max_sources: int = Field(default=60, ge=1, le=100)
    deep_research_time_cap_seconds: int = Field(default=600, ge=30, le=3600)
    deep_research_max_model_calls: int = Field(default=60, ge=4, le=400)
    deep_research_max_rounds: int = Field(default=2, ge=1, le=3)
    deep_research_fetch_concurrency: int = Field(default=6, ge=1, le=16)
    # Scheduled reports (app/schedules, ADR-0033). ``schedules_enabled`` is the kill switch for the
    # runner loop (the REST API and the settings stay available). Caps are per unattended run.
    schedules_enabled: bool = True
    schedules_poll_seconds: int = Field(default=30, ge=5, le=600)
    schedules_run_max_minutes: int = Field(default=15, ge=2, le=120)
    schedules_run_max_model_calls: int = Field(default=40, ge=4, le=400)
    # Gemini decision model (ADR-0029): advisory yes/no, pick-one and score judgements that can
    # only make MAYA safer or more accurate; every call fails open to the previous behaviour.
    decisions_enabled: bool = True
    decisions_model: str = Field(default="gemini-decision-1", min_length=1, max_length=100)
    decisions_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    decisions_background_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    # Comma list of purposes to switch off (route, search_need, grounding, task_verify,
    # doc_quality, approval_check, injection, mail_triage, voice_digest, memory, planner_hint).
    decisions_disabled_purposes: str = ""
    # Step review (task_verify): off = never ask, advisory = record and show a "Note" but never
    # change the task status (default), enforce = a strong, corroborated flag marks the task
    # "finished with problems".
    step_review_mode: Literal["off", "advisory", "enforce"] = "advisory"
    # Calls per minute across all purposes (the free tier allows 200).
    decisions_max_per_minute: int = Field(default=60, ge=1, le=180)
    # Master key for per-user connection credentials (Settings > Connections): 32 random bytes,
    # base64. Without it users cannot save a token (fail closed); the environment values above
    # keep working as the single-user default. Rotating it makes stored tokens unreadable.
    connections_encryption_key: SecretStr | None = None
    # Comma-separated account emails or user ids allowed to use the SERVER-LEVEL connections
    # (MAYA_GITHUB_TOKEN, MAYA_NOTION_*, MAYA_MAIL_*, MAYA_GOOGLE_ACCESS_TOKEN). Empty (default):
    # nobody, so one deployment owner's tokens never serve other users.
    env_connections_users: str = ""
    google_access_token: SecretStr | None = None
    google_client_id: str | None = Field(default=None, max_length=255)
    google_client_secret: SecretStr | None = None
    google_refresh_token: SecretStr | None = None
    artifacts_dir: Path = Path("storage/artifacts")
    # Uploaded files (ADR-0022): the size of one file and of one upload request. The contract caps
    # are 25 MB and 60 MB; a deployment may lower them (raising them needs a contract change).
    upload_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    upload_total_max_bytes: int = Field(default=60 * 1024 * 1024, ge=1024, le=60 * 1024 * 1024)

    # Chat: User-approved output limit (65536 tokens matching Gemini maximum output capacity),
    # extended reply deadline (600s), and context token budget (1M tokens).
    chat_rate_limit_per_minute: int = Field(default=20, ge=1, le=10_000)
    chat_context_token_budget: int = Field(default=1_000_000, ge=1_000, le=2_000_000)
    chat_max_concurrent_streams_per_user: int = Field(default=2, ge=1, le=100)
    chat_reply_deadline_seconds: int = Field(default=600, ge=1, le=1800)
    chat_max_output_tokens: int = Field(default=65_536, ge=1, le=128_000)
    # Deployment-level master switch for the optional per-message intent classifier (ADR-0016).
    # Off by default: no extra model call is made per message. When on, a user must also turn
    # on their own ``chat_intent_classifier`` preference before any message is classified.
    chat_intent_classifier: bool = False
    # Inline chat files and the deterministic router (services/chat_router.py, inline_files.py).
    # Kill switch: false restores the previous chat behaviour (submit_task only, no router).
    chat_inline_files: bool = True
    # Chat tool loop (ADR-0026, Phase 1): create_document, preview_document, start_job and
    # check_job for document, deck and website requests. Off by default: the previous behaviour.
    # Needs chat_inline_files (the router decides which requests the loop takes).
    chat_tool_loop: bool = False
    # Runtime tools (ADR-0027): the model may write a small Python tool, show it for approval and
    # run it ONLY inside an isolated container. Off by default; it also needs chat_tool_loop and
    # a sandbox image (runtime_tools_sandbox_image). Empty image = no isolated runtime = refuse.
    runtime_tools_enabled: bool = False
    runtime_tools_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    runtime_tools_max_runs_per_turn: int = Field(default=3, ge=1, le=10)
    runtime_tools_sandbox_image: str = Field(default="", max_length=200)
    # MCP client (ADR-0028): MAYA may use third-party MCP servers (Playwright, GitHub) started as
    # child processes on THIS machine. Off by default: while false nothing is imported or started
    # and no MCP tool is offered. Each server is also disabled until it is enabled and pinned to an
    # exact version in the servers file (mcp_servers_file); the file is optional.
    mcp_client_enabled: bool = False
    mcp_servers_file: str = Field(default="", max_length=500)
    mcp_call_timeout_seconds: float = Field(default=30.0, ge=1, le=120)
    mcp_start_timeout_seconds: float = Field(default=60.0, ge=5, le=300)
    mcp_max_calls_per_turn: int = Field(default=6, ge=1, le=20)
    mcp_max_output_chars: int = Field(default=20000, ge=1000, le=200000)
    mcp_max_tools_per_turn: int = Field(default=12, ge=1, le=30)
    # Image generation (services/image_gen.py): chat and live voice ask for an image through the
    # generate_image tool; the image itself comes from this model, not from the live or chat model.
    # The default id is the owner's choice and is not verified offline: a model the key cannot use
    # fails with a plain "model not found" message, and this setting is the fix.
    image_model: str = Field(default="gemini-3.1-flash-image", max_length=64)
    # Images one user may create per UTC day (0 turns image creation off).
    image_daily_limit: int = Field(default=20, ge=0, le=1000)
    # Cap for one image request to the image model.
    image_request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    # Start the headless Edge/Chrome used for PDFs in the background at startup, so the first
    # PDF does not pay the launch time. Off: the browser starts on the first PDF, as before.
    browser_pdf_warm_up: bool = True
    # Gemini thought summaries (short summaries of the model's reasoning, never raw thoughts). They
    # feed the "think" steps of the progress list. Thought tokens are billed at the output rate, so
    # this costs more per call and adds latency. false: no thinking config and no think step.
    thinking_summaries: bool = True

    @field_validator("image_model")
    @classmethod
    def validate_image_model(cls, value: str) -> str:
        cleaned = value.strip()
        if MODEL_ID_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("Invalid image model id")
        return cleaned

    @field_validator("jwt_signing_key", "otp_hmac_key")
    @classmethod
    def validate_secret_keys(cls, value: SecretStr) -> SecretStr:
        return _validate_secret_key(value)

    @field_validator("connections_encryption_key", mode="before")
    @classmethod
    def validate_connections_key(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        from app.connections.crypto import parse_key  # local: keeps config import light

        parse_key(str(raw))  # raises ValueError (no key material in the message)
        return raw

    @field_validator("allowed_hosts", "voice_allowed_origins", mode="before")
    @classmethod
    def parse_lists(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: list[str]) -> list[str]:
        if not value or any(not host or "/" in host for host in value):
            raise ValueError("Use a non-empty list of host names without paths")
        return value

    @field_validator("search_providers")
    @classmethod
    def validate_search_providers(cls, value: str) -> str:
        return ",".join(parse_provider_list(value))

    @field_validator("ddgs_engines")
    @classmethod
    def validate_ddgs_engines(cls, value: str) -> str:
        return ",".join(parse_engine_list(value))

    @field_validator("searxng_urls")
    @classmethod
    def validate_searxng_urls(cls, value: str) -> str:
        urls = [part.strip() for part in value.split(",") if part.strip()]
        for url in urls:
            if not url.startswith(("http://", "https://")):
                raise ValueError("MAYA_SEARXNG_URLS must be a comma list of http(s) base URLs")
        return ",".join(urls)

    @field_validator("brave_api_key", "tavily_api_key", mode="before")
    @classmethod
    def blank_search_key_is_unset(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if raw is None or (isinstance(raw, str) and not raw.strip()) else value

    @field_validator("voice_allowed_origins")
    @classmethod
    def validate_voice_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            parts = urlsplit(origin)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.path not in ("", "/")
            ):
                raise ValueError("Each voice origin must look like https://host[:port]")
        return [origin.rstrip("/") for origin in value]

    @field_validator("web_static_dir", mode="before")
    @classmethod
    def empty_static_dir_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("mail_address", "mail_app_password", "mail_enabled", mode="before")
    @classmethod
    def blank_mail_value_is_unset(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(raw, str) and not raw.strip() else value

    @field_validator("mail_address")
    @classmethod
    def validate_mail_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if SENDER_ADDRESS.fullmatch(cleaned) is None:
            raise ValueError("Invalid mailbox address")
        return cleaned

    @field_validator("mail_app_password")
    @classmethod
    def strip_app_password_spaces(cls, value: SecretStr | None) -> SecretStr | None:
        """Google shows an app password in groups of four: the spaces are not part of it."""
        if value is None:
            return None
        compact = "".join(value.get_secret_value().split())
        return SecretStr(compact) if compact else None

    @field_validator("smtp_from_address")
    @classmethod
    def validate_sender_address(cls, value: str | None) -> str | None:
        if value is not None and SENDER_ADDRESS.fullmatch(value) is None:
            raise ValueError("Invalid sender address")
        return value

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def validate_gemini_api_key(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = clean_api_key(value)
            return SecretStr(cleaned) if cleaned else None
        if isinstance(value, SecretStr):
            cleaned = clean_api_key(value.get_secret_value())
            return SecretStr(cleaned) if cleaned else None
        return value

    @field_validator("gemini_api_keys", mode="before")
    @classmethod
    def validate_gemini_api_keys(cls, value: Any) -> list[SecretStr]:
        if value is None:
            return []
        items: list[Any]
        if isinstance(value, str):
            items = _split_list(value)
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            items = [value]

        result: list[SecretStr] = []
        for item in items:
            s = item.get_secret_value() if isinstance(item, SecretStr) else str(item)
            s = clean_api_key(s)
            if not is_placeholder_key(s):
                result.append(SecretStr(s))
        return result

    @property
    def effective_gemini_api_keys(self) -> list[SecretStr]:
        """Effective Gemini API key pool.

        Combines gemini_api_key and gemini_api_keys, de-duplicated with placeholders dropped.
        """
        seen: set[str] = set()
        keys: list[SecretStr] = []

        if self.gemini_api_key is not None:
            raw = clean_api_key(self.gemini_api_key.get_secret_value())
            if raw and not is_placeholder_key(raw) and raw not in seen:
                seen.add(raw)
                keys.append(SecretStr(raw))

        for k in self.gemini_api_keys:
            raw = clean_api_key(k.get_secret_value())
            if raw and not is_placeholder_key(raw) and raw not in seen:
                seen.add(raw)
                keys.append(SecretStr(raw))

        return keys

    @field_validator("agent_model_alias")
    @classmethod
    def validate_agent_model_alias(cls, value: str) -> str:
        return validate_agent_alias(value.strip(), "MAYA_AGENT_MODEL_ALIAS")

    @field_validator("agent_fallback_alias")
    @classmethod
    def validate_agent_fallback_alias(cls, value: str) -> str:
        cleaned = value.strip()
        return validate_agent_alias(cleaned, "MAYA_AGENT_FALLBACK_ALIAS") if cleaned else ""

    @field_validator("agent_search_alias")
    @classmethod
    def validate_agent_search_alias(cls, value: str) -> str:
        return validate_agent_alias(value.strip(), "MAYA_AGENT_SEARCH_ALIAS", SEARCH_ALIAS_CHOICES)

    @field_validator("agent_search_fallback_alias")
    @classmethod
    def validate_agent_search_fallback_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return ""
        return validate_agent_alias(
            cleaned, "MAYA_AGENT_SEARCH_FALLBACK_ALIAS", SEARCH_ALIAS_CHOICES
        )

    @field_validator("gemini_model_overrides", mode="before")
    @classmethod
    def parse_model_overrides_before(cls, value: Any) -> Any:
        if value is None or value == "" or value == "{}":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s or s in ("{}", "None"):
                return {}
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            cleaned = s.strip("{}").strip()
            if cleaned in ("gemini-3.5-flash-lite", "gemini-4-hydrogen"):
                return {"orchestrator": cleaned}
            return {}
        return value

    @field_validator("gemini_model_overrides")
    @classmethod
    def validate_model_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for alias, model_id in value.items():
            if alias not in ALLOWED_OVERRIDE_ALIASES:
                raise ValueError(
                    f"Invalid model override alias '{alias}'. Only enabled REST aliases "
                    f"can be overridden: {sorted(ALLOWED_OVERRIDE_ALIASES)}"
                )
            if not MODEL_ID_PATTERN.fullmatch(model_id):
                raise ValueError(f"Invalid model_id '{model_id}' for alias '{alias}' in overrides")
        return value

    @model_validator(mode="after")
    def validate_authentication_settings(self) -> Self:
        if self.jwt_signing_key.get_secret_value() == self.otp_hmac_key.get_secret_value():
            raise ValueError("The JWT and OTP keys must be different")
        if self.refresh_absolute_ttl_seconds < self.refresh_token_ttl_seconds:
            raise ValueError("The absolute session lifetime must cover the refresh lifetime")
        if (self.smtp_host is None) != (self.smtp_from_address is None):
            raise ValueError("Configure the SMTP host and sender address together")
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("Configure the SMTP username and password together")
        if (self.mail_address is None) != (self.mail_app_password is None):
            raise ValueError("Configure the mailbox address and app password together")
        return self

    @model_validator(mode="after")
    def validate_budget_settings(self) -> Self:
        if self.budget_enforcement_mode == "enforce":
            if self.gemini_budget_task_usd_cap is None or self.gemini_max_tool_turns_cap is None:
                raise ValueError(
                    "Configure gemini_budget_task_usd_cap and gemini_max_tool_turns_cap "
                    "when budget_enforcement_mode='enforce'"
                )
        return self

    @property
    def email_configured(self) -> bool:
        return self.smtp_host is not None

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs and the OpenAPI schema: explicit setting, else development only."""
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return self.environment == "development"

    def fastapi_docs_kwargs(self) -> dict[str, str | None]:
        """Keyword arguments for ``FastAPI(...)`` that switch the documentation URLs on or off."""
        if self.docs_enabled:
            return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+asyncpg"
                and url.username not in (None, "", "postgres")
                and bool(url.password and url.host and url.database)
                and (url.port is None or 0 < url.port <= 65535)
            )
        except (ArgumentError, ValueError):
            raise ValueError("Invalid runtime database URL") from None
        if not valid:
            raise ValueError("Use an asyncpg URL with a non-admin application role")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = urlsplit(value.get_secret_value())
            valid = url.scheme in ("redis", "rediss") and bool(url.hostname)
            valid = valid and (url.port is None or 0 < url.port <= 65535)
            valid = valid and not url.query and not url.fragment
            valid = valid and (url.path in ("", "/") or url.path.removeprefix("/").isdigit())
        except ValueError:
            raise ValueError("Invalid Redis URL") from None
        if not valid:
            raise ValueError("Use a redis or rediss connection URL")
        return value

    @field_validator("migration_database_url")
    @classmethod
    def validate_migration_url(cls, value: SecretStr | None) -> SecretStr | None:
        return None if value is None else _validate_migration_url(value)

    @model_validator(mode="after")
    def separate_database_roles(self) -> Self:
        if self.migration_database_url is not None:
            runtime = make_url(self.database_url.get_secret_value())
            migration = make_url(self.migration_database_url.get_secret_value())
            if runtime.username == migration.username:
                raise ValueError("Runtime and migration database roles must differ")
        return self


class MigrationSettings(BaseSettings):
    """Only what the schema migration tool needs; the API never uses these credentials."""

    model_config = SettingsConfigDict(
        env_prefix="MAYA_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    migration_database_url: SecretStr

    @field_validator("migration_database_url")
    @classmethod
    def validate_migration_url(cls, value: SecretStr) -> SecretStr:
        return _validate_migration_url(value)
