from fastapi import APIRouter

from agent_api.api.chat import router as chat_router
from agent_api.api.health import router as health_router

router = APIRouter()
router.include_router(health_router)
router.include_router(chat_router)
