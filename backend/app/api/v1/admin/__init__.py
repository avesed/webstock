"""Admin API endpoints package."""

from fastapi import APIRouter, Depends

from app.core.security import require_admin

from app.api.v1.admin.users import router as users_router
from app.api.v1.admin.settings import router as settings_router
from app.api.v1.admin.llm_costs import router as llm_costs_router
from app.api.v1.admin.knowledge_base import router as knowledge_base_router
from app.api.v1.admin.predictions import router as predictions_router
from app.api.v1.admin.integrations import router as integrations_router

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])

router.include_router(users_router)
router.include_router(settings_router)
router.include_router(llm_costs_router)
router.include_router(knowledge_base_router)
router.include_router(predictions_router)
router.include_router(integrations_router)
