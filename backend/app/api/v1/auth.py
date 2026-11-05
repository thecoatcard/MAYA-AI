from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.auth.dependencies import client_address, get_auth_service, get_principal
from app.auth.models import Principal
from app.auth.schemas import (
    AcceptedResponse,
    EmailRequest,
    LoginRequest,
    MeResponse,
    PasswordResetResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifiedResponse,
    VerifyEmailRequest,
)
from app.auth.service import AuthService, RegisterCommand
from app.core.email import OutboundEmail

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(get_auth_service)]


def _queue(
    background: BackgroundTasks, service: AuthService, message: OutboundEmail | None
) -> None:
    """E-mail goes out after the response, so timing does not reveal whether one was sent."""
    if message is not None:
        background.add_task(service.deliver, message)


@router.post("/register", status_code=202, response_model=AcceptedResponse)
async def register(
    body: RegisterRequest, request: Request, background: BackgroundTasks, service: Service
) -> AcceptedResponse:
    message = await service.register(
        RegisterCommand(
            username=body.username,
            email=body.email,
            password=body.password.get_secret_value(),
            full_name=body.full_name,
            workspace_name=body.workspace_name,
        ),
        client_address(request),
    )
    _queue(background, service, message)
    return AcceptedResponse()


@router.post("/resend-verification", status_code=202, response_model=AcceptedResponse)
async def resend_verification(
    body: EmailRequest, request: Request, background: BackgroundTasks, service: Service
) -> AcceptedResponse:
    message = await service.resend_verification(body.email, client_address(request))
    _queue(background, service, message)
    return AcceptedResponse()


@router.post("/verify-email", response_model=VerifiedResponse)
async def verify_email(
    body: VerifyEmailRequest, request: Request, service: Service
) -> VerifiedResponse:
    await service.verify_email(
        body.email, body.code, body.password.get_secret_value(), client_address(request)
    )
    return VerifiedResponse()


def _no_store(response: Response) -> None:
    """Credentials in a response must never be kept by a browser or proxy cache."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/token", response_model=TokenResponse)
async def token(
    body: LoginRequest, request: Request, response: Response, service: Service
) -> TokenResponse:
    pair = await service.login(
        body.username, body.password.get_secret_value(), client_address(request)
    )
    _no_store(response)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/forgot-password", status_code=202, response_model=AcceptedResponse)
async def forgot_password(
    body: EmailRequest, request: Request, background: BackgroundTasks, service: Service
) -> AcceptedResponse:
    message = await service.request_password_reset(body.email, client_address(request))
    _queue(background, service, message)
    return AcceptedResponse()


@router.post("/reset-password", response_model=PasswordResetResponse)
async def reset_password(
    body: ResetPasswordRequest, request: Request, service: Service
) -> PasswordResetResponse:
    await service.reset_password(
        body.email, body.code, body.new_password.get_secret_value(), client_address(request)
    )
    return PasswordResetResponse()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, response: Response, service: Service) -> TokenResponse:
    pair = await service.refresh(body.refresh_token)
    _no_store(response)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, service: Service) -> Response:
    await service.logout(body.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id, workspace_id=principal.workspace_id, role=principal.role
    )
