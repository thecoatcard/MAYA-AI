from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.auth.dependencies import client_address, get_auth_service, get_principal
from app.auth.errors import InvalidToken
from app.auth.models import Principal
from app.auth.schemas import (
    AcceptedResponse,
    CompleteSignupRequest,
    CookieTokenResponse,
    EmailRequest,
    IdentifyRequest,
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
from app.auth.service import AuthService, CompleteSignupCommand, RegisterCommand
from app.core.config import Settings
from app.core.email import OutboundEmail

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(get_auth_service)]
REFRESH_COOKIE = "maya_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_TRANSPORT_HEADER = "X-Maya-Refresh-Transport"


def _queue(
    background: BackgroundTasks, service: AuthService, message: OutboundEmail | None
) -> None:
    """E-mail goes out after the response, so timing does not reveal whether one was sent."""
    if message is not None:
        background.add_task(service.deliver, message)


@router.post("/identify", response_model=AcceptedResponse)
async def identify(body: IdentifyRequest) -> AcceptedResponse:
    """Validate the identifier's shape without checking whether the account exists."""
    return AcceptedResponse()


@router.post("/signup/otp", status_code=202, response_model=AcceptedResponse)
async def signup_otp(
    body: EmailRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: Service,
) -> AcceptedResponse:
    message = await service.send_signup_otp(body.email, client_address(request))
    _queue(background, service, message)
    settings: Settings = request.app.state.settings
    response.headers["Retry-After"] = str(settings.otp_resend_cooldown_seconds)
    return AcceptedResponse()


@router.post("/signup/complete", response_model=None)
async def signup_complete(
    body: CompleteSignupRequest,
    request: Request,
    response: Response,
    service: Service,
) -> TokenResponse | CookieTokenResponse:
    pair = await service.complete_signup(
        CompleteSignupCommand(
            email=body.email,
            code=body.code,
            username=body.username,
            password=body.password.get_secret_value(),
            full_name=body.full_name,
            workspace_name=body.workspace_name,
        ),
        client_address(request),
    )
    _no_store(response)
    if _uses_refresh_cookie(request):
        settings: Settings = request.app.state.settings
        _set_refresh_cookie(response, pair.refresh_token, settings)
        return CookieTokenResponse(
            access_token=pair.access_token,
            expires_in=pair.expires_in,
        )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


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
    body: EmailRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: Service,
) -> AcceptedResponse:
    message = await service.resend_verification(body.email, client_address(request))
    _queue(background, service, message)
    settings: Settings = request.app.state.settings
    response.headers["Retry-After"] = str(settings.otp_resend_cooldown_seconds)
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


def _uses_refresh_cookie(request: Request) -> bool:
    return request.headers.get(REFRESH_TRANSPORT_HEADER, "").strip().lower() == "cookie"


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _invalid_cookie_refresh(settings: Settings) -> JSONResponse:
    response = JSONResponse(
        status_code=401,
        content={"error": "invalid_token"},
        headers={
            "WWW-Authenticate": "Bearer",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )
    _clear_refresh_cookie(response, settings)
    return response


def _missing_legacy_body() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "fields": ["body"]},
    )


@router.post("/token", response_model=None)
async def token(
    body: LoginRequest, request: Request, response: Response, service: Service
) -> TokenResponse | CookieTokenResponse:
    pair = await service.login(
        body.username, body.password.get_secret_value(), client_address(request)
    )
    _no_store(response)
    if _uses_refresh_cookie(request):
        settings: Settings = request.app.state.settings
        _set_refresh_cookie(response, pair.refresh_token, settings)
        return CookieTokenResponse(
            access_token=pair.access_token,
            expires_in=pair.expires_in,
        )
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


@router.post("/refresh", response_model=None)
async def refresh(
    request: Request,
    response: Response,
    service: Service,
    body: RefreshRequest | None = None,
) -> TokenResponse | CookieTokenResponse | JSONResponse:
    cookie_transport = _uses_refresh_cookie(request)
    settings: Settings | None = request.app.state.settings if cookie_transport else None
    refresh_token = (
        request.cookies.get(REFRESH_COOKIE)
        if cookie_transport
        else None
        if body is None
        else body.refresh_token
    )
    if refresh_token is None:
        if settings is not None:
            return _invalid_cookie_refresh(settings)
        return _missing_legacy_body()
    try:
        pair = await service.refresh(refresh_token)
    except InvalidToken:
        if settings is not None:
            return _invalid_cookie_refresh(settings)
        raise
    _no_store(response)
    if settings is not None:
        _set_refresh_cookie(response, pair.refresh_token, settings)
        return CookieTokenResponse(
            access_token=pair.access_token,
            expires_in=pair.expires_in,
        )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=204, response_model=None)
async def logout(
    request: Request,
    service: Service,
    body: RefreshRequest | None = None,
) -> Response:
    if _uses_refresh_cookie(request):
        settings: Settings = request.app.state.settings
        refresh_token = request.cookies.get(REFRESH_COOKIE)
        if refresh_token is not None:
            try:
                await service.logout(refresh_token)
            except InvalidToken:
                pass
        response = Response(status_code=204)
        _clear_refresh_cookie(response, settings)
        return response
    if body is None:
        return _missing_legacy_body()
    await service.logout(body.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id, workspace_id=principal.workspace_id, role=principal.role
    )
