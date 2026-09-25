from fastapi import APIRouter

from agent_api.api.chat import router as chat_router
from agent_api.api.health import router as health_router
from agent_api.knowledge.api.routes import router as knowledge_router
from agent_api.knowledge.wiki.api import router as wiki_router

router = APIRouter()
router.include_router(health_router)
router.include_router(chat_router)
router.include_router(knowledge_router)
router.include_router(wiki_router)
