from fastapi import APIRouter

from app.api.v1.approvals import router as approvals_router
from app.api.v1.artifacts import router as artifacts_router
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import router as auth_router
from app.api.v1.browser import router as browser_router
from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.api.v1.mcp_admin import router as mcp_admin_router
from app.api.v1.memory import router as memory_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.voice import router as voice_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(chat_router)
api_router.include_router(voice_router)
api_router.include_router(tasks_router)
api_router.include_router(approvals_router)
api_router.include_router(mcp_admin_router)
api_router.include_router(artifacts_router)
api_router.include_router(audit_router)
api_router.include_router(memory_router)
api_router.include_router(browser_router)
