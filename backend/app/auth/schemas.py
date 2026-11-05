from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth.identifiers import normalize_display_text, normalize_email, normalize_username
from app.core.security import PASSWORD_INPUT_LIMIT

Password = Annotated[SecretStr, Field(min_length=1, max_length=PASSWORD_INPUT_LIMIT)]
Code = Annotated[str, Field(min_length=1, max_length=16)]
Token = Annotated[str, Field(min_length=1, max_length=4096)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_Request):
    username: str = Field(max_length=64)
    email: str = Field(max_length=320)
    password: Password
    full_name: str = Field(max_length=1024)
    workspace_name: str | None = Field(default=None, max_length=1024)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("full_name", "workspace_name")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return None if value is None else normalize_display_text(value)


class EmailRequest(_Request):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)


class VerifyEmailRequest(EmailRequest):
    code: Code
    password: Password


class LoginRequest(_Request):
    username: str = Field(max_length=64)
    password: Password

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return normalize_username(value)


class ResetPasswordRequest(EmailRequest):
    code: Code
    new_password: Password


class RefreshRequest(_Request):
    refresh_token: Token


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"


class VerifiedResponse(BaseModel):
    status: Literal["verified"] = "verified"


class PasswordResetResponse(BaseModel):
    status: Literal["password_reset"] = "password_reset"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user_id: UUID
    workspace_id: UUID
    role: str
